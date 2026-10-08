"""hikvision component."""

from __future__ import annotations

import asyncio
from contextlib import suppress
import logging
import traceback
from typing import Any

from homeassistant.util import slugify
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_SHOW_SIDEBAR_PANEL,
    DEFAULT_SHOW_SIDEBAR_PANEL,
    DOMAIN,
    FRONTEND_STATIC_PATH,
    GLOBAL_SETTINGS_TITLE,
    PANEL_URL_PATH,
    VERSION,
)
from .helpers import get_global_settings_entry, is_global_settings_entry
from .hikvision_device import HikvisionDevice
from .isapi import ISAPIUnauthorizedError
from .notifications import EventNotificationsView
from .services import setup_services
from .views import HikvisionDeleteEventView, HikvisionEventsView, HikvisionImageView

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CAMERA,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.IMAGE,
]

type HikvisionConfigEntry = ConfigEntry[HikvisionDevice | None]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Hikvision component."""

    setup_services(hass)
    await _async_setup_frontend(hass)
    await async_ensure_global_settings_entry(hass)

    return True


async def _async_setup_frontend(hass: HomeAssistant) -> None:
    """Register frontend static files and custom panel."""
    from pathlib import Path

    frontend_dir = Path(__file__).parent / "frontend"
    if frontend_dir.exists():
        try:
            from homeassistant.components.http import StaticPathConfig

            await hass.http.async_register_static_paths(
                [StaticPathConfig(FRONTEND_STATIC_PATH, str(frontend_dir), cache_headers=False)]
            )
        except Exception as ex:
            _LOGGER.debug("Could not register static path: %s", ex)

        await async_update_sidebar_panel(hass)


async def async_ensure_global_settings_entry(hass: HomeAssistant) -> None:
    """Ensure global settings config entry exists if at least one device entry is present."""
    if get_global_settings_entry(hass):
        return

    device_entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if not is_global_settings_entry(entry)
    ]
    if not device_entries:
        return

    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "system"},
            data={},
        )
    )


def should_show_sidebar_panel(hass: HomeAssistant) -> bool:
    """Check if global settings entry has sidebar panel enabled."""
    global_entry = get_global_settings_entry(hass)
    if global_entry:
        return bool(global_entry.options.get(CONF_SHOW_SIDEBAR_PANEL, DEFAULT_SHOW_SIDEBAR_PANEL))
    return DEFAULT_SHOW_SIDEBAR_PANEL


async def async_update_sidebar_panel(hass: HomeAssistant) -> None:
    """Register or remove the custom sidebar panel based on config."""
    from homeassistant.components import frontend
    from homeassistant.components.frontend import DATA_PANELS

    show = should_show_sidebar_panel(hass)
    if show:
        target_module_url = f"{FRONTEND_STATIC_PATH}/timeline-panel.js?v={VERSION}"
        panel = hass.data.get(DATA_PANELS, {}).get(PANEL_URL_PATH)
        current_url = getattr(panel, "config", {}).get("_panel_custom", {}).get("module_url") if panel else None

        if panel and current_url != target_module_url:
            frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)
            panel = None

        if panel is None and PANEL_URL_PATH not in hass.data.get(DATA_PANELS, {}):
            try:
                from homeassistant.components.panel_custom import async_register_panel

                await async_register_panel(
                    hass,
                    frontend_url_path=PANEL_URL_PATH,
                    webcomponent_name="hikvision-timeline-panel",
                    sidebar_title="Hikvision Events",
                    sidebar_icon="mdi:cctv",
                    module_url=target_module_url,
                    require_admin=False,
                )
            except Exception as ex:
                _LOGGER.debug("Could not register custom panel: %s", ex)
    else:
        frontend.async_remove_panel(hass, PANEL_URL_PATH, warn_if_unknown=False)


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry on options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Set up integration from a config entry."""
    if is_global_settings_entry(entry):
        if entry.title != GLOBAL_SETTINGS_TITLE:
            hass.config_entries.async_update_entry(entry, title=GLOBAL_SETTINGS_TITLE)
        entry.async_on_unload(entry.add_update_listener(async_reload_entry))
        await async_update_sidebar_panel(hass)
        return True

    await async_ensure_global_settings_entry(hass)

    device = HikvisionDevice(hass, entry)
    device.pending_initialization = True
    try:
        await device.get_hardware_info()
        device_info = device.hass_device_info()
        device_registry = dr.async_get(hass)
        device_entry = device_registry.async_get_or_create(config_entry_id=entry.entry_id, **device_info)
        device.device_id = device_entry.id
    except ISAPIUnauthorizedError as ex:
        raise ConfigEntryAuthFailed from ex
    except Exception as ex:  # pylint: disable=broad-except
        msg = f"Cannot initialize {DOMAIN} {device.host}. Error: {ex}\n"
        _LOGGER.error(msg + traceback.format_exc())
        raise ConfigEntryNotReady(msg) from ex

    entry.runtime_data = device

    await device.init_coordinators()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    device.pending_initialization = False

    # Only initialise view once if multiple instances of integration
    if get_first_instance_unique_id(hass) == entry.unique_id:
        hass.http.register_view(EventNotificationsView(hass))
        hass.http.register_view(HikvisionEventsView(hass))
        hass.http.register_view(HikvisionImageView(hass))
        hass.http.register_view(HikvisionDeleteEventView(hass))

    refresh_disabled_entities_in_registry(hass, device)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await async_update_sidebar_panel(hass)

    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Delete device if not entities."""
    if not device_entry.via_device_id:
        _LOGGER.error(
            "You cannot delete the NVR device via the device delete method.  Please remove the integration instead"
        )
        return False
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionConfigEntry) -> bool:
    """Unload a config entry."""
    if is_global_settings_entry(entry):
        await async_update_sidebar_panel(hass)
        return True

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        # Reset alarm server after it has been set
        device = entry.runtime_data
        if device and device.control_alarm_server_host:
            with suppress(Exception):
                await device.set_alarm_server("http://0.0.0.0:80", "/")

        await async_update_sidebar_panel(hass)

    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up global settings entry if the last camera device entry was deleted."""
    if is_global_settings_entry(entry):
        return

    remaining_devices = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if not is_global_settings_entry(e) and e.entry_id != entry.entry_id
    ]
    if not remaining_devices:
        if global_entry := get_global_settings_entry(hass):
            hass.async_create_task(
                hass.config_entries.async_remove(global_entry.entry_id)
            )


