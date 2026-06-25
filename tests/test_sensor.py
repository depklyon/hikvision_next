"""Tests for sensor platform."""

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
import homeassistant.helpers.entity_registry as er
from homeassistant.util import slugify


@pytest.mark.parametrize("init_integration", ["DS-7608NXI-I2"], indirect=True)
async def test_sensor_value(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test sensors value."""

    for entity_id, state in [
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_address", "1.0.0.159"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_port_no", "8123"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_path", "/api/hikvision"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_alarm_server_protocol_type", "HTTP"),
        ("sensor.ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu_1_hdd1", "OK"),
    ]:
        assert (sensor := hass.states.get(entity_id))
        assert sensor.state == state

@pytest.mark.parametrize("init_integration", ["DS-2CD2T86G2-ISU"], indirect=True)
async def test_sensor_value_outside_network(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test sensors value."""

    for entity_id, state in [
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_address", "ha.hostname.domain"),
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_port_no", "443"),
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_path", "/api/hikvision"),
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_alarm_server_protocol_type", "HTTPS"),
        ("sensor.ds_2cd2t86g2_isu_sl00000000aawrae0000000_1_hdde", "OK"),
    ]:
        assert (sensor := hass.states.get(entity_id))
        assert sensor.state == state


@pytest.mark.parametrize("init_integration", ["DS-2CD2146G2-ISU", "DS-7608NXI-I2"], indirect=True)
async def test_scenechange_support(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test sensors value."""

    device_data = {
        "DS-7608NXI-I2": {
            "serial_no": "ds_7608nxi_i0_0p_s0000000000ccrrj00000000wcvu",
            "disabled": False,
        },
        "DS-2CD2146G2-ISU": {
            "serial_no": "ds_2cd2146g2_isu00000000aawrg00000000",
            "disabled": False,
        },
    }

    data = device_data[init_integration.title]
    entities = [
        f"binary_sensor.{data['serial_no']}_1_scenechangedetection",
        f"switch.{data['serial_no']}_1_scenechangedetection"
    ]

    entity_registry = er.async_get(hass)
    for entity_id in entities:
        assert (entity := entity_registry.async_get(entity_id))
        assert entity.disabled == data["disabled"]


@pytest.mark.parametrize("init_integration", ["iDS-7204HUHI-M1"], indirect=True)
async def test_facedetection_entities(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test face detection is exposed as sensor, switch, and image entities."""

    device = init_integration.runtime_data
    serial_no = slugify(device.device_info.serial_no.lower())
    entity_registry = er.async_get(hass)

    face_events = [event for camera in device.cameras for event in camera.events_info if event.id == "facedetection"]
    assert face_events

    channel_id = face_events[0].channel_id
    for platform in ("binary_sensor", "switch", "image"):
        entity_id = f"{platform}.{serial_no}_{channel_id}_facedetection"
        if platform == "image":
            entity_id = f"{entity_id}_last_image"
        assert entity_registry.async_get(entity_id)
