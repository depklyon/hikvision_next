"""Capture raw Hikvision event alerts (XML + pictures) from a camera/NVR.

Connects to the ISAPI alert stream (``/ISAPI/Event/notification/alertStream``),
which works alongside the Home Assistant push notifications and requires no
change to the device configuration.

Every alert is saved to the output folder (raw XML and any attached images) and
a one-line summary is printed, including all detection target related tags so
we can see how the firmware reports human / vehicle detections.

Usage (standard library only, Python 3.9+):

    python scripts/capture_hikvision_events.py --host 192.168.1.7 --user admin

The password is asked interactively (or read from the HIKVISION_PASSWORD
environment variable). Press Ctrl+C to stop.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import getpass
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

ALERT_STREAM_PATH = "/ISAPI/Event/notification/alertStream"
HEARTBEAT_EVENTS = {"videoloss"}  # sent periodically with eventState=inactive


def strip_ns(tag: str) -> str:
    """Remove the XML namespace from a tag."""
    return tag.rsplit("}", 1)[-1]


def summarize_alert(xml_bytes: bytes) -> tuple[str, str, str]:
    """Return (event_type, event_state, summary line) for an alert XML."""

    text = xml_bytes.decode("utf-8", errors="replace").strip()
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        try:
            # some firmwares send non-escaped '&'
            root = ET.fromstring(re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#)", "&amp;", text))
        except ET.ParseError as ex:
            return "unparsed", "", f"!! could not parse XML: {ex}"

    values: dict[str, list[str]] = {}
    for element in root.iter():
        text = (element.text or "").strip()
        if text:
            values.setdefault(strip_ns(element.tag), []).append(text)

    def first(tag: str, default: str = "") -> str:
        return values.get(tag, [default])[0]

    event_type = first("eventType", "?")
    if event_type.lower() == "duration":
        event_type = f"duration/{first('relationEvent', '?')}"
    event_state = first("eventState", "?")

    # Everything that may carry the detected target, whatever the firmware calls it
    target_info = {
        tag: vals
        for tag, vals in values.items()
        if re.search(r"target|object|human|vehicle|person|car", tag, re.IGNORECASE)
        and not re.search(r"rect|^x$|^y$|width|height", tag, re.IGNORECASE)
    }

    parts = [
        f"event={event_type}",
        f"state={event_state}",
        f"channel={first('channelID') or first('dynChannelID') or '-'}",
        f"regions={','.join(values.get('regionID', [])) or '-'}",
        f"pictures={first('detectionPicturesNumber', '-')}",
        f"desc={first('eventDescription', '-')!r}",
    ]
    if target_info:
        parts.append("TARGETS=" + "; ".join(f"{tag}={'|'.join(vals)}" for tag, vals in target_info.items()))
    else:
        parts.append("TARGETS=<none>")
    return event_type, event_state, "  ".join(parts)


class MultipartStreamParser:
    """Incremental parser for the endless multipart/mixed alert stream."""

    def __init__(self, boundary: bytes) -> None:
        self.delimiter = b"--" + boundary
        self.buffer = b""

    def feed(self, data: bytes):
        """Feed bytes, yield (headers, body) for each complete part."""

        self.buffer += data
        while True:
            start = self.buffer.find(self.delimiter)
            if start == -1:
                # keep the tail in case the delimiter is split across reads
                self.buffer = self.buffer[-len(self.delimiter) :]
                return
            header_end = self.buffer.find(b"\r\n\r\n", start)
            if header_end == -1:
                self.buffer = self.buffer[start:]
                return

            header_block = self.buffer[start + len(self.delimiter) : header_end].decode("latin-1")
            headers = {}
            for line in header_block.split("\r\n"):
                if ":" in line:
                    key, value = line.split(":", 1)
                    headers[key.strip().lower()] = value.strip()

            body_start = header_end + 4
            length = headers.get("content-length")
            if length and length.isdigit():
                body_end = body_start + int(length)
                if len(self.buffer) < body_end:
                    self.buffer = self.buffer[start:]
                    return
            else:
                next_start = self.buffer.find(self.delimiter, body_start)
                if next_start == -1:
                    self.buffer = self.buffer[start:]
                    return
                body_end = next_start
                # drop the CRLF preceding the next delimiter
                while body_end > body_start and self.buffer[body_end - 1 : body_end] in (b"\r", b"\n"):
                    body_end -= 1

            body = self.buffer[body_start:body_end]
            self.buffer = self.buffer[body_end:]
            yield headers, body


def open_stream(args: argparse.Namespace, password: str):
    """Open the alert stream with digest/basic authentication."""

    url = f"{args.scheme}://{args.host}{ALERT_STREAM_PATH}"
    password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    password_mgr.add_password(None, url, args.user, password)
    handlers = [
        urllib.request.HTTPDigestAuthHandler(password_mgr),
        urllib.request.HTTPBasicAuthHandler(password_mgr),
    ]
    if args.scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(url, timeout=args.timeout)


def run(args: argparse.Namespace, password: str) -> None:
    """Read the stream and save every alert."""

    out_dir = Path(args.output) / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Saving captures to: {out_dir.resolve()}")

    counter = 0
    last_xml_name = "unknown"
    while True:
        try:
            print(f"Connecting to {args.scheme}://{args.host}{ALERT_STREAM_PATH} ...")
            with open_stream(args, password) as response:
                content_type = response.headers.get("Content-Type", "")
                match = re.search(r'boundary="?([^";]+)"?', content_type)
                boundary = (match.group(1) if match else "boundary").encode()
                print(f"Connected. Content-Type: {content_type}")
                print("Waiting for events... walk in front of the camera. Ctrl+C to stop.\n")
                parser = MultipartStreamParser(boundary)

                while True:
                    chunk = response.read1(65536) if hasattr(response, "read1") else response.read(4096)
                    if not chunk:
                        raise ConnectionError("stream closed by device")
                    for headers, body in parser.feed(chunk):
                        part_type = headers.get("content-type", "").lower()
                        stamp = datetime.now().strftime("%H%M%S_%f")[:-3]

                        if "xml" in part_type or body.lstrip().startswith(b"<"):
                            event_type, event_state, summary = summarize_alert(body)
                            is_heartbeat = event_type.lower() in HEARTBEAT_EVENTS and event_state == "inactive"
                            if is_heartbeat and not args.include_heartbeat:
                                continue
                            counter += 1
                            safe_type = re.sub(r"[^a-zA-Z0-9_-]", "_", event_type)
                            last_xml_name = f"{counter:04d}_{stamp}_{safe_type}_{event_state}"
                            (out_dir / f"{last_xml_name}.xml").write_bytes(body)
                            print(f"[{datetime.now():%H:%M:%S}] #{counter} {summary}")
                        elif part_type.startswith("image/"):
                            ext = part_type.split("/", 1)[1].split(";")[0].replace("jpg", "jpeg")
                            name = f"{last_xml_name}_img_{stamp}.{ext}"
                            (out_dir / name).write_bytes(body)
                            print(f"    + image saved: {name} ({len(body)} bytes)")
                        elif body.strip():
                            name = f"{last_xml_name}_part_{stamp}.bin"
                            (out_dir / name).write_bytes(body)
                            print(f"    + other part ({part_type or 'no content-type'}) saved: {name}")
        except KeyboardInterrupt:
            print(f"\nStopped. {counter} alerts saved in {out_dir.resolve()}")
            return
        except urllib.error.HTTPError as ex:
            if ex.code in (401, 403):
                print(f"Authentication/permission error ({ex.code}). Check user/password and that the user has 'Remote: Notify Surveillance Center / Alarm' permission.")
                return
            print(f"HTTP error {ex.code}: {ex.reason}. Retrying in 5 s...")
        except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, OSError) as ex:
            print(f"Connection problem: {ex}. Retrying in 5 s...")
        time.sleep(5)


def main() -> None:
    """Entry point."""

    arg_parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    arg_parser.add_argument("--host", default="192.168.1.7", help="Camera/NVR IP or hostname (default: 192.168.1.7)")
    arg_parser.add_argument("--user", default="admin", help="Username (default: admin)")
    arg_parser.add_argument("--scheme", choices=("http", "https"), default="http")
    arg_parser.add_argument("--output", default="captures", help="Output folder (default: ./captures)")
    arg_parser.add_argument("--timeout", type=int, default=120, help="Socket read timeout in seconds")
    arg_parser.add_argument("--include-heartbeat", action="store_true", help="Also save 'videoloss inactive' heartbeats")
    args = arg_parser.parse_args()

    password = os.environ.get("HIKVISION_PASSWORD") or getpass.getpass(f"Password for {args.user}@{args.host}: ")
    try:
        run(args, password)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
