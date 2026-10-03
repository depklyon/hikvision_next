"""Platform for binary sensor integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from . import HikvisionConfigEntry
from .const import (
    DEFAULT_SENSOR_RESET_SECONDS,
    EVENTS,
    HIKVISION_SIGNAL_EVENT,
)
from .hikvision_device import HikvisionDevice
from .isapi import EventInfo
from .isapi.const import EVENT_IO


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add binary sensors for hikvision events states."""

    device = entry.runtime_data

    entities = []

    # Video Events
    for camera in device.cameras:
        for event in camera.events_info:
            entities.append(EventBinarySensor(device, camera.id, event))
            if event.id in ("motiondetection", "fielddetection", "linedetection", "regionentrance", "regionexiting"):
                entities.append(TargetBinarySensor(device, camera.id, event, "human"))
                entities.append(TargetBinarySensor(device, camera.id, event, "vehicle"))

    # General Events
    for event in device.events_info:
        entities.append(EventBinarySensor(device, 0, event))

    async_add_entities(entities)


class TargetBinarySensor(BinarySensorEntity):
    """Event detection target sensor."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo, target: str) -> None:
        """Initialize."""
        self.device = device
        self.event_info = event
        self.target = target
        self.entity_id = f"binary_sensor.{event.unique_id}_{target}"
        self._attr_unique_id = self.entity_id

        base_name = EVENTS[event.id].get("label", event.id)
        target_title = target.capitalize()
        self._attr_name = f"{base_name} {target_title}"

        self._attr_device_class = EVENTS[event.id]["device_class"]
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled
        self._reset_timer_cancel = None

    async def async_added_to_hass(self) -> None:
        """Register signal listener."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{HIKVISION_SIGNAL_EVENT}_{self.unique_id}",
                self._handle_event,
            )
        )

    @callback
    def _handle_event(self, state: bool, attributes: dict) -> None:
        """Handle incoming event signal."""
        if self._reset_timer_cancel:
            self._reset_timer_cancel()
            self._reset_timer_cancel = None

        self._attr_is_on = state
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()

        if state:
            @callback
            def _reset_state(*_):
                self._attr_is_on = False
                self._reset_timer_cancel = None
                self.async_write_ha_state()

            self._reset_timer_cancel = async_call_later(
                self.hass,
                DEFAULT_SENSOR_RESET_SECONDS,
                _reset_state,
            )


    async def async_will_remove_from_hass(self) -> None:
        """Cancel lingering timer when removed."""
        if self._reset_timer_cancel:
            self._reset_timer_cancel()
            self._reset_timer_cancel = None


class EventBinarySensor(BinarySensorEntity):
    """Event detection sensor."""

    _attr_has_entity_name = True
    _attr_is_on = False

    def __init__(self, device: HikvisionDevice, device_id: int, event: EventInfo) -> None:
        """Initialize."""
        self.device = device
        self.event_info = event
        self.entity_id = f"binary_sensor.{event.unique_id}"
        self._attr_unique_id = self.entity_id
        self._attr_translation_key = event.id
        if event.id == EVENT_IO:
            self._attr_translation_placeholders = {"io_port_id": event.io_port_id}
        self._attr_device_class = EVENTS[event.id]["device_class"]
        self._attr_device_info = device.hass_device_info(device_id)
        self._attr_entity_registry_enabled_default = not event.disabled
        self._reset_timer_cancel = None

    async def async_added_to_hass(self) -> None:
        """Register signal listener."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{HIKVISION_SIGNAL_EVENT}_{self.unique_id}",
                self._handle_event,
            )
        )

    @callback
    def _handle_event(self, state: bool, attributes: dict) -> None:
        """Handle incoming event signal."""
        if self._reset_timer_cancel:
            self._reset_timer_cancel()
            self._reset_timer_cancel = None

        self._attr_is_on = state
        self._attr_extra_state_attributes = attributes
        self.async_write_ha_state()

        if state:
            @callback
            def _reset_state(*_):
                self._attr_is_on = False
                self._reset_timer_cancel = None
                self.async_write_ha_state()

            self._reset_timer_cancel = async_call_later(
                self.hass,
                DEFAULT_SENSOR_RESET_SECONDS,
                _reset_state,
            )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel lingering timer when removed."""
        if self._reset_timer_cancel:
            self._reset_timer_cancel()
            self._reset_timer_cancel = None
