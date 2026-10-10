# Hikvision Next

[![GitHub release (latest by date)](https://img.shields.io/github/v/release/depklyon/hikvision_next?style=flat-square&color=blue)](https://github.com/depklyon/hikvision_next/releases)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom%20Repository-orange.svg?style=flat-square)](https://github.com/hacs/integration)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1+-blue.svg?style=flat-square&logo=home-assistant)](https://www.home-assistant.io/)
[![Upstream](https://img.shields.io/badge/Forked%20From-maciej--or%2Fhikvision__next-lightgrey?style=flat-square&logo=github)](https://github.com/maciej-or/hikvision_next)

A high-performance Home Assistant integration for **Hikvision NVRs, DVRs, and IP Cameras**. It provides instant push-based alarm event handling, video streams, snapshot management, and an interactive event browser.

---

> [!NOTE]
> ### 🌟 Credits & Attribution
> This project is an enhanced fork of the original [**`hikvision_next`**](https://github.com/maciej-or/hikvision_next) integration created and maintained by [**Maciej (@maciej-or)**](https://github.com/maciej-or).
> 
> Huge thanks to Maciej for building the core ISAPI client and laying the foundation for Hikvision integration in Home Assistant! If you appreciate the original work, consider supporting Maciej on [Ko-fi](https://ko-fi.com/maciejor).
>
> **What's added in this fork (`depklyon/hikvision_next`):**
> * 🎞️ **Interactive Event Timeline Panel**: Dedicated sidebar browser with filmstrip, target filters, HUD overlay, and image export.
> * 🎯 **Target-Specific Binary Sensors & Triggers**: Separate entities and device triggers for **Human**, **Vehicle**, and **Movement**.
> * 📸 **Multi-Image Capture & Retention**: Rolling snapshot buffers and configurable automatic retention pruning (days/count).
> * 🔒 **Security Hardening**: Path traversal protection, admin-only deletion controls, DOM XSS sanitization, and request payload size enforcement.
> * ⚙️ **Centralized Global Settings**: Single entry point for shared integration options across all devices.

---

## ⚡ Key Features

### 🔔 Real-Time Event Detection & Automations
* **100% Push-Based (Zero Polling)**: Device alarms hit Home Assistant via ISAPI HTTP notifications instantly (< 20 ms).
* **Target Classification**: Dedicated sensors and native device automation triggers for **Human** and **Vehicle** detections (AcuSense / Smart events).
* **Event Bus & Dispatcher**: Emits `hikvision_event` on the Home Assistant bus for advanced YAML automations.
* **Auto-Resetting Binary Sensors**: Reliable state handling even when cameras omit an explicit "inactive" message.

### 🖼️ Media & Event Timeline
* **Sidebar Timeline Panel**: Browse historical event snapshots grouped by camera and date, complete with zoom, keyboard navigation, and full-resolution download.
* **Smart Snapshots**: Receives attached multipart camera snapshots or falls back to live snapshot capture in the background.
* **Configurable Retention**: Control rolling buffer size and disk retention days (`/media/hikvision_next`).

### 🎛️ Device Control & Monitoring
* **Camera Entities**: High-definition main streams and lightweight sub-streams.
* **Arming Switches**: Arm or disarm individual detection events (Motion, Intrusion, Line Crossing, etc.) directly from Home Assistant.
* **Hardware Relays & PIR**: Control NVR alarm output ports and monitor physical PIR inputs.
* **Diagnostics & Health**: Real-time HDD/NAS health tracking and holiday recording schedule toggles.

---

## 📡 Supported Event Types

| Event | Description | Target Classification |
| :--- | :--- | :---: |
| **Motion Detection** | Standard pixel motion | Human / Vehicle / Movement |
| **Field Detection** | Intrusion area monitoring | Human / Vehicle |
| **Line Crossing** | Boundary tripwire crossing | Human / Vehicle |
| **Region Entrance / Exit** | Zone entry / departure | Human / Vehicle |
| **Face Detection** | Facial recognition / capture | — |
| **Video Tampering** | Lens obstruction / spray | — |
| **Video Loss / Scene Change** | Signal drops and camera angle shifts | — |
| **Hardware I/O & PIR** | Physical alarm inputs and PIR sensors | — |

> [!IMPORTANT]
> For Home Assistant to receive events, you must enable **Notify Surveillance Center** under **Linkage Action** in your camera/NVR web configuration for each desired event.

---

## 📸 Preview

### IP Camera Device View
![IP Camera](assets/ipcam.jpg "IP Camera device view")

### NVR Device View
![NVR](assets/nvr.jpg "NVR device view")

---

## 🚀 Installation

### Option 1: Via HACS (Recommended)

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=depklyon&repository=hikvision_next&category=integration)

1. Open **HACS** > **Integrations** > Three dots menu (top right) > **Custom repositories**.
2. Add `https://github.com/depklyon/hikvision_next` with Category **Integration**.
3. Search for **Hikvision NVR / IP Camera** and click **Download**.
4. Restart Home Assistant.
5. In Home Assistant, go to **Settings** > **Devices & Services** > **Add Integration**, search for **Hikvision NVR / IP Camera**, and follow the setup wizard.

### Option 2: Manual Installation

1. Download the latest release from the [Releases](https://github.com/depklyon/hikvision_next/releases) page.
2. Copy the `custom_components/hikvision_next` directory into your Home Assistant `config/custom_components/` folder.
3. Restart Home Assistant and add the integration via **Settings** > **Devices & Services**.

---

## ⚙️ Configuration

You can configure options for this integration by navigating to **Settings > Devices & Services**, finding the Hikvision NVR / IP Camera integration, and clicking **Configure**:

* **Show Timeline Panel in Sidebar**: Toggle whether to show the Hikvision Event Timeline panel in the Home Assistant sidebar (enabled by default).
* **Image Retention Count**: Sets the maximum number of event snapshots to keep per event/channel on disk. Defaults to 1 (overwriting previous images). Higher values preserve images in a rolling buffer.
* **Image Retention Days**: Number of days to keep event images before automatic cleanup (defaults to 7 days). Set to 0 to disable age-based pruning.

---

## 📋 Hikvision Device Setup Checklist

Before adding your device, verify the following in the camera or NVR web interface:

- [x] **ISAPI Access**: Ensure ISAPI is enabled (**Network** > **Advanced Settings** > **Integration Protocol**).
- [x] **User Permissions**: Ensure the integration user has:
  - *Parameters Settings*
  - *Log Search / Interrogate Working Status*
  - *Live View*
  - *Notify Surveillance Center / Trigger Alarm Output*
- [x] **Linkage Action**: Check **Notify Surveillance Center** for every event you wish to receive in HA.
- [x] **Notifications Host**: Set the Alarm Server / HTTP Host to your Home Assistant IP (`/api/hikvision_next/event`). *(The integration can configure this automatically during setup if enabled).*

---

## 📦 Blueprints

Quickly deploy ready-to-use automations using these pre-built blueprints:

* **[Take Multiple Snapshots on Detection Event](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/depklyon/hikvision_next/blob/main/blueprints/take_pictures_on_motion_detection.yaml)**: Automatically captures a burst of images when an event fires.
* **[Display Sensor State on Hikvision Video](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https://github.com/depklyon/hikvision_next/blob/main/blueprints/display_sensor_state_on_hikvision_video.yaml)**: Renders live Home Assistant sensor data (e.g. temperature, gate status) directly as an OSD text overlay on your camera stream.

---

## 🔍 Troubleshooting & Diagnostics

If an event is not triggering or a device fails to connect:

1. **Download Diagnostics**: Go to the device page in Home Assistant (**Settings** > **Devices & Services** > **Hikvision NVR / IP Camera** > **Your Device**) and click **Download Diagnostics**. Sensitive data (passwords, serials, IPs) is automatically redacted.
2. **Enable Debug Logging**: Add the following to your `configuration.yaml` and restart Home Assistant:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.hikvision_next: debug
   ```
3. Check the logs under **Settings** > **System** > **Logs**.

---

<details>
<summary><b>📜 Tested Device Models (Click to expand)</b></summary>

### NVRs
* Annke N46PCK
* DS-7108NI-Q1/8P
* DS-7608NI-I2 / DS-7608NI-I2/8P
* DS-7608NXI-I2/8P/S / DS-7608NXI-K1/8P
* DS-7616NI-E2/16P / DS-7616NI-I2/16P / DS-7616NI-Q2 / DS-7616NI-Q2/16P / DS-7616NXI-I2/16P/S
* DS-7716NI-I4/16P / DS-7732NI-M4
* ERI-K104-P4

### DVRs
* iDS-7204HUHI-M1/FA/A / iDS-7204HUHI-M1/P
* iDS-7208HQHI-M1(A)/S(C)

### IP Cameras
* Annke C800 (I91BM)
* DS-2CD1323G2-LIU
* DS-2CD2047G2-LU/SL / DS-2CD2047G2H-LIU / DS-2CD2083G2-I / DS-2CD2087G2-LU
* DS-2CD2146G2-ISU / DS-2CD2155FWD-I
* DS-2CD2346G2-IU / DS-2CD2386G2-IU / DS-2CD2387G2-LU / DS-2CD2387G2H-LISU/SL
* DS-2CD2425FWD-IW / DS-2CD2443G0-IW
* DS-2CD2532F-IWS / DS-2CD2546G2-IS
* DS-2CD2747G2-LZS / DS-2CD2785G1-IZS / DS-2CD2H46G2-IZS (C)
* DS-2CD2T46G2-ISU/SL / DS-2CD2T87G2-L / DS-2CD2T87G2P-LSU/SL
* DS-2DE4425IW-DE (PTZ) / DS-2SE4C425MWG-E/26
</details>

---

## 📄 License & Attribution

This project is licensed under the [MIT License](LICENSE).
* Original project by [Maciej (@maciej-or)](https://github.com/maciej-or/hikvision_next).
* Enhancements and maintenance by [depklyon](https://github.com/depklyon).
