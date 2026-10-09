"""Tests for specific ISAPI responses."""

import respx
import httpx
from contextlib import suppress
from custom_components.hikvision_next.isapi import StorageInfo
from tests.conftest import mock_endpoint, load_fixture


@respx.mock
async def test_storage(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("ContentMgmt/Storage", "hdd1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 1
    assert storage_list[0] == StorageInfo(
        id=1,
        name="hdd1",
        type="SATA",
        status="ok",
        capacity=1907729,
        freespace=0,
        property="RW",
        ip="",
    )

    mock_endpoint("ContentMgmt/Storage", "hdd1_nas1")
    storage_list = await isapi.get_storage_devices()
    assert len(storage_list) == 2
    assert storage_list[0].type == "SATA"
    assert storage_list[1].type == "NFS"
    assert storage_list[1].ip != ""

    mock_endpoint("ContentMgmt/Storage", status_code=500)
    with suppress(Exception):
        storage_list = await isapi.get_storage_devices()
        assert len(storage_list) == 0


@respx.mock
async def test_notification_hosts(mock_isapi):
    isapi = mock_isapi

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    host_nvr = await isapi.get_alarm_server()

    mock_endpoint("Event/notification/httpHosts", "ipc_list")
    host_ipc = await isapi.get_alarm_server()

    assert host_nvr == host_ipc


@respx.mock
async def test_update_notification_hosts(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("http://1.0.0.11:8123", "/api/hikvision")

    assert endpoint.called


@respx.mock
async def test_update_notification_hosts_from_ipaddress_to_hostname(mock_isapi):
    isapi = mock_isapi

    def update_side_effect(request, route):
        payload = load_fixture("ISAPI/Event.notification.httpHosts", "set_alarm_server_outside_network_payload")
        if request.content.decode("utf-8") != payload:
            raise AssertionError("Request content does not match expected payload")
        return httpx.Response(200)

    mock_endpoint("Event/notification/httpHosts", "nvr_single_item")
    url = f"{isapi.host}/ISAPI/Event/notification/httpHosts"
    endpoint = respx.put(url).mock(side_effect=update_side_effect)
    await isapi.set_alarm_server("https://ha.hostname.domain", "/api/hikvision")

    assert endpoint.called


def test_parse_event_notification_suffixed_event():
    """Test parsing alert XML with suffixed event types like facedetection-1."""
    from custom_components.hikvision_next.isapi.isapi import ISAPIClient

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<EventNotificationAlert version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
    <ipAddress>1.0.0.18</ipAddress>
    <portNo>8123</portNo>
    <protocol>HTTP</protocol>
    <macAddress>DD:73:4A:29:96:F1</macAddress>
    <channelID>1</channelID>
    <dateTime>2026-10-08T21:00:00-03:00</dateTime>
    <activePostCount>1</activePostCount>
    <eventType>facedetection-1</eventType>
    <eventState>active</eventState>
    <eventDescription>Face detection alarm</eventDescription>
</EventNotificationAlert>"""

    alert = ISAPIClient.parse_event_notification(xml)
    assert alert.event_id == "facedetection"
    assert alert.channel_id == 1
    assert alert.mac == "DD:73:4A:29:96:F1"
    assert alert.ip_address == "1.0.0.18"


@respx.mock
async def test_get_supported_events_with_suffixed_trigger(mock_isapi):
    """Test get_supported_events with facedetection-1 trigger from camera."""
    isapi = mock_isapi

    xml_content = """<?xml version="1.0" encoding="UTF-8"?>
<EventTriggerList version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
    <EventTrigger>
        <id>facedetection-1</id>
        <eventType>facedetection-1</eventType>
        <videoInputChannelID>1</videoInputChannelID>
        <EventTriggerNotificationList>
            <EventTriggerNotification>
                <notificationMethod>center</notificationMethod>
            </EventTriggerNotification>
        </EventTriggerNotificationList>
    </EventTrigger>
</EventTriggerList>"""

    url = f"{isapi.host}/ISAPI/Event/triggers"
    respx.get(url).mock(return_value=httpx.Response(200, text=xml_content))

    events = await isapi.get_supported_events({})
    assert len(events) == 1
    assert events[0].id == "facedetection"
    assert events[0].channel_id == 1
    assert events[0].url == "Smart/FaceDetection/1"
    assert "center" in events[0].notifications
