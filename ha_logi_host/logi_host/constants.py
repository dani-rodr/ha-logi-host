"""HID++ protocol constants for Logitech receiver communication.

Sources:
  - Logitech HID++ 2.0 specification
  - CleverSwitch project (github.com/MikalaiBarysevich/CleverSwitch)
"""

# -- Logitech USB identifiers -------------------------------------------------

LOGITECH_VENDOR_ID = 0x046D

# Receiver USB product IDs
BOLT_PID = 0xC548
UNIFYING_PIDS = (0xC52B, 0xC532)
ALL_RECEIVER_PIDS = (BOLT_PID,) + UNIFYING_PIDS

# HID usage page/usage for filtering the correct HID interface
HIDPP_USAGE_PAGE = 0xFF00  # Vendor-specific HID++ page
HIDPP_USAGE_LONG = 0x0002  # Long HID++ collection (report 0x11, 20 bytes)

# -- HID++ report IDs and message sizes ----------------------------------------

REPORT_SHORT = 0x10  # 7 bytes total
REPORT_LONG = 0x11  # 20 bytes total
REPORT_DJ = 0x20  # 15 bytes total

MSG_SHORT_LEN = 7
MSG_LONG_LEN = 20
MSG_DJ_LEN = 15
MAX_READ_SIZE = 32

# -- Addressing ----------------------------------------------------------------

# Software ID — lower nibble of function byte in requests.
# Bit 3 set (>= 0x08) so notifications (sw_id=0) are distinguishable.
SW_ID = 0x08

# -- HID++ 2.0 feature codes --------------------------------------------------

FEATURE_ROOT = 0x0000  # IRoot: look up feature index by code
FEATURE_DEVICE_TYPE_AND_NAME = 0x0005  # getDeviceType(), getDeviceName()
FEATURE_CHANGE_HOST = 0x1814  # SetCurrentHost — switch to another host
FEATURE_HOSTS_INFO = 0x1815  # getHostInfo — query current host, number of hosts

# CHANGE_HOST function code (upper nibble of function byte)
CHANGE_HOST_FN_SET = 0x10  # SetCurrentHost — fire-and-forget, no reply

# -- Device type constants (from x0005 getDeviceType) --------------------------

DEVICE_TYPE_KEYBOARD = 0
DEVICE_TYPE_MOUSE = 3
DEVICE_TYPE_TRACKPAD = 4
DEVICE_TYPE_TRACKBALL = 5

MOUSE_DEVICE_TYPES = (DEVICE_TYPE_MOUSE, DEVICE_TYPE_TRACKPAD, DEVICE_TYPE_TRACKBALL)

# -- Receiver slot range -------------------------------------------------------

MIN_SLOT = 1
MAX_SLOT = 6
