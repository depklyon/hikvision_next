"""Events listener."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
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
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.entity_registry import async_get
from homeassistant.util import dt as dt_util, slugify

from .const import (
    ALARM_SERVER_PATH,
    ATTR_DETECTION_TARGETS,
    ATTR_IMAGE_HISTORY,
    ATTR_LAST_EVENT_RECEIVED_AT,
    ATTR_LAST_IMAGE_CONTENT_TYPE,
    ATTR_LAST_IMAGE_PATH,
    ATTR_LAST_IMAGE_SIZE,
    ATTR_LAST_IMAGE_URL,
    ATTR_TARGET,
    CONF_IMAGE_CAPTURE_MOVEMENT,
    CONF_IMAGE_RETENTION,
    DEFAULT_IMAGE_CAPTURE_MOVEMENT,
    DEFAULT_IMAGE_RETENTION_DAYS,
    DOMAIN,
    HIKVISION_EVENT,
    HIKVISION_EVENT_IMAGE_UPDATED,
    HIKVISION_SIGNAL_EVENT,
    TARGET_MOVEMENT,
)
from .helpers import get_camera_media_dir, is_global_settings_entry
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
    images: list[EventImage] = field(default_factory=list)

    @property
    def image(self) -> EventImage | None:
        """Return the primary/latest image for backward compatibility."""
        return self.images[0] if self.images else None


@dataclass
class StoredEventImage:
    """Latest event image storage info."""

    path: str
    url: str
    content_type: str
    size: int
    all_paths: list[str] = field(default_factory=list)
    all_urls: list[str] = field(default_factory=list)


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
            should_capture = self.should_capture_images(device, alert)
            stored_image = (
                await self.store_event_images(device, alert, event_request.images)
                if event_request.images and should_capture
                else None
            )
            self.trigger_sensor(device, alert, stored_image)
            if not event_request.images and alert.event_state == "active" and should_capture:
                self.schedule_event_snapshot(device, alert)
        except Exception as ex:  # pylint: disable=broad-except
            _LOGGER.warning("Cannot process incoming event %s", ex)

        response = web.Response(status=HTTPStatus.OK, content_type=CONTENT_TYPE_TEXT_PLAIN)
        return response

    def should_capture_images(self, device: HikvisionDevice, alert: AlertInfo) -> bool:
        """Check if images should be captured/stored for this event."""
        if alert.event_id == "motiondetection":
            is_targeted = bool(alert.detection_targets or alert.detection_target)
            if not is_targeted:
                capture_movement = (
                    device.entry.options.get(
                        CONF_IMAGE_CAPTURE_MOVEMENT, DEFAULT_IMAGE_CAPTURE_MOVEMENT
                    )
                    if device.entry
                    else DEFAULT_IMAGE_CAPTURE_MOVEMENT
                )
                return bool(capture_movement)
        return True

    def get_isapi_device(self, device_ip, alert: AlertInfo) -> HikvisionDevice:
        """Get integration instance for device sending alert."""
        integration_entries = [
            item
            for item in self.hass.config_entries.async_entries(DOMAIN)
            if not is_global_settings_entry(item)
            and not item.disabled_by
            and getattr(item, "runtime_data", None) is not None
        ]
        instance_identifiers = []
        entry = None
        if len(integration_entries) == 1:
            entry = integration_entries[0]
        else:
            # Search device by mac_address
            for item in integration_entries:
                item_mac_address = item.runtime_data.device_info.mac_address
                instance_identifiers.append(item_mac_address)

                if item_mac_address == alert.mac:
                    entry = item
                    break

            # Search device by ip_address
            if not entry:
                for item in integration_entries:
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

        content_type_header = request.headers.get(CONTENT_TYPE, "").strip()

        _LOGGER.debug("request headers: %s", request.headers)
        xml = None
        images: list[EventImage] = []
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
                    images.append(
                        EventImage(
                            content=part.content,
                            content_type=part_content_type,
                            extension=self.image_extension(part_content_type),
                        )
                    )

        if not xml:
            raise ValueError(f"Unexpected event Content-Type {content_type_header}")
        return EventRequestContent(xml=xml, images=images)

    def schedule_event_snapshot(self, device: HikvisionDevice, alert: AlertInfo) -> None:
        """Schedule fallback snapshot capture without delaying the event response."""

        self.hass.async_create_task(self.process_event_snapshot(device, replace(alert)))

    async def process_event_snapshot(self, device: HikvisionDevice, alert: AlertInfo) -> None:
        """Fetch and store a fallback event snapshot in the background."""

        event_image = await self.fetch_event_snapshot(device, alert)
        if not event_image:
            return

        stored_image = await self.store_event_images(device, alert, [event_image])
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
        """Backward-compatible single image storage."""
        return await self.store_event_images(device, alert, [image])

    async def store_event_images(
        self,
        device: HikvisionDevice,
        alert: AlertInfo,
        images: list[EventImage],
    ) -> StoredEventImage:
        """Store images for event with retention in days and update latest files."""

        def write_images() -> StoredEventImage:
            channel_id = alert.channel_id or 0
            retention_days = int(
                device.entry.options.get(CONF_IMAGE_RETENTION, DEFAULT_IMAGE_RETENTION_DAYS)
            )

            # Store in isolated camera media folder
            media_dir = get_camera_media_dir(self.hass, device, channel_id)
            media_dir.mkdir(parents=True, exist_ok=True)

            # Backward-compatible www folder
            www_relative_dir = Path(DOMAIN, device.entry.entry_id, f"channel_{channel_id}")
            www_dir = Path(self.hass.config.path("www")) / www_relative_dir
            www_dir.mkdir(parents=True, exist_ok=True)

            now = dt_util.utcnow()
            timestamp_str = now.strftime("%Y%m%d_%H%M%S_%f")
            target_label = alert.detection_target or (alert.detection_targets[0] if alert.detection_targets else None)
            target_suffix = f"_{target_label}" if target_label else ""

            saved_paths: list[str] = []
            saved_urls: list[str] = []
            primary_path: str = ""
            primary_url: str = ""
            primary_content_type = images[0].content_type
            primary_size = len(images[0].content)

            for idx, img in enumerate(images):
                suffix = f"_{idx + 1}" if len(images) > 1 else ""
                filename = f"{alert.event_id}{target_suffix}_{timestamp_str}{suffix}.{img.extension}"

                media_path = media_dir / filename
                www_path = www_dir / filename

                # Write to media
                tmp_media = media_path.with_suffix(f".tmp.{img.extension}")
                tmp_media.write_bytes(img.content)
                tmp_media.replace(media_path)

                # Write to www for backward compatibility
                tmp_www = www_path.with_suffix(f".tmp.{img.extension}")
                tmp_www.write_bytes(img.content)
                tmp_www.replace(www_path)

                # Update fixed latest files
                if idx == 0:
                    latest_media = media_dir / f"{alert.event_id}_latest.{img.extension}"
                    tmp_latest = latest_media.with_suffix(f".tmp.{img.extension}")
                    tmp_latest.write_bytes(img.content)
                    tmp_latest.replace(latest_media)

                    if target_label:
                        target_latest_media = media_dir / f"{alert.event_id}_{target_label}_latest.{img.extension}"
                        tmp_target = target_latest_media.with_suffix(f".tmp.{img.extension}")
                        tmp_target.write_bytes(img.content)
                        tmp_target.replace(target_latest_media)

                    # Update legacy {event}.jpeg in www
                    legacy_www = www_dir / f"{alert.event_id}.{img.extension}"
                    tmp_legacy = legacy_www.with_suffix(f".tmp.{img.extension}")
                    tmp_legacy.write_bytes(img.content)
                    tmp_legacy.replace(legacy_www)

                    if target_label:
                        legacy_target_www = www_dir / f"{alert.event_id}_{target_label}.{img.extension}"
                        tmp_legacy_target = legacy_target_www.with_suffix(f".tmp.{img.extension}")
                        tmp_legacy_target.write_bytes(img.content)
                        tmp_legacy_target.replace(legacy_target_www)

                    primary_path = str(media_path)
                    primary_url = f"/local/{www_relative_dir.as_posix()}/{alert.event_id}.{img.extension}"

                saved_paths.append(str(media_path))
                saved_urls.append(f"/local/{www_relative_dir.as_posix()}/{filename}")

            # Cleanup older images based on retention_days
            cutoff_timestamp = (
                (now - timedelta(days=retention_days)).timestamp() if retention_days > 0 else now.timestamp()
            )

            for target_dir in (media_dir, www_dir):
                pattern = f"{alert.event_id}_*.*"
                for p in target_dir.glob(pattern):
                    if p.name.endswith(".tmp") or "_latest." in p.name:
                        continue
                    try:
                        if retention_days == 0 or p.stat().st_mtime < cutoff_timestamp:
                            # Keep only the ones just saved if retention is 0
                            if str(p) not in saved_paths and str(p) not in [str(www_dir / Path(sp).name) for sp in saved_paths]:
                                p.unlink(missing_ok=True)
                    except OSError:
                        pass

            return StoredEventImage(
                path=primary_path,
                url=primary_url,
                content_type=primary_content_type,
                size=primary_size,
                all_paths=saved_paths,
                all_urls=saved_urls,
            )

        return await self.hass.async_add_executor_job(write_images)

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

        is_active = alert.event_state != "inactive"

        attributes = {
            ATTR_LAST_EVENT_RECEIVED_AT: dt_util.utcnow().isoformat(),
        }
        if stored_image:
            attributes[ATTR_LAST_IMAGE_PATH] = stored_image.path
            attributes[ATTR_LAST_IMAGE_URL] = stored_image.url
            attributes[ATTR_LAST_IMAGE_CONTENT_TYPE] = stored_image.content_type
            attributes[ATTR_LAST_IMAGE_SIZE] = stored_image.size
            attributes[ATTR_IMAGE_HISTORY] = stored_image.all_urls

        if alert.detection_targets:
            attributes[ATTR_DETECTION_TARGETS] = alert.detection_targets
        if alert.detection_target:
            attributes["detection_target"] = alert.detection_target
            attributes["region_id"] = alert.region_id

        # Dispatch state signal to primary sensor entity
        async_dispatcher_send(
            self.hass,
            f"{HIKVISION_SIGNAL_EVENT}_{unique_id}",
            is_active,
            attributes,
        )

        # Trigger target-specific sensors (human / vehicle / movement)
        is_targeted = bool(alert.detection_targets or alert.detection_target)
        target_name = (
            alert.detection_target
            or (alert.detection_targets[0] if alert.detection_targets else None)
            or (TARGET_MOVEMENT if not is_targeted else None)
        )
        if target_name:
            attributes[ATTR_TARGET] = target_name

        for target in ("human", "vehicle", "movement"):
            target_unique_id = f"{unique_id}_{target}"
            if target == "movement":
                target_active = is_active and not is_targeted
            else:
                target_active = is_active and (
                    target in alert.detection_targets
                    or (alert.detection_target and target == alert.detection_target)
                )
            async_dispatcher_send(
                self.hass,
                f"{HIKVISION_SIGNAL_EVENT}_{target_unique_id}",
                target_active,
                attributes,
            )

        # Fire Home Assistant bus event
        self.fire_hass_event(device, alert, stored_image)
        if stored_image:
            self.fire_image_updated_event(device, alert, stored_image)

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

        attributes = {
            ATTR_LAST_IMAGE_PATH: stored_image.path,
            ATTR_LAST_IMAGE_URL: stored_image.url,
            ATTR_LAST_IMAGE_CONTENT_TYPE: stored_image.content_type,
            ATTR_LAST_IMAGE_SIZE: stored_image.size,
            ATTR_IMAGE_HISTORY: stored_image.all_urls,
        }

        async_dispatcher_send(
            self.hass,
            f"{HIKVISION_SIGNAL_EVENT}_{unique_id}",
            True,
            attributes,
        )
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
            "event_state": alert.event_state,
        }
        if alert.detection_targets:
            message[ATTR_DETECTION_TARGETS] = alert.detection_targets
        if alert.detection_target:
            message["detection_target"] = alert.detection_target
            message["region_id"] = alert.region_id
        if stored_image:
            message["last_image_path"] = stored_image.path
            message["last_image_url"] = stored_image.url
            message["image_history"] = stored_image.all_urls

        self.hass.bus.async_fire(
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
        for target in alert.detection_targets or ([alert.detection_target] if alert.detection_target else []):
            self.hass.bus.async_fire(
                HIKVISION_EVENT_IMAGE_UPDATED,
                {
                    "unique_id": self.event_image_unique_id(device, alert, target),
                    "path": stored_image.path,
                    "url": stored_image.url,
                    "content_type": stored_image.content_type,
                    "size": stored_image.size,
                },
            )

    def event_image_unique_id(
        self, device: HikvisionDevice, alert: AlertInfo, target: str | None = None
    ) -> str:
        """Return unique ID for the event image entity."""

        serial_no = device.device_info.serial_no.lower()
        target_param = f"_{target}" if target else ""
        return slugify(f"{serial_no}_{alert.channel_id}_{alert.event_id}{target_param}_last_image")
