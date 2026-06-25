"""Events listener."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from http import HTTPStatus
import ipaddress
import logging
from pathlib import Path
import socket
from urllib.parse import urlparse

from aiohttp import web
from requests_toolbelt.multipart import MultipartDecoder

from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONTENT_TYPE_TEXT_PLAIN, STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_registry import async_get
from homeassistant.util import slugify

from .const import (
    ALARM_SERVER_PATH,
    ATTR_LAST_EVENT_RECEIVED_AT,
    ATTR_LAST_IMAGE_CONTENT_TYPE,
    ATTR_LAST_IMAGE_PATH,
    ATTR_LAST_IMAGE_SIZE,
    ATTR_LAST_IMAGE_URL,
    DOMAIN,
    HIKVISION_EVENT,
    HIKVISION_EVENT_IMAGE_UPDATED,
)
from .hikvision_device import HikvisionDevice
from .isapi import AlertInfo, IPCamera, ISAPIClient
from .isapi.const import EVENT_IO

_LOGGER = logging.getLogger(__name__)

CONTENT_TYPE = "Content-Type"
CONTENT_TYPE_XML = (
    "application/xml",
    'application/xml; charset="UTF-8"',
    "text/xml",
)
CONTENT_TYPE_TEXT_HTML = "text/html"
CONTENT_TYPE_IMAGE = "image/jpeg"
CONTENT_TYPE_IMAGE_PREFIX = "image/"


@dataclass
class EventImage:
    """Image attached to or fetched for an event."""

    content: bytes
    content_type: str
    extension: str


@dataclass
class EventRequestContent:
    """Parsed event notification request content."""

    xml: str
    image: EventImage | None = None


@dataclass
class StoredEventImage:
    """Latest event image storage info."""

    path: str
    url: str
    content_type: str
    size: int


class EventNotificationsView(HomeAssistantView):
    """Event notifications listener."""

    def __init__(self, hass: HomeAssistant):
        """Initialize."""
        self.requires_auth = False
        self.url = ALARM_SERVER_PATH
        self.name = DOMAIN
        self.device: HikvisionDevice
        self.hass = hass

    async def post(self, request: web.Request):
        """Accept the POST request from NVR or IP Camera."""

        try:
            _LOGGER.debug("--- Incoming event notification ---")
            _LOGGER.debug("Source: %s", request.remote)
            event_request = await self.parse_event_request(request)
            _LOGGER.debug("alert info: %s", event_request.xml)
            alert = ISAPIClient.parse_event_notification(event_request.xml)
            device = self.get_isapi_device(request.remote, alert)
            self.device = device
            self.update_alert_channel(alert, device)
            stored_image = await self.store_event_image(device, alert, event_request.image) if event_request.image else None
            self.trigger_sensor(device, alert, stored_image)
            if not event_request.image:
                self.schedule_event_snapshot(device, alert)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process incoming event %s", ex)

        response = web.Response(status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN)
        return response

    def get_isapi_device(self, device_ip, alert: AlertInfo) -> HikvisionDevice:
        """Get integration instance for device sending alert."""
        integration_entries = self.hass.config_entries.async_entries(DOMAIN)
        instance_identifiers = []
        entry = None
        if len(integration_entries) == 1:
            entry = integration_entries[0]
        else:
            # Search device by mac_address
            for item in integration_entries:
                if item.disabled_by:
                    continue

                item_mac_address = item.runtime_data.device_info.mac_address
                instance_identifiers.append(item_mac_address)

                if item_mac_address == alert.mac:
                    entry = item
                    break

            # Search device by ip_address
            if not entry:
                for item in integration_entries:
                    if item.disabled_by:
                        continue

                    url = item.runtime_data.host
                    instance_identifiers.append(url)

                    if self.get_ip(urlparse(url).hostname) == device_ip:
                        entry = item
                        break

        if not entry:
            raise ValueError(f"Cannot find ISAPI instance for device {device_ip} in {instance_identifiers}")

        return entry.runtime_data

    def get_ip(self, ip_string: str) -> str:
        """Return an IP if either hostname or IP is provided."""

        try:
            ipaddress.ip_address(ip_string)
            return ip_string
        except ValueError:
            resolved_hostname = socket.gethostbyname(ip_string)
            _LOGGER.debug("Resolve host %s resolves to IP %s", ip_string, resolved_hostname)

            return resolved_hostname

    async def parse_event_request(self, request: web.Request) -> EventRequestContent:
        """Extract XML content from multipart request or from simple request."""

        data = await request.read()

        content_type_header = request.headers.get(CONTENT_TYPE).strip()

        _LOGGER.debug("request headers: %s", request.headers)
        xml = None
        image = None
        if content_type_header in CONTENT_TYPE_XML:
            xml = data.decode("utf-8")
        else:
            # "multipart/form-data; boundary=boundary"
            decoder = MultipartDecoder(data, content_type_header)
            for part in decoder.parts:
                headers = {}
                for key, value in part.headers.items():
                    assert isinstance(key, bytes)
                    headers[key.decode("ascii")] = value.decode("ascii")
                _LOGGER.debug("part headers: %s", headers)
                if headers.get(CONTENT_TYPE) in CONTENT_TYPE_XML:
                    xml = part.text
                part_content_type = headers.get(CONTENT_TYPE, "")
                if part_content_type.lower().startswith(CONTENT_TYPE_IMAGE_PREFIX):
                    _LOGGER.debug("image found")
                    image = EventImage(
                        content=part.content,
                        content_type=part_content_type,
                        extension=self.image_extension(part_content_type),
                    )

        if not xml:
            raise ValueError(f"Unexpected event Content-Type {content_type_header}")
        return EventRequestContent(xml=xml, image=image)

    def schedule_event_snapshot(self, device: HikvisionDevice, alert: AlertInfo) -> None:
        """Schedule fallback snapshot capture without delaying the event response."""

        self.hass.async_create_task(self.process_event_snapshot(device, replace(alert)))

    async def process_event_snapshot(self, device: HikvisionDevice, alert: AlertInfo) -> None:
        """Fetch and store a fallback event snapshot in the background."""

        event_image = await self.fetch_event_snapshot(device, alert)
        if not event_image:
            return

        stored_image = await self.store_event_image(device, alert, event_image)
        self.update_sensor_image(device, alert, stored_image)

    async def fetch_event_snapshot(self, device: HikvisionDevice, alert: AlertInfo) -> EventImage | None:
        """Fetch a camera snapshot when the event payload has no attached image."""

        if alert.channel_id == 0 or alert.event_id == EVENT_IO:
            return None

        camera = device.get_camera_by_id(alert.channel_id)
        if not camera:
            return None

        stream = next((item for item in camera.streams if item.type_id == 1), None)
        if not stream:
            return None

        try:
            image = await device.get_camera_image(stream)
        except Exception as ex:  # pylint: disable=broad-except
            device.handle_exception(ex, f"Cannot fetch event snapshot for {alert.event_id}")
            return None

        if not image:
            return None

        return EventImage(content=image, content_type=CONTENT_TYPE_IMAGE, extension="jpeg")

    async def store_event_image(
        self,
        device: HikvisionDevice,
        alert: AlertInfo,
        image: EventImage,
    ) -> StoredEventImage:
        """Store latest image for event."""

        def write_image() -> StoredEventImage:
            channel_id = alert.channel_id or 0
            relative_path = Path(
                DOMAIN,
                device.entry.entry_id,
                f"channel_{channel_id}",
                f"{alert.event_id}.{image.extension}",
            )
            path = Path(self.hass.config.path("www")) / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = path.with_suffix(f".tmp.{image.extension}")
            temporary_path.write_bytes(image.content)
            temporary_path.replace(path)
            return StoredEventImage(
                path=str(path),
                url=f"/local/{relative_path.as_posix()}",
                content_type=image.content_type,
                size=len(image.content),
            )

        return await self.hass.async_add_executor_job(write_image)

    @staticmethod
    def image_extension(content_type: str) -> str:
        """Return a file extension for image content type."""

        content_type = content_type.lower().split(";", 1)[0].strip()
        extension = content_type.removeprefix(CONTENT_TYPE_IMAGE_PREFIX).split("+", 1)[0]
        if extension == "jpg":
            return "jpeg"
        return slugify(extension or "jpeg")

    def update_alert_channel(self, alert: AlertInfo, device: HikvisionDevice | None = None) -> AlertInfo:
        """Fix channel id for NVR/DVR alert."""

        device = device or self.device
        if alert.channel_id > 32:
            # channel id above 32 is an IP camera
            # On DVRs that support analog cameras 33 may not be
            # camera 1 but camera 5 for example
            try:
                alert.channel_id = [
                    camera.id
                    for camera in device.cameras
                    if isinstance(camera, IPCamera) and camera.input_port == alert.channel_id - 32
                ][0]
            except IndexError:
                alert.channel_id = alert.channel_id - 32

    def trigger_sensor(
        self,
        device: HikvisionDevice,
        alert: AlertInfo,
        stored_image: StoredEventImage | None = None,
    ) -> None:
        """Determine entity and set binary sensor state."""

        _LOGGER.debug("Alert: %s", alert)

        serial_no = device.device_info.serial_no.lower()

        device_id_param = f"_{alert.channel_id}" if alert.channel_id != 0 and alert.event_id != EVENT_IO else ""
        io_port_id_param = f"_{alert.io_port_id}" if alert.io_port_id != 0 else ""
        unique_id = f"binary_sensor.{slugify(serial_no)}{device_id_param}{io_port_id_param}_{alert.event_id}"

        _LOGGER.debug("UNIQUE_ID: %s", unique_id)

        entity_registry = async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, unique_id)
        if entity_id:
            entity = self.hass.states.get(entity_id)
            if entity:
                attributes = dict(entity.attributes)
                attributes[ATTR_LAST_EVENT_RECEIVED_AT] = datetime.now().isoformat()
                if stored_image:
                    attributes[ATTR_LAST_IMAGE_PATH] = stored_image.path
                    attributes[ATTR_LAST_IMAGE_URL] = stored_image.url
                    attributes[ATTR_LAST_IMAGE_CONTENT_TYPE] = stored_image.content_type
                    attributes[ATTR_LAST_IMAGE_SIZE] = stored_image.size
                if alert.detection_target:
                    attributes["detection_target"] = alert.detection_target
                    attributes["region_id"] = alert.region_id

                self.hass.states.async_set(entity_id, STATE_ON, attributes)
                self.fire_hass_event(device, alert, stored_image)
                if stored_image:
                    self.fire_image_updated_event(device, alert, stored_image)
            return
        raise ValueError(f"Entity not found {entity_id}")

    def update_sensor_image(
        self,
        device: HikvisionDevice,
        alert: AlertInfo,
        stored_image: StoredEventImage,
    ) -> None:
        """Update sensor image attributes after a fallback snapshot is saved."""

        serial_no = device.device_info.serial_no.lower()
        device_id_param = f"_{alert.channel_id}" if alert.channel_id != 0 and alert.event_id != EVENT_IO else ""
        io_port_id_param = f"_{alert.io_port_id}" if alert.io_port_id != 0 else ""
        unique_id = f"binary_sensor.{slugify(serial_no)}{device_id_param}{io_port_id_param}_{alert.event_id}"

        entity_registry = async_get(self.hass)
        entity_id = entity_registry.async_get_entity_id(Platform.BINARY_SENSOR, DOMAIN, unique_id)
        if not entity_id or not (entity := self.hass.states.get(entity_id)):
            return

        attributes = dict(entity.attributes)
        attributes[ATTR_LAST_IMAGE_PATH] = stored_image.path
        attributes[ATTR_LAST_IMAGE_URL] = stored_image.url
        attributes[ATTR_LAST_IMAGE_CONTENT_TYPE] = stored_image.content_type
        attributes[ATTR_LAST_IMAGE_SIZE] = stored_image.size
        self.hass.states.async_set(entity_id, entity.state, attributes)
        self.fire_image_updated_event(device, alert, stored_image)

    def fire_hass_event(
        self,
        device: HikvisionDevice,
        alert: AlertInfo,
        stored_image: StoredEventImage | None = None,
    ):
        """Fire HASS event."""
        camera_name = ""
        if camera := device.get_camera_by_id(alert.channel_id):
            camera_name = camera.name

        message = {
            "channel_id": alert.channel_id,
            "io_port_id": alert.io_port_id,
            "camera_name": camera_name,
            "event_id": alert.event_id,
        }
        if alert.detection_target:
            message["detection_target"] = alert.detection_target
            message["region_id"] = alert.region_id
        if stored_image:
            message["last_image_path"] = stored_image.path
            message["last_image_url"] = stored_image.url

        self.hass.bus.fire(
            HIKVISION_EVENT,
            message,
        )

    def fire_image_updated_event(
        self,
        device: HikvisionDevice,
        alert: AlertInfo,
        stored_image: StoredEventImage,
    ) -> None:
        """Fire image entity update event."""

        self.hass.bus.async_fire(
            HIKVISION_EVENT_IMAGE_UPDATED,
            {
                "unique_id": self.event_image_unique_id(device, alert),
                "path": stored_image.path,
                "url": stored_image.url,
                "content_type": stored_image.content_type,
                "size": stored_image.size,
            },
        )

    def event_image_unique_id(self, device: HikvisionDevice, alert: AlertInfo) -> str:
        """Return unique ID for the event image entity."""

        serial_no = device.device_info.serial_no.lower()
        return slugify(f"{serial_no}_{alert.channel_id}_{alert.event_id}_last_image")
