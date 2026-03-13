"""HID++ 2.0 protocol implementation — simplified for host switching.

Provides:
  - request/reply with SW_ID matching
  - Feature index resolution via IRoot (0x0000)
  - Device type/name queries via x0005
  - CHANGE_HOST command (fire-and-forget)

Protocol references:
  - Logitech HID++ 2.0 specification
  - CleverSwitch project (github.com/MikalaiBarysevich/CleverSwitch)
"""

from __future__ import annotations

import logging
import struct
from time import time

from .constants import (
    CHANGE_HOST_FN_SET,
    FEATURE_CHANGE_HOST,
    FEATURE_DEVICE_TYPE_AND_NAME,
    FEATURE_ROOT,
    MAX_SLOT,
    MIN_SLOT,
    MOUSE_DEVICE_TYPES,
    MSG_DJ_LEN,
    MSG_LONG_LEN,
    MSG_SHORT_LEN,
    REPORT_DJ,
    REPORT_LONG,
    REPORT_SHORT,
    SW_ID,
)
from .transport import HIDTransport, TransportError

log = logging.getLogger(__name__)

# Expected message lengths by report ID
_MSG_LENGTHS = {
    REPORT_SHORT: MSG_SHORT_LEN,
    REPORT_LONG: MSG_LONG_LEN,
    REPORT_DJ: MSG_DJ_LEN,
}


# -- Internal helpers ----------------------------------------------------------


def _pack_params(*params) -> bytes:
    """Serialize parameters into bytes for HID++ message payload."""
    parts = []
    for p in params:
        if isinstance(p, int):
            parts.append(struct.pack("B", p))
        else:
            parts.append(bytes(p))
    return b"".join(parts)


def _build_msg(devnumber: int, request_id: int, params: bytes) -> bytes:
    """Assemble a 20-byte HID++ long message (report 0x11).

    Always uses long format — HID++ 2.0 responses are always long.
    """
    data = struct.pack("!H", request_id) + params
    return struct.pack("!BB18s", REPORT_LONG, devnumber, data)


def _is_relevant(raw: bytes) -> bool:
    """True if raw bytes are a well-formed HID++ or DJ message."""
    return bool(raw) and len(raw) >= 3 and raw[0] in _MSG_LENGTHS and len(raw) == _MSG_LENGTHS[raw[0]]


# -- Request / reply -----------------------------------------------------------


def request(
    transport: HIDTransport,
    devnumber: int,
    request_id: int,
    *params: int,
    timeout: int = 500,
) -> bytes | None:
    """Send a HID++ request and wait for the matching reply.

    Returns payload bytes starting at byte 4 of the raw message (after the
    2-byte request_id echo). Returns None on timeout or error.

    SW_ID is OR'd into the low nibble of request_id so our replies are
    distinguishable from notifications (sw_id=0).
    """
    request_id = (request_id & 0xFFF0) | SW_ID

    params_bytes = _pack_params(*params)
    request_data = struct.pack("!H", request_id) + params_bytes
    msg = _build_msg(devnumber, request_id, params_bytes)

    log.debug("-> dev=0x%02X [%s]", devnumber, msg.hex())

    try:
        transport.write(msg)
    except Exception as e:
        raise TransportError(f"write failed: {e}") from e

    deadline = time() + timeout / 1000
    while time() < deadline:
        try:
            raw = transport.read(timeout)
        except Exception as e:
            raise TransportError(f"read failed: {e}") from e

        if not raw or not _is_relevant(raw):
            continue

        log.debug("<- dev=0x%02X [%s]", raw[1], raw.hex())

        rdev = raw[1]
        rdata = raw[2:]

        # Accept reply from this device (Bluetooth may XOR devnumber)
        if rdev != devnumber and rdev != (devnumber ^ 0xFF):
            continue

        # HID++ 1.0 error: sub_id=0x8F, next 2 bytes mirror our request
        if raw[0] == REPORT_SHORT and rdata[0:1] == b"\x8f" and rdata[1:3] == request_data[:2]:
            log.debug("HID++ 1.0 error 0x%02X for request 0x%04X", rdata[3], request_id)
            return None

        # HID++ 2.0 error: sub_id=0xFF, next 2 bytes mirror our request
        if rdata[0:1] == b"\xff" and rdata[1:3] == request_data[:2]:
            log.debug("HID++ 2.0 error 0x%02X for request 0x%04X", rdata[3], request_id)
            return None

        # Successful reply: first 2 bytes match our request_id
        if rdata[:2] == request_data[:2]:
            return rdata[2:]

    log.debug("Timeout on request 0x%04X from device 0x%02X", request_id, devnumber)
    return None


