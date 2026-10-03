"""Provides device triggers for Hikvision cameras."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import (
    CONF_DEVICE_ID,
    CONF_DOMAIN,
    CONF_PLATFORM,
    CONF_TYPE,
)
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, HIKVISION_EVENT

CONF_EVENT_TYPE = "event_type"
CONF_DETECTION_TARGET = "detection_target"

TRIGGER_TYPES = {
    "motion": {"event": "motiondetection", "target": None},
    "human": {"event": "motiondetection", "target": "human"},
    "vehicle": {"event": "motiondetection", "target": "vehicle"},
    "intrusion": {"event": "fielddetection", "target": None},
    "intrusion_human": {"event": "fielddetection", "target": "human"},
    "intrusion_vehicle": {"event": "fielddetection", "target": "vehicle"},
    "line_crossing": {"event": "linedetection", "target": None},
    "line_crossing_human": {"event": "linedetection", "target": "human"},
    "line_crossing_vehicle": {"event": "linedetection", "target": "vehicle"},
    "tamper": {"event": "tamperdetection", "target": None},
    "scene_change": {"event": "scenechangedetection", "target": None},
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES.keys()),
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, Any]]:
    """Return a list of triggers for a Hikvision device."""

    device_registry = dr.async_get(hass)
    device_entry = device_registry.async_get(device_id)
    if not device_entry:
        return []

    # Verify device belongs to this integration
    entry_id = next(iter(device_entry.config_entries), None)
    if not entry_id:
        return []

    triggers = []
    for trigger_type in TRIGGER_TYPES:
        triggers.append(
            {
                CONF_PLATFORM: "device",
                CONF_DOMAIN: DOMAIN,
                CONF_DEVICE_ID: device_id,
                CONF_TYPE: trigger_type,
            }
        )

    return triggers


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Attach a trigger."""

    trigger_type = config[CONF_TYPE]
    trigger_spec = TRIGGER_TYPES.get(trigger_type, {})
    target_event = trigger_spec.get("event")
    required_target = trigger_spec.get("target")

    async def _handle_event(event: Any) -> None:
        data = event.data
        if target_event and data.get("event_id") != target_event:
            return
        if data.get("event_state") == "inactive":
            return
        if required_target:
            targets = data.get("detection_targets", [])
            primary_target = data.get("detection_target")
            if required_target not in targets and primary_target != required_target:
                return

        return await action(
            {
                "trigger": {
                    **trigger_info.trigger_data,
                    "platform": "device",
                    "event": event,
                    "description": f"Hikvision {trigger_type} triggered",
                }
            }
        )

    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: HIKVISION_EVENT,
        }
    )

    return await event_trigger.async_attach_trigger(
        hass, event_config, _handle_event, trigger_info
    )
