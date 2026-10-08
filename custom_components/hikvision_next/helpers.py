"""Helper utilities for Hikvision Next."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import CONF_IS_GLOBAL_SETTINGS, DOMAIN, GLOBAL_SETTINGS_UNIQUE_ID

if TYPE_CHECKING:
    from .hikvision_device import HikvisionDevice

_LOGGER = logging.getLogger(__name__)


def get_media_dir(hass: HomeAssistant) -> Path:
    """Return the base media directory path.

    Prefers Home Assistant's configured media directories (e.g. 'local' pointing to /media),
    the root /media folder (standard in HAOS / container), or a sibling folder in dev setups,
    falling back to hass.config.path('media') for test harnesses.
    """
    # 1. Check media_dirs in hass.config
    if hass.config.media_dirs:
        if "local" in hass.config.media_dirs:
            return Path(hass.config.media_dirs["local"])
        # Return first configured media directory
        first_dir = next(iter(hass.config.media_dirs.values()))
        return Path(first_dir)

    # 2. Check root /media (standard in Home Assistant OS and Home Assistant Container)
    root_media = Path("/media")
    if root_media.exists() and root_media.is_dir():
        return root_media

    # 3. Check sibling media directory of config (dev environments)
    try:
        config_parent = Path(hass.config.config_dir).parent
        sibling_media = config_parent / "media"
        if sibling_media.exists() and sibling_media.is_dir():
            return sibling_media
    except Exception:
        pass

    # 4. Fallback for test harnesses or constrained environments
    return Path(hass.config.path("media"))


def get_camera_media_dir(
    hass: HomeAssistant,
    device: HikvisionDevice,
    channel_id: int,
) -> Path:
    """Return the isolated media storage directory for a specific camera/channel."""
    base_media = get_media_dir(hass)
    serial_no = (
        slugify(device.device_info.serial_no.lower())
        if device.device_info and device.device_info.serial_no
        else "unknown_device"
    )
    return base_media / DOMAIN / serial_no / f"channel_{channel_id}"


def is_global_settings_entry(entry: ConfigEntry) -> bool:
    """Check if config entry is the dedicated global settings entry."""
    return (
        entry.unique_id == GLOBAL_SETTINGS_UNIQUE_ID
        or bool(entry.data.get(CONF_IS_GLOBAL_SETTINGS, False))
    )


def get_global_settings_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the global settings config entry if one exists."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if is_global_settings_entry(entry):
            return entry
    return None
