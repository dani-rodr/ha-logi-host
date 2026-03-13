# ha-logi-host

Home Assistant add-on that switches your Logitech mouse between hosts on demand. Plug a Unifying or Bolt USB receiver into your server, pair your mouse, and control which computer it connects to — from HA automations, dashboard buttons, or scripts.

## How It Works

```
┌─ Home Assistant VM ──────────────────────────────────┐
│                                                      │
│   ┌─ ha-logi-host add-on ─────────────────────────┐  │
│   │  HID++ 2.0 protocol ←→ Unifying/Bolt receiver │  │
│   │  MQTT ←→ Home Assistant                        │  │
│   └────────────────────────────────────────────────┘  │
│                                                      │
│   Home Assistant creates a "Mouse Host" select       │
│   entity with options: 1, 2, 3                       │
│                                                      │
└──────────────────────────────────────────────────────┘
         │ USB passthrough
    ┌────┴────┐
    │ Logi    │   Sends CHANGE_HOST command to mouse
    │ Receiver│   Mouse switches to host 1, 2, or 3
    └─────────┘
```

The add-on opens the receiver via `libhidapi`, probes all 6 device slots to find your mouse, then listens for MQTT commands from Home Assistant. When you select a host (1, 2, or 3), it sends a HID++ `CHANGE_HOST` command through the receiver. The mouse switches immediately — even to hosts connected via Bluetooth.

## Prerequisites

- **Logitech mouse** with Easy-Switch (e.g., MX Master 3, MX Anywhere 3, MX Ergo)
- **Logitech Unifying or Bolt USB receiver** (the small USB dongle)
- **Home Assistant** with the **Mosquitto MQTT broker** add-on installed
- **USB passthrough** from your host (e.g., Proxmox) to the HA VM

## Setup

### 1. Pair the mouse to the receiver

The mouse needs to be paired to the Unifying/Bolt receiver on one of its Easy-Switch slots (e.g., slot 3). This is a one-time step.

**Option A — Windows (Logitech Unifying Software):**
1. Plug the receiver into any Windows PC
2. Download and run [Logitech Unifying Software](https://support.logi.com/hc/en-us/articles/360025297913)
3. Press the Easy-Switch button on the mouse bottom to select slot 3
4. Follow the pairing wizard
5. Unplug the receiver when done

**Option B — Linux (Solaar):**
1. Install Solaar: `sudo apt install solaar`
2. Plug in the receiver
3. Run `solaar pair` and press the Easy-Switch button on the mouse

### 2. USB passthrough (Proxmox)

Pass the Unifying/Bolt receiver to your Home Assistant VM:

1. In Proxmox, go to your HA VM > Hardware > Add > USB Device
2. Select the Logitech receiver (vendor `046d`)
3. Restart the VM
4. Verify the device appears inside HA: check the add-on logs after installation

### 3. Install the add-on

1. In Home Assistant, go to **Settings > Add-ons > Add-on Store**
2. Click the three-dot menu (top right) > **Repositories**
3. Add: `https://github.com/dani-rodr/ha-logi-host`
4. Find **Logi Host Switch** in the store and click **Install**
5. Configure the receiver type (default: `unifying`)
6. Start the add-on

### 4. Use it

Once the add-on starts and finds your mouse, a **"Mouse Host"** select entity appears automatically in Home Assistant (via MQTT auto-discovery).

**Dashboard:** Add the entity to any dashboard as a dropdown or button card.

**Automation example:**
```yaml
automation:
  - alias: "Switch mouse to Work PC"
    trigger:
      - platform: state
        entity_id: input_boolean.work_mode
        to: "on"
    action:
      - service: select.select_option
        target:
          entity_id: select.mx_master_3_host
        data:
          option: "1"
```

**Script example:**
```yaml
script:
  switch_mouse_to_host_2:
    sequence:
      - service: select.select_option
        target:
          entity_id: select.mx_master_3_host
        data:
          option: "2"
```

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `receiver_type` | `unifying` | Receiver type: `unifying` or `bolt` |
| `log_level` | `info` | Log verbosity: `debug`, `info`, `warning`, `error` |

## HA Entities Created

| Entity | Type | Description |
|--------|------|-------------|
| `select.<mouse_name>_host` | Select | Dropdown to pick host 1, 2, or 3 |
| `sensor.receiver_status` | Sensor | Shows `online` or `offline` |

## Limitations

- **Receiver required** — The mouse must be paired to a Unifying/Bolt receiver on at least one slot. You can switch *to* any host (including Bluetooth-paired ones), but the command is sent through the receiver.
- **Can't switch back from Bluetooth** — If the mouse is on a Bluetooth host, you need to physically press Easy-Switch to return to the receiver host. Once on the receiver host, the add-on can switch it again.
- **One mouse** — Currently discovers the first mouse on the receiver. Multiple mice on the same receiver are not yet supported.

## How It Works (Technical)

The add-on communicates with the mouse using the **HID++ 2.0** protocol over the Logitech receiver:

1. Opens the receiver's HID device (`/dev/hidraw*`) via `libhidapi`
2. Probes slots 1-6 using `DEVICE_TYPE_AND_NAME` (feature `0x0005`)
3. Identifies the mouse by device type (3=Mouse, 4=Trackpad, 5=Trackball)
4. Resolves the `CHANGE_HOST` feature index (feature `0x1814`) via IRoot
5. On MQTT command: sends a 20-byte `SetCurrentHost` message — fire-and-forget

## Credits

HID++ protocol implementation inspired by [CleverSwitch](https://github.com/MikalaiBarysevich/CleverSwitch) and the [Solaar](https://github.com/pwr-Solaar/Solaar) project.

## License

GPL-3.0
