# ha-logi-host — Developer Guide

## Project Overview

Home Assistant add-on that sends HID++ 2.0 `CHANGE_HOST` commands to a Logitech mouse
through a Unifying/Bolt USB receiver. Controlled via MQTT — HA automations, dashboard
buttons, or scripts can switch the mouse between hosts 1, 2, or 3.

## Tech Stack

- **Python 3.10+** — runs inside HA add-on Alpine container
- `libhidapi` — HID device access (loaded via ctypes, no Python `hid` package)
- `paho-mqtt` — MQTT client for HA integration
- `pytest` + `pytest-mock` + `pytest-cov` — testing (coverage threshold: 80%)

## Directory Structure

```
ha-logi-host/
├── config.yaml              # HA add-on manifest (name, arch, devices, mqtt dep)
├── Dockerfile               # Alpine + libhidapi + Python + paho-mqtt
├── run.sh                   # Entry point — reads MQTT creds via bashio, runs Python
├── requirements.txt         # paho-mqtt
├── pyproject.toml           # pytest config, coverage settings, dev dependencies
├── logi_host/
│   ├── __init__.py          # Package version
│   ├── __main__.py          # python -m logi_host entry point
│   ├── constants.py         # HID++ magic numbers: PIDs, feature codes, device types
│   ├── transport.py         # ctypes binding to libhidapi: enumerate, open, read, write
│   ├── protocol.py          # HID++ 2.0: request/reply, feature resolution, CHANGE_HOST
│   ├── mqtt.py              # MQTT client: HA auto-discovery, command handling
│   └── main.py              # Main loop: discover → probe → MQTT serve → reconnect
└── tests/
    ├── conftest.py          # FakeTransport class and fixtures
    ├── test_constants.py    # Constant value verification
    ├── test_protocol.py     # Protocol functions with mocked transport
    ├── test_mqtt.py         # MQTT bridge with mocked paho client
    └── test_main.py         # Main loop lifecycle with mocked dependencies
```

## Development Commands

```bash
# Run all tests with coverage
pytest

# Run a specific test file
pytest tests/test_protocol.py -v

# Run with debug output
pytest -s --log-cli-level=DEBUG

# Check coverage report
pytest --cov-report=term-missing
```

## Architecture

### Dependency Direction

```
main.py → mqtt.py
main.py → protocol.py → transport.py → libhidapi (ctypes)
              ↑
          constants.py
```

- `transport.py` is the **only** module that touches libhidapi (via ctypes).
- `protocol.py` knows nothing about MQTT or "mouse host switching" — pure HID++ 2.0.
- `mqtt.py` knows nothing about HID++ — pure MQTT + HA auto-discovery.
- `main.py` orchestrates: discovery → probing → MQTT bridge → command dispatch.

### Threading Model

```
main.py:run()
├── Main thread: discovery loop + transport health check
└── paho MQTT thread: loop_start() — handles MQTT I/O + callbacks
```

- Main thread blocks on `transport.read(timeout=2000)` as a health check.
- MQTT callbacks (`_on_message`) fire on the paho background thread.
- `_handle_host_switch` is called from the MQTT thread — sends `CHANGE_HOST` via transport.

### Message Flow

1. HA user selects host 1/2/3 via the select entity
2. HA publishes to `ha_logi_host/mouse/host/set`
3. `MQTTBridge._on_message` validates and calls `on_host_switch(host)`
4. `_handle_host_switch` in main.py sends `send_change_host(transport, slot, feat_idx, host-1)`
5. Transport writes 20-byte HID++ message to receiver
6. Mouse switches immediately (fire-and-forget, no reply)

## Key Constants

| Thing | Value |
|---|---|
| Logitech vendor ID | `0x046D` |
| Bolt receiver PID | `0xC548` |
| Unifying receiver PIDs | `0xC52B`, `0xC532` |
| CHANGE_HOST feature code | `0x1814` |
| DEVICE_TYPE_AND_NAME feature | `0x0005` |
| CHANGE_HOST_FN_SET | `0x10` |
| SW_ID | `0x08` |
| Mouse device types | 3 (Mouse), 4 (Trackpad), 5 (Trackball) |

## MQTT Topics

| Topic | Direction | Purpose |
|-------|-----------|---------|
| `ha_logi_host/mouse/host/state` | Publish | Current host (1, 2, or 3) |
| `ha_logi_host/mouse/host/set` | Subscribe | Command: switch to host X |
| `ha_logi_host/mouse/status` | Publish | Availability: `online` / `offline` |
| `homeassistant/select/ha_logi_host_mouse_host/config` | Publish | HA auto-discovery for select entity |
| `homeassistant/sensor/ha_logi_host_mouse_status/config` | Publish | HA auto-discovery for status sensor |

## Testing Conventions

- All HID I/O is mocked — tests never open real devices.
- `FakeTransport` in conftest.py captures writes and replays pre-queued reads.
- `transport.py` and `__main__.py` are excluded from coverage (hardware I/O and entry point).
- Coverage threshold: **80%** (enforced in `pyproject.toml`).
- Protocol tests use layered patching: low-level tests mock transport, high-level tests mock `protocol.request`.

## Error Handling

| Error | Behavior |
|-------|----------|
| `TransportError` | Logged, transport closed, main loop re-discovers |
| No receiver found | Retries every 5 seconds |
| No mouse on receiver | Retries every 5 seconds |
| MQTT disconnect | paho auto-reconnects; Last Will publishes "offline" |
| `send_change_host` failure | Raises `TransportError`, triggers reconnect |
| Invalid MQTT command | Logged as warning, ignored |

## Config (HA Add-on Options)

Configured via HA UI, passed as environment variables by `run.sh`:

| Env Var | Add-on Option | Default |
|---------|--------------|---------|
| `MQTT_HOST` | (from Mosquitto service) | `localhost` |
| `MQTT_PORT` | (from Mosquitto service) | `1883` |
| `MQTT_USER` | (from Mosquitto service) | — |
| `MQTT_PASS` | (from Mosquitto service) | — |
| `RECEIVER_TYPE` | `receiver_type` | `unifying` |
| `LOG_LEVEL` | `log_level` | `info` |
