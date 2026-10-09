"""Image entities with camera snapshots."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.components.image import ImageEntity
from homeassistant.const import ATTR_ENTITY_ID, CONF_FILENAME
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util, slugify

from . import HikvisionConfigEntry
from .const import DOMAIN, ACTION_UPDATE_SNAPSHOT, HIKVISION_EVENT_IMAGE_UPDATED
from .helpers import get_camera_media_dir, get_media_dir
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
            if event.id in ("motiondetection", "fielddetection", "linedetection", "regionentrance", "regionexiting"):
                entities.append(EventImage(hass, device, camera, event, target="human"))
                entities.append(EventImage(hass, device, camera, event, target="vehicle"))

    async_add_entities(entities)

    try:
        from homeassistant.helpers.service import async_register_platform_entity_service
    except ImportError:
        async_register_platform_entity_service = None

    if not async_register_platform_entity_service:
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
        camera: AnalogCamera | IPCamera,
        stream_info: CameraStreamInfo,
    ) -> None:
        """Initialize the snapshot file."""

        ImageEntity.__init__(self, hass)

        self._attr_unique_id = slugify(f"{device.device_info.serial_no.lower()}_{stream_info.id}_snapshot")
        self.entity_id = f"image.{self.unique_id}"
        self._attr_translation_key = "snapshot"
        self._attr_translation_placeholders = {"camera": camera.name}

    def image(self) -> bytes | None:
        """Return bytes of image."""
        try:
            if self.file_path:
                if not self.hass.config.is_allowed_path(self.file_path):
                    _LOGGER.warning(
                        "Path %s is not in allowed directories",
                        self.file_path,
                    )
                    return None
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
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()


PLACEHOLDER_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360" viewBox="0 0 640 360">
  <rect width="100%" height="100%" fill="#1f232a"/>
  <g fill="#616e7f" transform="translate(288, 120)">
    <circle cx="32" cy="24" r="14"/>
    <path d="M52 8h-9.2l-3.2-5.4A4 4 0 0 0 36.2 0H27.8a4 4 0 0 0-3.4 2.6L21.2 8H12A12 12 0 0 0 0 20v28a12 12 0 0 0 12 12h40a12 12 0 0 0 12-12V20a12 12 0 0 0-12-12zm-20 44a18 18 0 1 1 0-36 18 18 0 0 1 0 36z"/>
  </g>
  <text x="50%" y="220" text-anchor="middle" fill="#8c9baa" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="18" font-weight="500">No image captured yet</text>
</svg>"""


class EventImage(ImageEntity):
    """An entity for displaying the last event image."""

    _attr_has_entity_name = True

    def __init__(
        self,
        hass: HomeAssistant,
        device: HikvisionDevice,
        camera: AnalogCamera | IPCamera,
        event: EventInfo,
        target: str | None = None,
    ) -> None:
        """Initialize the event image."""

        ImageEntity.__init__(self, hass)

        self.device = device
        self.camera = camera
        self.event = event
        self.target = target
        self._current_path: Path | None = None
        self._attr_device_info = device.hass_device_info(camera.id)
        target_param = f"_{target}" if target else ""
        self._attr_unique_id = slugify(f"{device.device_info.serial_no.lower()}_{camera.id}_{event.id}{target_param}_last_image")
        self.entity_id = f"image.{self.unique_id}"
        self._attr_translation_key = "event_image"
        
        from .const import EVENTS as ISAPI_EVENTS
        event_label = ISAPI_EVENTS.get(event.id, {}).get("label", event.id)
        if target:
            event_label = f"{event_label} {target.capitalize()}"
        self._attr_translation_placeholders = {
            "camera": camera.name,
            "event": event_label,
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
        if path_str := event.data.get("path"):
            candidate = Path(path_str).resolve()
            media_root = (get_media_dir(self.hass) / DOMAIN).resolve()
            www_root = Path(self.hass.config.path("www", DOMAIN)).resolve()
            if candidate.is_relative_to(media_root) or candidate.is_relative_to(www_root):
                self._current_path = candidate
        self._attr_image_last_updated = dt_util.utcnow()
        self.async_write_ha_state()

    @property
    def file_path(self) -> Path:
        """Return latest image path."""

        if self._current_path and self._current_path.exists():
            return self._current_path

        target_prefix = f"{self.event.id}_{self.target}" if self.target else self.event.id

        # 1. Check isolated camera media directory first (preferred)
        camera_media_dir = get_camera_media_dir(self.hass, self.device, self.camera.id)
        if camera_media_dir.exists():
            latest_file = camera_media_dir / f"{target_prefix}_latest.jpeg"
            if latest_file.exists():
                return latest_file
            matching = sorted(
                camera_media_dir.glob(f"{target_prefix}*.*"),
                key=lambda p: p.stat().st_mtime if not p.name.endswith(".tmp") else 0,
                reverse=True,
            )
            for f in matching:
                if not f.name.endswith(".tmp") and not f.name.endswith(".tmp.jpeg"):
                    return f

        # 2. Check root media directory fallback (legacy flat path)
        media_root = get_media_dir(self.hass) / DOMAIN / f"channel_{self.camera.id}"
        if media_root.exists():
            latest_file = media_root / f"{target_prefix}_latest.jpeg"
            if latest_file.exists():
                return latest_file
            matching = sorted(
                media_root.glob(f"{target_prefix}*.*"),
                key=lambda p: p.stat().st_mtime if not p.name.endswith(".tmp") else 0,
                reverse=True,
            )
            for f in matching:
                if not f.name.endswith(".tmp") and not f.name.endswith(".tmp.jpeg"):
                    return f

        # 2. Check www fallback directory
        www_base = Path(
            self.hass.config.path(
                "www",
                DOMAIN,
                self.device.entry.entry_id,
                f"channel_{self.camera.id}",
            )
        )
        if www_base.exists():
            latest_file = www_base / f"{target_prefix}_latest.jpeg"
            if latest_file.exists():
                return latest_file
            for extension in ("jpeg", "jpg", "png", "webp", "gif"):
                path = www_base / f"{target_prefix}.{extension}"
                if path.exists():
                    return path
            matching = sorted(
                www_base.glob(f"{target_prefix}*.*"),
                key=lambda p: p.stat().st_mtime if not p.name.endswith(".tmp") else 0,
                reverse=True,
            )
            for f in matching:
                if not f.name.endswith(".tmp") and not f.name.endswith(".tmp.jpeg"):
                    return f

        return camera_media_dir / f"{target_prefix}.jpeg"

    def image(self) -> bytes | None:
        """Return bytes of image."""

        try:
            path = self.file_path
            if path.exists():
                self._attr_content_type = "image/jpeg"
                self._attr_image_last_updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
                return path.read_bytes()
        except FileNotFoundError:
            pass

        # Return friendly placeholder SVG if no image is available
        self._attr_content_type = "image/svg+xml"
        return PLACEHOLDER_SVG