# -- Feature operations --------------------------------------------------------


def resolve_feature_index(
    transport: HIDTransport,
    devnumber: int,
    feature_code: int,
) -> int | None:
    """Look up the feature table index for a feature code on a device.

    Sends an IRoot (0x0000) GetFeature request.
    Returns the feature index (1-255), or None if not supported / no device.
    """
    request_id = (FEATURE_ROOT << 8) | 0x00
    reply = request(
        transport,
        devnumber,
        request_id,
        feature_code >> 8,
        feature_code & 0xFF,
        0x00,
        timeout=500,
    )
    if reply and reply[0] != 0x00:
        return reply[0]
    return None


def get_device_type(
    transport: HIDTransport,
    devnumber: int,
    feat_idx: int,
) -> int | None:
    """Query x0005 getDeviceType() [function 2].

    Returns device type int (0=Keyboard, 3=Mouse, 4=Trackpad, 5=Trackball),
    or None on failure.
    """
    request_id = (feat_idx << 8) | 0x20  # function [2]
    reply = request(transport, devnumber, request_id, timeout=500)
    if reply:
        return reply[0]
    return None


def get_device_name(
    transport: HIDTransport,
    devnumber: int,
    feat_idx: int,
) -> str | None:
    """Query x0005 getDeviceNameCount + getDeviceName to read the full name.

    Returns marketing name (e.g. 'MX Master 3'), or None on failure.
    """
    # fn [0]: getDeviceNameCount — returns total name length
    reply = request(transport, devnumber, (feat_idx << 8) | 0x00, timeout=500)
    if not reply:
        return None
    name_len = reply[0]
    if name_len == 0:
        return None

    # fn [1]: getDeviceName(charIndex) — returns chunk starting at charIndex
    chars: list[int] = []
    while len(chars) < name_len:
        reply = request(transport, devnumber, (feat_idx << 8) | 0x10, len(chars), timeout=500)
        if not reply:
            break
        remaining = name_len - len(chars)
        chunk = reply[:remaining]
        if not chunk:
            break
        chars.extend(chunk)

    return bytes(chars).decode("utf-8", errors="replace") if chars else None


def send_change_host(
    transport: HIDTransport,
    devnumber: int,
    feature_idx: int,
    target_host: int,
) -> None:
    """Switch device to target_host (0-based). Fire-and-forget — no reply expected.

    The device disconnects immediately after receiving this command.
    """
    request_id = (feature_idx << 8) | (CHANGE_HOST_FN_SET & 0xF0) | SW_ID
    params = struct.pack("B", target_host)
    msg = _build_msg(devnumber, request_id, params)
    log.info("send_change_host -> dev=0x%02X host=%d [%s]", devnumber, target_host, msg.hex())
    try:
        transport.write(msg)
    except Exception as e:
        raise TransportError(f"send_change_host failed: {e}") from e


# -- High-level discovery ------------------------------------------------------


def find_mouse(transport: HIDTransport) -> tuple[int, str | None, int] | None:
    """Probe receiver slots 1-6 and find the first mouse-class device.

    Returns (slot, device_name, change_host_feat_idx) or None if no mouse found.
    """
    for slot in range(MIN_SLOT, MAX_SLOT + 1):
        log.debug("Probing slot %d...", slot)

        # Resolve x0005 (DEVICE_TYPE_AND_NAME) feature index
        feat_0005 = resolve_feature_index(transport, slot, FEATURE_DEVICE_TYPE_AND_NAME)
        if feat_0005 is None:
            log.debug("Slot %d: no device or no x0005 support", slot)
            continue

        # Check device type
        dtype = get_device_type(transport, slot, feat_0005)
        if dtype is None:
            log.debug("Slot %d: could not read device type", slot)
            continue

        if dtype not in MOUSE_DEVICE_TYPES:
            log.debug("Slot %d: device type %d (not a mouse)", slot, dtype)
            continue

        # Found a mouse — get its name
        name = get_device_name(transport, slot, feat_0005)
        log.info("Slot %d: found mouse '%s' (type=%d)", slot, name, dtype)

        # Resolve CHANGE_HOST feature
        ch_feat = resolve_feature_index(transport, slot, FEATURE_CHANGE_HOST)
        if ch_feat is None:
            log.warning("Slot %d: mouse '%s' does not support CHANGE_HOST (0x1814)", slot, name)
            continue

        log.info("Slot %d: mouse '%s' CHANGE_HOST feature at index 0x%02X", slot, name, ch_feat)
        return (slot, name, ch_feat)

    return None
