"""Image entities with camera snapshots."""

from datetime import datetime
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.camera import Camera
from homeassistant.components.image import ImageEntity
from homeassistant.const import ATTR_ENTITY_ID, CONF_FILENAME
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template
from homeassistant.util import slugify

from . import HikvisionConfigEntry
from .const import DOMAIN, ACTION_UPDATE_SNAPSHOT, HIKVISION_EVENT_IMAGE_UPDATED
from .hikvision_device import HikvisionDevice
from .isapi import AnalogCamera, CameraStreamInfo, EventInfo, IPCamera

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: HikvisionConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add images with snapshots."""

    device = entry.runtime_data

    entities = []
    for camera in device.cameras:
        for stream in camera.streams:
            if stream.type_id == 1:
                entities.append(SnapshotFile(hass, device, camera, stream))
        for event in camera.events_info:
            entities.append(EventImage(hass, device, camera, event))

    async_add_entities(entities)

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        ACTION_UPDATE_SNAPSHOT,
        {vol.Required(CONF_FILENAME): cv.template},
        "update_snapshot_filename",
    )


class SnapshotFile(ImageEntity):
    """An entity for displaying snapshot files."""

    _attr_has_entity_name = True
    file_path = None

    def __init__(
        self,
        hass: HomeAssistant,
        device: HikvisionDevice,
        camera: Camera,
        stream_info: CameraStreamInfo,
    ) -> None:
        """Initialize the snapshot file."""

        ImageEntity.__init__(self, hass)

        self._attr_unique_id = slugify(f"{device.device_info.serial_no.lower()}_{stream_info.id}_snapshot")
        self.entity_id = f"camera.{self.unique_id}"
        self._attr_translation_key = "snapshot"
        self._attr_translation_placeholders = {"camera": camera.name}

    def image(self) -> bytes | None:
        """Return bytes of image."""
        try:
            if self.file_path:
                with open(self.file_path, "rb") as file:
                    return file.read()
        except FileNotFoundError:
            _LOGGER.warning(
                "Could not read camera %s image from file: %s",
                self.name,
                self.file_path,
            )
        return None

    async def update_snapshot_filename(
        self,
        filename: Template,
    ) -> None:
        """Update the file_path."""
        self.file_path = filename.async_render(variables={ATTR_ENTITY_ID: self.entity_id})
        self._attr_image_last_updated = datetime.now()
        self.schedule_update_ha_state()


class EventImage(ImageEntity):
    """An entity for displaying the last event image."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        device: HikvisionDevice,
        camera: AnalogCamera | IPCamera,
        event: EventInfo,
    ) -> None:
        """Initialize the event image."""

        ImageEntity.__init__(self, hass)

        self.device = device
        self.camera = camera
        self.event = event
        self._attr_device_info = device.hass_device_info(camera.id)
        self._attr_unique_id = slugify(f"{device.device_info.serial_no.lower()}_{camera.id}_{event.id}_last_image")
        self.entity_id = f"image.{self.unique_id}"
        self._attr_translation_key = "event_image"
        self._attr_translation_placeholders = {
            "camera": camera.name,
            "event": event.id,
        }
        self._attr_entity_registry_enabled_default = not event.disabled

    async def async_added_to_hass(self) -> None:
        """Listen for event image updates."""

        self.async_on_remove(
            self.hass.bus.async_listen(
                HIKVISION_EVENT_IMAGE_UPDATED,
                self._handle_event_image_updated,
            )
        )

    @callback
    def _handle_event_image_updated(self, event: Event) -> None:
        """Handle event image update signal."""

        if event.data.get("unique_id") != self.unique_id:
            return
        self._attr_image_last_updated = datetime.now()
        self.schedule_update_ha_state()

    @property
    def file_path(self) -> Path:
        """Return latest image path."""

        base_path = Path(
            self.hass.config.path(
                "www",
                DOMAIN,
                self.device.entry.entry_id,
                f"channel_{self.camera.id}",
            )
        )
        for extension in ("jpeg", "jpg", "png", "webp", "gif"):
            path = base_path / f"{self.event.id}.{extension}"
            if path.exists():
                return path
        return base_path / f"{self.event.id}.jpeg"

    def image(self) -> bytes | None:
        """Return bytes of image."""

        try:
            path = self.file_path
            self._attr_image_last_updated = datetime.fromtimestamp(path.stat().st_mtime)
            return path.read_bytes()
        except FileNotFoundError:
            return None
