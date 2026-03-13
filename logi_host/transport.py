"""Low-level HID device access via ctypes binding to libhidapi (Linux only).

Opens the Logitech Unifying/Bolt receiver and provides synchronous read/write
of raw 20-byte HID++ long-format messages.
"""

from __future__ import annotations

import ctypes
import logging
from dataclasses import dataclass

from .constants import (
    ALL_RECEIVER_PIDS,
    BOLT_PID,
    HIDPP_USAGE_LONG,
    HIDPP_USAGE_PAGE,
    LOGITECH_VENDOR_ID,
    MAX_READ_SIZE,
    UNIFYING_PIDS,
)

log = logging.getLogger(__name__)


class TransportError(Exception):
    """Raised on HID I/O failure."""


# -- Load libhidapi -----------------------------------------------------------

_LIB_NAMES = [
    "libhidapi-hidraw.so.0",  # Preferred: hidraw backend (non-exclusive)
    "libhidapi-hidraw.so",
    "libhidapi-libusb.so.0",
    "libhidapi-libusb.so",
    "libhidapi.so.0",
    "libhidapi.so",
]

_lib: ctypes.CDLL | None = None
for _name in _LIB_NAMES:
    try:
        _lib = ctypes.CDLL(_name)
        log.debug("hidapi: loaded %s", _name)
        break
    except OSError:
        continue

if _lib is None:
    raise ImportError(
        "Cannot load hidapi library. "
        "Install it with: apk add hidapi (Alpine) or apt install libhidapi-hidraw0 (Debian)"
    )

# -- Initialize hidapi ---------------------------------------------------------

_lib.hid_init.restype = ctypes.c_int
_lib.hid_init.argtypes = []
_lib.hid_init()


# -- struct hid_device_info (mirrors hidapi.h) ---------------------------------


class _DeviceInfo(ctypes.Structure):
    pass  # Forward declaration for self-referential struct


_DeviceInfo._fields_ = [
    ("path", ctypes.c_char_p),
    ("vendor_id", ctypes.c_ushort),
    ("product_id", ctypes.c_ushort),
    ("serial_number", ctypes.c_wchar_p),
    ("release_number", ctypes.c_ushort),
    ("manufacturer_string", ctypes.c_wchar_p),
    ("product_string", ctypes.c_wchar_p),
    ("usage_page", ctypes.c_ushort),
    ("usage", ctypes.c_ushort),
    ("interface_number", ctypes.c_int),
    ("next", ctypes.POINTER(_DeviceInfo)),
]

# -- hidapi function signatures ------------------------------------------------

_lib.hid_enumerate.restype = ctypes.POINTER(_DeviceInfo)
_lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]

_lib.hid_free_enumeration.restype = None
_lib.hid_free_enumeration.argtypes = [ctypes.POINTER(_DeviceInfo)]

_lib.hid_open_path.restype = ctypes.c_void_p
_lib.hid_open_path.argtypes = [ctypes.c_char_p]

_lib.hid_close.restype = None
_lib.hid_close.argtypes = [ctypes.c_void_p]

_lib.hid_read_timeout.restype = ctypes.c_int
_lib.hid_read_timeout.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_size_t,
    ctypes.c_int,
]

_lib.hid_write.restype = ctypes.c_int
_lib.hid_write.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_ubyte),
    ctypes.c_size_t,
]

_lib.hid_error.restype = ctypes.c_wchar_p
_lib.hid_error.argtypes = [ctypes.c_void_p]


def _hid_err(dev: int | None = None) -> str:
    msg = _lib.hid_error(dev)
    return msg if msg else "unknown hidapi error"


# -- Device info dataclass -----------------------------------------------------


@dataclass
class HidDeviceInfo:
    path: bytes
    vid: int
    pid: int
    usage_page: int
    usage: int

    @property
    def receiver_type(self) -> str:
        if self.pid == BOLT_PID:
            return "bolt"
        if self.pid in UNIFYING_PIDS:
            return "unifying"
        return "unknown"


# -- Enumeration ---------------------------------------------------------------


def enumerate_receivers(
    vendor_id: int = LOGITECH_VENDOR_ID,
    receiver_type: str | None = None,
) -> list[HidDeviceInfo]:
    """Find all Logitech Unifying/Bolt receivers connected to the system.

    Filters by vendor ID, known receiver PIDs, HID++ usage page (0xFF00),
    and long HID++ usage (0x0002).

    Args:
        vendor_id: USB vendor ID to filter (default: Logitech 0x046D).
        receiver_type: Optional filter — "unifying" or "bolt". None returns all.

    Returns:
        List of HidDeviceInfo for matching receivers.
    """
    allowed_pids = ALL_RECEIVER_PIDS
    if receiver_type == "unifying":
        allowed_pids = UNIFYING_PIDS
    elif receiver_type == "bolt":
        allowed_pids = (BOLT_PID,)

    head = _lib.hid_enumerate(vendor_id, 0)
    result: dict[bytes, HidDeviceInfo] = {}
    node = head

    while node:
        info = node.contents
        node = info.next
        path = info.path
        pid = info.product_id

        if pid not in allowed_pids:
            continue
        if path in result:
            continue
        if info.usage_page != HIDPP_USAGE_PAGE:
            continue
        if info.usage != HIDPP_USAGE_LONG:
            continue

        result[path] = HidDeviceInfo(
            path=path,
            vid=info.vendor_id,
            pid=pid,
            usage_page=info.usage_page,
            usage=info.usage,
        )
        log.info("Found %s receiver at %s (pid=0x%04X)", result[path].receiver_type, path, pid)

    _lib.hid_free_enumeration(head)
    return list(result.values())


# -- HIDTransport --------------------------------------------------------------


class HIDTransport:
    """Owns one open HID device handle for a Logitech receiver.

    Provides synchronous read/write of raw HID++ packets.
    """

    def __init__(self, path: bytes, receiver_type: str, pid: int) -> None:
        self.path = path
        self.receiver_type = receiver_type
        self.pid = pid
        self._dev: int | None = _lib.hid_open_path(path)
        if not self._dev:
            raise TransportError(f"Failed to open {path}: {_hid_err()}")
        log.info("Opened %s receiver (pid=0x%04X) at %s", receiver_type, pid, path)

    def read(self, timeout: int = 500) -> bytes | None:
        """Read one HID packet with timeout (ms).

        Returns raw bytes on success, None on timeout.
        Raises TransportError on device error.
        """
        if self._dev is None:
            raise TransportError("read on closed transport")
        buf = (ctypes.c_ubyte * MAX_READ_SIZE)()
        n = _lib.hid_read_timeout(self._dev, buf, MAX_READ_SIZE, timeout)
        if n < 0:
            raise TransportError(f"hid_read_timeout failed: {_hid_err(self._dev)}")
        return bytes(buf[:n]) if n > 0 else None

    def write(self, msg: bytes) -> None:
        """Write one HID packet (first byte must be the report ID)."""
        buf = (ctypes.c_ubyte * len(msg))(*msg)
        n = _lib.hid_write(self._dev, buf, len(msg))
        if n < 0:
            raise TransportError(f"hid_write failed: {_hid_err(self._dev)}")

    def close(self) -> None:
        """Close the HID device handle."""
        if self._dev is not None:
            _lib.hid_close(self._dev)
            self._dev = None
            log.info("Closed transport at %s", self.path)

    def __repr__(self) -> str:
        return f"HIDTransport({self.receiver_type!r}, pid=0x{self.pid:04X}, path={self.path!r})"