def get_first_instance_unique_id(hass: HomeAssistant) -> Any:
    """Get entry unique_id for first device instance of integration."""
    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if not entry.disabled_by and not is_global_settings_entry(entry)
    ]
    return entries[0].unique_id if entries else None


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    if is_global_settings_entry(config_entry):
        return True

    _LOGGER.debug(
        "Migrating from version %s.%s",
        config_entry.version,
        config_entry.minor_version,
    )

    # 1 -> 2: Config entry unique_id format changed
    if config_entry.version == 1:
        unique_id = config_entry.unique_id
        new_unique_id = None
        if isinstance(unique_id, list) and len(unique_id) == 1 and isinstance(unique_id[0], list):
            new_unique_id = unique_id[0][1]

        updates: dict[str, Any] = {"version": 2}
        if new_unique_id:
            updates["unique_id"] = new_unique_id
        hass.config_entries.async_update_entry(config_entry, **updates)

    # 2 -> 3: Delete previous alarm server sensor entities
    if config_entry.version == 2:
        old_keys = ["protocoltype", "ipaddress", "portno", "url"]
        entity_registry = er.async_get(hass)
        for key in old_keys:
            entity_id = f"sensor.{slugify(config_entry.unique_id)}_alarm_server_{key}"
            entity = entity_registry.async_get(entity_id)
            if entity:
                entity_registry.async_remove(entity_id)

        hass.config_entries.async_update_entry(
            config_entry,
            version=3,
        )

    _LOGGER.debug(
        "Migration to version %s.%s successful",
        config_entry.version,
        config_entry.minor_version,
    )

    return True


def refresh_disabled_entities_in_registry(hass: HomeAssistant, device: HikvisionDevice) -> None:
    """Set disable state according to Notify Surveillance Center flag."""

    def update_entity(event, platform: Platform) -> None:
        entity_id = f"{platform}.{event.unique_id}"
        entity = entity_registry.async_get(entity_id)
        if not entity:
            if reg_entity_id := entity_registry.async_get_entity_id(platform, DOMAIN, entity_id):
                entity = entity_registry.async_get(reg_entity_id)
        if not entity:
            return
        if entity.disabled != event.disabled:
            disabled_by = er.RegistryEntryDisabler.INTEGRATION if event.disabled else None
            entity_registry.async_update_entity(entity.entity_id, disabled_by=disabled_by)

    entity_registry = er.async_get(hass)
    for camera in device.cameras:
        for event in camera.events_info:
            update_entity(event, Platform.SWITCH)
            update_entity(event, Platform.BINARY_SENSOR)

    for event in device.events_info:
        update_entity(event, Platform.SWITCH)
        update_entity(event, Platform.BINARY_SENSOR)
