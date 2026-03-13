"""Tests for logi_host.constants — verify key protocol values."""

from logi_host.constants import (
    ALL_RECEIVER_PIDS,
    BOLT_PID,
    CHANGE_HOST_FN_SET,
    DEVICE_TYPE_KEYBOARD,
    DEVICE_TYPE_MOUSE,
    DEVICE_TYPE_TRACKBALL,
    DEVICE_TYPE_TRACKPAD,
    FEATURE_CHANGE_HOST,
    FEATURE_DEVICE_TYPE_AND_NAME,
    FEATURE_ROOT,
    HIDPP_USAGE_LONG,
    HIDPP_USAGE_PAGE,
    LOGITECH_VENDOR_ID,
    MAX_SLOT,
    MIN_SLOT,
    MOUSE_DEVICE_TYPES,
    MSG_LONG_LEN,
    MSG_SHORT_LEN,
    REPORT_LONG,
    REPORT_SHORT,
    SW_ID,
    UNIFYING_PIDS,
)


def test_logitech_vendor_id():
    assert LOGITECH_VENDOR_ID == 0x046D


def test_bolt_pid():
    assert BOLT_PID == 0xC548


def test_unifying_pids():
    assert 0xC52B in UNIFYING_PIDS
    assert 0xC532 in UNIFYING_PIDS


def test_all_receiver_pids_includes_bolt_and_unifying():
    assert BOLT_PID in ALL_RECEIVER_PIDS
    for pid in UNIFYING_PIDS:
        assert pid in ALL_RECEIVER_PIDS


def test_hidpp_usage_page():
    assert HIDPP_USAGE_PAGE == 0xFF00


def test_hidpp_usage_long():
    assert HIDPP_USAGE_LONG == 0x0002


def test_report_ids():
    assert REPORT_SHORT == 0x10
    assert REPORT_LONG == 0x11


def test_message_lengths():
    assert MSG_SHORT_LEN == 7
    assert MSG_LONG_LEN == 20


def test_sw_id_has_bit3_set():
    """SW_ID must have bit 3 set to distinguish from notifications (sw_id=0)."""
    assert SW_ID >= 0x08
    assert SW_ID & 0x08 != 0


def test_feature_codes():
    assert FEATURE_ROOT == 0x0000
    assert FEATURE_DEVICE_TYPE_AND_NAME == 0x0005
    assert FEATURE_CHANGE_HOST == 0x1814


def test_change_host_fn_set():
    assert CHANGE_HOST_FN_SET == 0x10


def test_device_type_values():
    assert DEVICE_TYPE_KEYBOARD == 0
    assert DEVICE_TYPE_MOUSE == 3
    assert DEVICE_TYPE_TRACKPAD == 4
    assert DEVICE_TYPE_TRACKBALL == 5


def test_mouse_device_types_contains_all_mouse_class():
    assert DEVICE_TYPE_MOUSE in MOUSE_DEVICE_TYPES
    assert DEVICE_TYPE_TRACKPAD in MOUSE_DEVICE_TYPES
    assert DEVICE_TYPE_TRACKBALL in MOUSE_DEVICE_TYPES
    assert DEVICE_TYPE_KEYBOARD not in MOUSE_DEVICE_TYPES


def test_slot_range():
    assert MIN_SLOT == 1
    assert MAX_SLOT == 6
    assert MIN_SLOT < MAX_SLOT
