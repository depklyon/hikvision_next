"""Tests for Hikvision Next timeline views and image serving."""

import pytest
from http import HTTPStatus
from pathlib import Path
from homeassistant.core import HomeAssistant
from custom_components.hikvision_next.const import DOMAIN
from custom_components.hikvision_next.helpers import get_media_dir
from custom_components.hikvision_next.views import (
    HikvisionDeleteEventView,
    HikvisionEventsView,
    HikvisionImageView,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.parametrize("init_integration", ["DS-2CD2146G2-ISU"], indirect=True)
async def test_timeline_events_and_image_views(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
) -> None:
    """Test retrieving events and streaming images via API views."""

    serial = "ds_2cd2146g2_isu00000000aawrg00000000"
    channel = 1
    media_dir = get_media_dir(hass) / DOMAIN / serial / f"channel_{channel}"
    media_dir.mkdir(parents=True, exist_ok=True)

    test_image_content = b"fake jpeg image data"
    test_filename = "motiondetection_human_20261006_143000_123456.jpeg"
    (media_dir / test_filename).write_bytes(test_image_content)

    events_view = HikvisionEventsView(hass)
    events, cameras = events_view._scan_events_and_cameras(
        filter_camera=None,
        filter_target=None,
        filter_event=None,
        filter_device=None,
        limit=50,
    )

    assert len(events) >= 1
    assert len(cameras) >= 1
    ev = next(e for e in events if e["filename"] == test_filename)
    assert any(c["key"] == ev["camera_key"] for c in cameras)
    assert ev["channel_id"] == 1
    assert ev["target"] == "human"
    assert ev["event_type"] == "motiondetection"
    assert ev["formatted_time"] == "14:30:00"
    assert ev["formatted_date"] == "2026-10-06"
    assert ev["size"] == len(test_image_content)
    assert test_filename in ev["url"]
    assert "camera_key" in ev
    assert "camera_label" in ev
    assert ev["camera_key"] == f"{serial}_{channel}"

    # Test filtering by target
    human_events = events_view._scan_events(
        filter_camera=None,
        filter_target="human",
        filter_event=None,
        filter_device=None,
        limit=50,
    )
    assert any(e["filename"] == test_filename for e in human_events)

    vehicle_events = events_view._scan_events(
        filter_camera=None,
        filter_target="vehicle",
        filter_event=None,
        filter_device=None,
        limit=50,
    )
    assert not any(e["filename"] == test_filename for e in vehicle_events)

    # Test filtering by camera_key and channel
    cam_events = events_view._scan_events(
        filter_camera=ev["camera_key"],
        filter_target=None,
        filter_event=None,
        filter_device=None,
        limit=50,
    )
    assert any(e["filename"] == test_filename for e in cam_events)

    non_cam_events = events_view._scan_events(
        filter_camera="nonexistent_camera",
        filter_target=None,
        filter_event=None,
        filter_device=None,
        limit=50,
    )
    assert len(non_cam_events) == 0

    # Test image serving view
    image_view = HikvisionImageView(hass)
    from unittest.mock import MagicMock
    mock_request = MagicMock()

    resp = await image_view.get(mock_request, serial, str(channel), test_filename)
    assert resp.status == HTTPStatus.OK
    assert Path(resp._path).read_bytes() == test_image_content

    # Test fallback image resolution
    resp_fallback = await image_view.get(mock_request, "unknown_serial", str(channel), test_filename)
    assert resp_fallback.status == HTTPStatus.OK
    assert Path(resp_fallback._path).read_bytes() == test_image_content

    # Test sidebar panel helper
    from custom_components.hikvision_next import (
        async_ensure_global_settings_entry,
        async_update_sidebar_panel,
        get_global_settings_entry,
        should_show_sidebar_panel,
    )
    from custom_components.hikvision_next.const import CONF_SHOW_SIDEBAR_PANEL, PANEL_URL_PATH
    from homeassistant.components.frontend import DATA_PANELS
    from aiohttp import web

    assert should_show_sidebar_panel(hass) is True
    await async_update_sidebar_panel(hass)
    assert PANEL_URL_PATH in hass.data.get(DATA_PANELS, {})

    # Ensure global settings entry exists
    await async_ensure_global_settings_entry(hass)
    await hass.async_block_till_done()
    global_entry = get_global_settings_entry(hass)
    assert global_entry is not None

    # Disable panel in global options and verify removal
    hass.config_entries.async_update_entry(
        global_entry,
        options={CONF_SHOW_SIDEBAR_PANEL: False},
    )
    assert should_show_sidebar_panel(hass) is False
    await async_update_sidebar_panel(hass)
    assert PANEL_URL_PATH not in hass.data.get(DATA_PANELS, {})

    # --- Security Hardening Tests ---
    # 1. Invalid / Traversal parameter validation on image view
    with pytest.raises(web.HTTPBadRequest):
        await image_view.get(mock_request, "../etc", "1", "test.jpeg")

    with pytest.raises(web.HTTPBadRequest):
        await image_view.get(mock_request, serial, "invalid_channel", "test.jpeg")

    with pytest.raises(web.HTTPBadRequest):
        await image_view.get(mock_request, serial, "1", "../test.jpeg")

    # 2. Deletion view authorization and containment
    delete_view = HikvisionDeleteEventView(hass)
    non_admin_req = MagicMock()
    non_admin_req.get = MagicMock(return_value=MagicMock(is_admin=False))

    with pytest.raises(web.HTTPForbidden):
        await delete_view.delete(non_admin_req, serial, str(channel), test_filename)

    # Admin request without valid permissions or invalid path
    admin_user = MagicMock(is_admin=True)
    admin_req = MagicMock()
    admin_req.get = MagicMock(side_effect=lambda key, default=None: admin_user if key == "hass_user" else default)

    with pytest.raises(web.HTTPBadRequest):
        await delete_view.delete(admin_req, "../traversal", "1", "file.jpeg")

    # Admin successful deletion
    del_resp = await delete_view.delete(admin_req, serial, str(channel), test_filename)
    assert del_resp.status == HTTPStatus.OK
    assert not (media_dir / test_filename).exists()

    # Deletion of non-existent file returns 404
    with pytest.raises(web.HTTPNotFound):
        await delete_view.delete(admin_req, serial, str(channel), test_filename)

