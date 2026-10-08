"""Config flow for hikvision_next integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.components.network import async_get_source_ip
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, CONF_VERIFY_SSL
from homeassistant.core import callback

from . import HikvisionConfigEntry
from .const import (
    CONF_ALARM_SERVER_HOST,
    CONF_IMAGE_CAPTURE_MOVEMENT,
    CONF_IMAGE_RETENTION,
    CONF_IS_GLOBAL_SETTINGS,
    CONF_SET_ALARM_SERVER,
    CONF_SHOW_SIDEBAR_PANEL,
    DEFAULT_IMAGE_CAPTURE_MOVEMENT,
    DEFAULT_IMAGE_RETENTION_DAYS,
    DEFAULT_SHOW_SIDEBAR_PANEL,
    DOMAIN,
    GLOBAL_SETTINGS_TITLE,
    GLOBAL_SETTINGS_UNIQUE_ID,
    RTSP_PORT_FORCED,
)
from .helpers import is_global_settings_entry
from .hikvision_device import HikvisionDevice
from .isapi import ISAPIForbiddenError, ISAPIUnauthorizedError

_LOGGER = logging.getLogger(__name__)


class HikvisionConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for hikvision device."""

    VERSION = 3
    MINOR_VERSION = 1
    _entry: HikvisionConfigEntry

    async def get_schema(self, user_input: dict[str, Any]):
        """Get schema with suggested values."""
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default="http://"): str,
                vol.Optional(CONF_VERIFY_SSL, default=True): bool,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_SET_ALARM_SERVER, default=True): bool,
                vol.Required(CONF_ALARM_SERVER_HOST): str,
                vol.Optional(RTSP_PORT_FORCED): vol.And(int, vol.Range(min=1)),
            }
        )
        if self.source in (SOURCE_RECONFIGURE, SOURCE_REAUTH):
            return self.add_suggested_values_to_schema(
                schema,
                {**self._entry.data, **(user_input or {})},
            )
        local_ip = await async_get_source_ip(self.hass)
        return self.add_suggested_values_to_schema(
            schema,
            {CONF_ALARM_SERVER_HOST: f"http://{local_ip}:8123", **(user_input or {})},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initiated by the user."""

        errors = {}

        if user_input is not None:
            try:
                host = user_input[CONF_HOST].rstrip("/")
                user_input_validated = {
                    **user_input,
                    CONF_HOST: host,
                }

                device = HikvisionDevice(self.hass, data=user_input_validated)
                await device.get_device_info()

            except ISAPIForbiddenError:
                errors["base"] = "insufficient_permission"
            except ISAPIUnauthorizedError:
                errors["base"] = "invalid_auth"
            except Exception as ex:  # pylint: disable=broad-except
                _LOGGER.error("Unexpected %s %s", {type(ex).__name__}, ex)
                errors["base"] = f"Unexpected {type(ex).__name__}: {ex}"

            if not errors:
                if self.source == SOURCE_RECONFIGURE:
                    await self.async_set_unique_id(device.device_info.serial_no, raise_on_progress=False)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._entry,
                        data_updates=user_input_validated,
                    )
                if self.source == SOURCE_REAUTH:
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._entry,
                        data_updates=user_input_validated,
                    )

                # add new device
                await self.async_set_unique_id(device.device_info.serial_no)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=device.device_info.name, data=user_input_validated)

        # show form
        schema = await self.get_schema(user_input)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle device re-configuration."""
        self._entry = self._get_reconfigure_entry()
        return await self.async_step_user(user_input)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Perform reauth upon an authorization error."""
        self._entry = self._get_reauth_entry()
        return await self.async_step_user()

    async def async_step_system(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle automatic creation of global settings entry."""
        await self.async_set_unique_id(GLOBAL_SETTINGS_UNIQUE_ID)
        self._abort_if_unique_id_configured()

        initial_show_sidebar = DEFAULT_SHOW_SIDEBAR_PANEL
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if not is_global_settings_entry(entry) and CONF_SHOW_SIDEBAR_PANEL in entry.options:
                initial_show_sidebar = entry.options[CONF_SHOW_SIDEBAR_PANEL]
                break

        return self.async_create_entry(
            title=GLOBAL_SETTINGS_TITLE,
            data={CONF_IS_GLOBAL_SETTINGS: True},
            options={CONF_SHOW_SIDEBAR_PANEL: initial_show_sidebar},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return HikvisionOptionsFlowHandler()


class HikvisionOptionsFlowHandler(OptionsFlow):
    """Handle Hikvision options."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage the options."""
        if is_global_settings_entry(self.config_entry):
            if user_input is not None:
                return self.async_create_entry(title="", data=user_input)

            current_show_sidebar = bool(
                self.config_entry.options.get(
                    CONF_SHOW_SIDEBAR_PANEL, DEFAULT_SHOW_SIDEBAR_PANEL
                )
            )

            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema(
                    {
                        vol.Optional(
                            CONF_SHOW_SIDEBAR_PANEL,
                            default=current_show_sidebar,
                        ): bool,
                    }
                ),
            )

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_retention = int(
            self.config_entry.options.get(CONF_IMAGE_RETENTION, DEFAULT_IMAGE_RETENTION_DAYS)
        )
        current_capture_movement = bool(
            self.config_entry.options.get(
                CONF_IMAGE_CAPTURE_MOVEMENT, DEFAULT_IMAGE_CAPTURE_MOVEMENT
            )
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_IMAGE_RETENTION,
                        default=current_retention,
                    ): vol.All(vol.Coerce(int), vol.Range(min=0, max=365)),
                    vol.Optional(
                        CONF_IMAGE_CAPTURE_MOVEMENT,
                        default=current_capture_movement,
                    ): bool,
                }
            ),
        )

    async def async_step_global(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manage global integration options (alias)."""
        return await self.async_step_init(user_input)
