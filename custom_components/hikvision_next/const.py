"""hikvision integration constants."""

from typing import Final

from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from .isapi.const import EVENTS as ISAPI_EVENTS

DOMAIN: Final = "hikvision_next"
VERSION: Final = "1.5.0"

RTSP_PORT_FORCED: Final = "rtsp_port_forced"
CONF_SET_ALARM_SERVER: Final = "set_alarm_server"
CONF_ALARM_SERVER_HOST: Final = "alarm_server"
CONF_IMAGE_RETENTION: Final = "image_retention"
DEFAULT_IMAGE_RETENTION_DAYS: Final = 7
CONF_IMAGE_CAPTURE_MOVEMENT: Final = "image_capture_movement"
DEFAULT_IMAGE_CAPTURE_MOVEMENT: Final = False
CONF_SHOW_SIDEBAR_PANEL: Final = "show_sidebar_panel"
DEFAULT_SHOW_SIDEBAR_PANEL: Final = True
GLOBAL_SETTINGS_UNIQUE_ID: Final = "hikvision_global_settings"
CONF_IS_GLOBAL_SETTINGS: Final = "is_global_settings"
GLOBAL_SETTINGS_TITLE: Final = "⚙️ Global Settings"
DEFAULT_SENSOR_RESET_SECONDS: Final = 5
ALARM_SERVER_PATH = "/api/hikvision"

TARGET_HUMAN: Final = "human"
TARGET_VEHICLE: Final = "vehicle"
TARGET_MOVEMENT: Final = "movement"
ATTR_TARGET: Final = "target"

PANEL_URL_PATH: Final = "hikvision-events"
FRONTEND_STATIC_PATH: Final = "/hikvision_next_frontend"

EVENTS_COORDINATOR: Final = "events"
SECONDARY_COORDINATOR: Final = "secondary"
HOLIDAY_MODE = "holiday_mode"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ACTION_REBOOT = "reboot"
ACTION_ISAPI_REQUEST = "isapi_request"
ACTION_UPDATE_SNAPSHOT = "update_snapshot"

HIKVISION_EVENT = f"{DOMAIN}_event"
HIKVISION_EVENT_IMAGE_UPDATED = f"{DOMAIN}_event_image_updated"
HIKVISION_SIGNAL_EVENT = f"{DOMAIN}_signal_event"

ATTR_LAST_EVENT_RECEIVED_AT = "last_event_received_at"
ATTR_LAST_IMAGE_CONTENT_TYPE = "last_image_content_type"
ATTR_LAST_IMAGE_PATH = "last_image_path"
ATTR_LAST_IMAGE_SIZE = "last_image_size"
ATTR_LAST_IMAGE_URL = "last_image_url"
ATTR_DETECTION_TARGETS = "detection_targets"
ATTR_IMAGE_HISTORY = "image_history"

EVENTS = {
    "motiondetection": {
        **ISAPI_EVENTS["motiondetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "tamperdetection": {
        **ISAPI_EVENTS["tamperdetection"],
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "videoloss": {
        **ISAPI_EVENTS["videoloss"],
        "device_class": BinarySensorDeviceClass.PROBLEM,
    },
    "scenechangedetection": {
        **ISAPI_EVENTS["scenechangedetection"],
        "device_class": BinarySensorDeviceClass.TAMPER,
    },
    "fielddetection": {
        **ISAPI_EVENTS["fielddetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "linedetection": {
        **ISAPI_EVENTS["linedetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "facedetection": {
        **ISAPI_EVENTS["facedetection"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "regionentrance": {
        **ISAPI_EVENTS["regionentrance"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "regionexiting": {
        **ISAPI_EVENTS["regionexiting"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "io": {
        **ISAPI_EVENTS["io"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
    "pir": {
        **ISAPI_EVENTS["pir"],
        "device_class": BinarySensorDeviceClass.MOTION,
    },
}
