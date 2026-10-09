"""HTTP API views for Hikvision Next timeline and event browsing."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http import HTTPStatus
import logging
from pathlib import Path
import re
from typing import Any

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import DOMAIN
from .helpers import get_media_dir, is_global_settings_entry

_LOGGER = logging.getLogger(__name__)

# Matches filenames like:
# motiondetection_human_20261006_224500_123456.jpeg
# motiondetection_20261006_224500_123456_1.jpeg
# motiondetection_latest.jpeg
FILENAME_REGEX = re.compile(
    r"^(?P<event>[a-zA-Z0-9]+)(?:_(?P<target>human|vehicle|movement))?_(?P<date>\d{8})_(?P<time>\d{6})(?:_\d+)?(?:_\d+)?\.(?P<ext>jpeg|jpg|png|webp)$"
)


class HikvisionEventsView(HomeAssistantView):
    """View to list event images with timeline metadata."""

    url = "/api/hikvision_next/events"
    name = "api:hikvision_next:events"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the events view."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Handle GET request to retrieve events."""
        filter_camera = request.query.get("camera")
        filter_target = request.query.get("target")
        filter_event = request.query.get("event")
        filter_device = request.query.get("device")
        try:
            limit = max(1, min(int(request.query.get("limit", 150)), 500))
        except (ValueError, TypeError):
            limit = 150

        events, cameras = await self.hass.async_add_executor_job(
            self._scan_events_and_cameras,
            filter_camera,
            filter_target,
            filter_event,
            filter_device,
            limit,
        )

        for event in events:
            try:
                event["url"] = async_sign_path(
                    self.hass,
                    event["url"],
                    timedelta(hours=24),
                )
            except Exception as err:
                _LOGGER.debug("Could not sign event URL: %s", err)

        return self.json(
            {
                "success": True,
                "count": len(events),
                "events": events,
                "cameras": cameras,
            }
        )

    def _scan_events(
        self,
        filter_camera: str | None,
        filter_target: str | None,
        filter_event: str | None,
        filter_device: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Backwards compatible scan events helper."""
        events, _ = self._scan_events_and_cameras(
            filter_camera, filter_target, filter_event, filter_device, limit
        )
        return events

    def _scan_events_and_cameras(
        self,
        filter_camera: str | None,
        filter_target: str | None,
        filter_event: str | None,
        filter_device: str | None,
        limit: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Scan media directories for event images and gather all configured cameras."""
        base_media = get_media_dir(self.hass)
        hikvision_dir = base_media / DOMAIN

        cameras_map: dict[str, dict[str, Any]] = {}
        device_camera_names: dict[str, str] = {}

        # 1. Discover all cameras from config entries and runtime data
        entries_data = self.hass.config_entries.async_entries(DOMAIN)
        for entry in entries_data:
            if entry.disabled_by or is_global_settings_entry(entry):
                continue
            dev = getattr(entry, "runtime_data", None)
            serial = ""
            device_name = entry.title or "Camera"
            if dev and dev.device_info and dev.device_info.serial_no:
                serial = slugify(dev.device_info.serial_no.lower())
                if dev.device_info.name:
                    device_name = dev.device_info.name

            cams = getattr(dev, "cameras", []) if dev else []
            if cams:
                for cam in cams:
                    cam_id = getattr(cam, "id", 1)
                    cam_name = getattr(cam, "name", device_name)
                    if not cam_name or cam_name == f"Camera {cam_id}":
                        cam_name = device_name
                    cam_key = f"{serial}_{cam_id}" if serial else f"ch_{cam_id}"
                    device_camera_names[cam_key] = cam_name
                    short_serial = serial.upper()[-8:] if len(serial) > 12 else serial.upper()
                    label = f"{cam_name} ({short_serial})" if short_serial else cam_name
                    cameras_map[cam_key] = {
                        "key": cam_key,
                        "device_serial": serial,
                        "channel_id": cam_id,
                        "name": cam_name,
                        "label": label,
                    }
            else:
                cam_key = f"{serial}_1" if serial else entry.entry_id
                device_camera_names[cam_key] = device_name
                short_serial = serial.upper()[-8:] if len(serial) > 12 else serial.upper()
                label = f"{device_name} ({short_serial})" if short_serial else device_name
                cameras_map[cam_key] = {
                    "key": cam_key,
                    "device_serial": serial,
                    "channel_id": 1,
                    "name": device_name,
                    "label": label,
                }

        # 2. Discover from device registry
        try:
            from homeassistant.helpers import device_registry as dr
            dev_reg = dr.async_get(self.hass)
            for d in dev_reg.devices.values():
                for domain, identifier in d.identifiers:
                    if domain == DOMAIN:
                        serial = slugify(identifier.lower())
                        cam_key = f"{serial}_1"
                        if cam_key not in cameras_map:
                            cam_name = d.name_by_user or d.name or d.model or "Camera"
                            short_serial = serial.upper()[-8:] if len(serial) > 12 else serial.upper()
                            label = f"{cam_name} ({short_serial})" if short_serial else cam_name
                            device_camera_names[cam_key] = cam_name
                            cameras_map[cam_key] = {
                                "key": cam_key,
                                "device_serial": serial,
                                "channel_id": 1,
                                "name": cam_name,
                                "label": label,
                            }
        except Exception as err:
            _LOGGER.debug("Could not read device registry: %s", err)

        events: list[dict[str, Any]] = []

        if not hikvision_dir.exists():
            return [], list(cameras_map.values())

        # Find all image files
        for img_path in hikvision_dir.glob("**/*.*"):
            if img_path.name.endswith(".tmp") or ".tmp." in img_path.name:
                continue
            if img_path.suffix.lower() not in (".jpeg", ".jpg", ".png", ".webp"):
                continue
            if "_latest." in img_path.name:
                continue

            # Determine channel and device from path hierarchy
            rel = img_path.relative_to(hikvision_dir)
            parts = rel.parts

            device_serial = ""
            channel_id = 1

            if len(parts) >= 3 and parts[1].startswith("channel_"):
                # New isolated structure: hikvision_next/<device_serial>/channel_<id>/<file>
                device_serial = parts[0]
                try:
                    channel_id = int(parts[1].removeprefix("channel_"))
                except ValueError:
                    channel_id = 1
            elif len(parts) >= 2 and parts[0].startswith("channel_"):
                # Legacy flat structure: hikvision_next/channel_<id>/<file>
                try:
                    channel_id = int(parts[0].removeprefix("channel_"))
                except ValueError:
                    channel_id = 1
            else:
                continue

            cam_key = f"{device_serial}_{channel_id}" if device_serial else f"ch_{channel_id}"

            if filter_device and device_serial != filter_device:
                continue

            if filter_camera:
                clean_filter_cam = filter_camera.removeprefix("channel_")
                if not any(fc in (cam_key, device_serial, str(channel_id)) for fc in (filter_camera, clean_filter_cam)):
                    continue

            # Parse filename metadata
            match = FILENAME_REGEX.match(img_path.name)
            event_type = ""
            target = "movement"
            ts_iso = ""
            formatted_time = ""
            formatted_date = ""

            try:
                stat = img_path.stat()
                file_size = stat.st_size
                mtime_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            except OSError:
                continue

            if match:
                event_type = match.group("event")
                target = match.group("target") or "movement"
                date_str = match.group("date")
                time_str = match.group("time")
                try:
                    dt = datetime.strptime(f"{date_str}_{time_str}", "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
                    ts_iso = dt.isoformat()
                    formatted_time = dt.strftime("%H:%M:%S")
                    formatted_date = dt.strftime("%Y-%m-%d")
                except ValueError:
                    ts_iso = mtime_dt.isoformat()
                    formatted_time = mtime_dt.strftime("%H:%M:%S")
                    formatted_date = mtime_dt.strftime("%Y-%m-%d")
            else:
                # Fallback for non-standard filenames
                parts_name = img_path.stem.split("_")
                event_type = parts_name[0]
                if len(parts_name) > 1 and parts_name[1] in ("human", "vehicle", "movement"):
                    target = parts_name[1]
                else:
                    target = "movement"
                ts_iso = mtime_dt.isoformat()
                formatted_time = mtime_dt.strftime("%H:%M:%S")
                formatted_date = mtime_dt.strftime("%Y-%m-%d")

            if filter_target and filter_target != "all" and target != filter_target:
                continue

            if filter_event and filter_event != "all" and event_type != filter_event:
                continue

            camera_name = device_camera_names.get(cam_key)
            if not camera_name:
                camera_name = device_camera_names.get(f"{device_serial}_{channel_id}", f"Camera {channel_id}")

            if device_serial:
                short_serial = device_serial.upper()
                if len(short_serial) > 16:
                    short_serial = short_serial[-8:]
                camera_label = f"{camera_name} ({short_serial} • Ch {channel_id})"
            else:
                camera_label = f"{camera_name} (Ch {channel_id})"

            events.append(
                {
                    "id": f"{device_serial}_{channel_id}_{img_path.name}",
                    "filename": img_path.name,
                    "channel_id": channel_id,
                    "device_serial": device_serial,
                    "camera_key": cam_key,
                    "camera_name": camera_name,
                    "camera_label": camera_label,
                    "event_type": event_type,
                    "target": target,
                    "timestamp": ts_iso,
                    "formatted_time": formatted_time,
                    "formatted_date": formatted_date,
                    "size": file_size,
                    "url": f"/api/hikvision_next/image/{device_serial or 'default'}/{channel_id}/{img_path.name}",
                }
            )

        # Sort descending by timestamp
        events.sort(key=lambda e: e["timestamp"], reverse=True)
        return events[:limit], list(cameras_map.values())


SAFE_IDENTIFIER_REGEX = re.compile(r"^[a-zA-Z0-9_\-]+$")
SAFE_CHANNEL_REGEX = re.compile(r"^[0-9]+$")
SAFE_FILENAME_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]+$")


class HikvisionImageView(HomeAssistantView):
    """View to securely stream event images."""

    url = "/api/hikvision_next/image/{device_serial}/{channel_id}/{filename}"
    name = "api:hikvision_next:image"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the image view."""
        self.hass = hass

    async def get(
        self, request: web.Request, device_serial: str, channel_id: str, filename: str
    ) -> web.Response:
        """Serve the requested image."""
        if (
            not SAFE_IDENTIFIER_REGEX.match(device_serial)
            or not SAFE_CHANNEL_REGEX.match(channel_id)
            or not SAFE_FILENAME_REGEX.match(filename)
            or ".." in filename
        ):
            raise web.HTTPBadRequest(text="Invalid path parameters")

        base_media = get_media_dir(self.hass)
        hikvision_dir = (base_media / DOMAIN).resolve()

        # 1. Check isolated directory: hikvision_next/<device_serial>/channel_<id>/<filename>
        candidate = (hikvision_dir / device_serial / f"channel_{channel_id}" / filename).resolve()
        if not candidate.is_relative_to(hikvision_dir):
            raise web.HTTPBadRequest(text="Invalid path")

        if candidate.exists() and candidate.is_file():
            return web.FileResponse(candidate, headers={"Cache-Control": "public, max-age=86400"})

        # 2. Check legacy flat directory: hikvision_next/channel_<id>/<filename>
        legacy_candidate = (hikvision_dir / f"channel_{channel_id}" / filename).resolve()
        if legacy_candidate.is_relative_to(hikvision_dir) and legacy_candidate.exists() and legacy_candidate.is_file():
            return web.FileResponse(legacy_candidate, headers={"Cache-Control": "public, max-age=86400"})

        # 3. Fallback search: any camera folder for this channel and filename
        for matched in hikvision_dir.glob(f"*/channel_{channel_id}/{filename}"):
            if matched.resolve().is_relative_to(hikvision_dir) and matched.is_file():
                return web.FileResponse(matched, headers={"Cache-Control": "public, max-age=86400"})

        raise web.HTTPNotFound(text="Image not found")

    head = get


class HikvisionDeleteEventView(HomeAssistantView):
    """View to delete an event image."""

    url = "/api/hikvision_next/event/{device_serial}/{channel_id}/{filename}"
    name = "api:hikvision_next:delete_event"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the delete view."""
        self.hass = hass

    async def delete(
        self, request: web.Request, device_serial: str, channel_id: str, filename: str
    ) -> web.Response:
        """Delete the specified image file."""
        hass_user = request.get("hass_user")
        if not hass_user or not getattr(hass_user, "is_admin", False):
            raise web.HTTPForbidden(text="Admin privileges required to delete events")

        if (
            not SAFE_IDENTIFIER_REGEX.match(device_serial)
            or not SAFE_CHANNEL_REGEX.match(channel_id)
            or not SAFE_FILENAME_REGEX.match(filename)
            or ".." in filename
        ):
            raise web.HTTPBadRequest(text="Invalid path parameters")

        base_media = get_media_dir(self.hass)
        hikvision_dir = (base_media / DOMAIN).resolve()
        candidate = (hikvision_dir / device_serial / f"channel_{channel_id}" / filename).resolve()

        if not candidate.is_relative_to(hikvision_dir):
            raise web.HTTPBadRequest(text="Invalid path")

        if candidate.exists() and candidate.is_file():
            try:
                candidate.unlink(missing_ok=True)
                return self.json({"success": True})
            except OSError as err:
                return self.json({"success": False, "error": str(err)}, status_code=HTTPStatus.INTERNAL_SERVER_ERROR)

        raise web.HTTPNotFound(text="Image not found")
