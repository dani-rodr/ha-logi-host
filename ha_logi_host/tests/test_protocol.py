"""Tests for logi_host.protocol — HID++ 2.0 message building, request/reply, and feature operations."""

from __future__ import annotations

import struct

import pytest

from logi_host.constants import (
    CHANGE_HOST_FN_SET,
    FEATURE_CHANGE_HOST,
    FEATURE_DEVICE_TYPE_AND_NAME,
    MSG_LONG_LEN,
    MSG_SHORT_LEN,
    REPORT_LONG,
    REPORT_SHORT,
    SW_ID,
)
from logi_host.protocol import (
    _build_msg,
    _is_relevant,
    _pack_params,
    find_mouse,
    get_current_host,
    get_device_name,
    get_device_type,
    is_reconnection_event,
    request,
    resolve_feature_index,
    send_change_host,
)
from logi_host.transport import TransportError


# -- Helpers -------------------------------------------------------------------


def _make_long_reply(devnumber: int, request_id: int, payload: bytes = b"") -> bytes:
    """Build a valid 20-byte HID++ long reply."""
    data = struct.pack("!H", request_id) + payload
    return struct.pack("!BB18s", REPORT_LONG, devnumber, data)


def _make_short_error(devnumber: int, request_id: int, error_code: int) -> bytes:
    """Build a 7-byte HID++ 1.0 error reply."""
    return struct.pack(
        "!BBBHBx",
        REPORT_SHORT,
        devnumber,
        0x8F,
        request_id,
        error_code,
    )


def _make_20_error(devnumber: int, request_id: int, error_code: int) -> bytes:
    """Build a 20-byte HID++ 2.0 error reply."""
    data = struct.pack("!BHB", 0xFF, request_id, error_code)
    return struct.pack("!BB18s", REPORT_LONG, devnumber, data)


# -- _pack_params tests --------------------------------------------------------


class TestPackParams:
    def test_empty(self):
        assert _pack_params() == b""

    def test_single_int(self):
        assert _pack_params(0x42) == b"\x42"

    def test_multiple_ints(self):
        assert _pack_params(0x18, 0x14, 0x00) == b"\x18\x14\x00"

    def test_bytes_passthrough(self):
        assert _pack_params(b"\xAA\xBB") == b"\xAA\xBB"

    def test_mixed_int_and_bytes(self):
        result = _pack_params(0x01, b"\x02\x03")
        assert result == b"\x01\x02\x03"


# -- _build_msg tests ----------------------------------------------------------


class TestBuildMsg:
    def test_always_produces_20_bytes(self):
        msg = _build_msg(devnumber=1, request_id=0x0008, params=b"")
        assert len(msg) == MSG_LONG_LEN

    def test_report_id_is_long(self):
        msg = _build_msg(devnumber=1, request_id=0x0008, params=b"")
        assert msg[0] == REPORT_LONG

    def test_devnumber_in_byte_1(self):
        msg = _build_msg(devnumber=0x03, request_id=0x0008, params=b"")
        assert msg[1] == 0x03

    def test_request_id_in_bytes_2_3(self):
        msg = _build_msg(devnumber=1, request_id=0x1234, params=b"")
        assert msg[2] == 0x12
        assert msg[3] == 0x34

    def test_params_follow_request_id(self):
        msg = _build_msg(devnumber=1, request_id=0x0008, params=b"\xAA\xBB")
        assert msg[4] == 0xAA
        assert msg[5] == 0xBB

    def test_remaining_bytes_are_zero_padded(self):
        msg = _build_msg(devnumber=1, request_id=0x0008, params=b"\x01")
        # Bytes 5-19 should be 0x00
        assert msg[5:] == b"\x00" * 15


# -- _is_relevant tests --------------------------------------------------------


class TestIsRelevant:
    def test_valid_long_message(self):
        msg = b"\x11" + b"\x00" * 19
        assert _is_relevant(msg) is True

    def test_valid_short_message(self):
        msg = b"\x10" + b"\x00" * 6
        assert _is_relevant(msg) is True

    def test_valid_dj_message(self):
        msg = b"\x20" + b"\x00" * 14
        assert _is_relevant(msg) is True

    def test_empty_bytes(self):
        assert _is_relevant(b"") is False

    def test_wrong_report_id(self):
        msg = b"\x99" + b"\x00" * 19
        assert _is_relevant(msg) is False

    def test_wrong_length_for_report_id(self):
        # Report 0x11 expects 20 bytes, give it 10
        msg = b"\x11" + b"\x00" * 9
        assert _is_relevant(msg) is False

    def test_none_returns_false(self):
        assert _is_relevant(None) is False  # type: ignore[arg-type]

    def test_too_short(self):
        assert _is_relevant(b"\x11\x00") is False


# -- request() tests -----------------------------------------------------------


class TestRequest:
    def test_returns_payload_on_successful_reply(self, make_fake_transport):
        effective_id = (0x0100 & 0xFFF0) | SW_ID  # 0x0108
        reply = _make_long_reply(1, effective_id, b"\xAA\xBB\xCC")
        t = make_fake_transport(responses=[reply])
        result = request(t, devnumber=1, request_id=0x0100)
        assert result is not None
        assert result[:3] == b"\xAA\xBB\xCC"

    def test_sends_correct_message(self, make_fake_transport):
        effective_id = (0x0100 & 0xFFF0) | SW_ID
        reply = _make_long_reply(1, effective_id, b"\x05")
        t = make_fake_transport(responses=[reply])
        request(t, 1, 0x0100, 0x18, 0x14, 0x00)
        assert len(t.written) == 1
        msg = t.written[0]
        assert len(msg) == MSG_LONG_LEN
        assert msg[0] == REPORT_LONG
        assert msg[1] == 0x01  # devnumber

    def test_returns_none_on_timeout(self, make_fake_transport, mocker):
        # Fake transport returns None (no data), and patch time to expire immediately
        mocker.patch("logi_host.protocol.time", side_effect=[0.0, 100.0])
        t = make_fake_transport(responses=[])
        result = request(t, devnumber=1, request_id=0x0100)
        assert result is None

    def test_returns_none_on_hidpp10_error(self, make_fake_transport):
        effective_id = (0x0100 & 0xFFF0) | SW_ID
        error = _make_short_error(1, effective_id, 0x02)
        t = make_fake_transport(responses=[error])
        result = request(t, devnumber=1, request_id=0x0100)
        assert result is None

    def test_returns_none_on_hidpp20_error(self, make_fake_transport):
        effective_id = (0x0100 & 0xFFF0) | SW_ID
        error = _make_20_error(1, effective_id, 0x05)
        t = make_fake_transport(responses=[error])
        result = request(t, devnumber=1, request_id=0x0100)
        assert result is None

    def test_raises_transport_error_on_write_failure(self, fake_transport, mocker):
        mocker.patch.object(fake_transport, "write", side_effect=OSError("device gone"))
        with pytest.raises(TransportError, match="write failed"):
            request(fake_transport, devnumber=1, request_id=0x0100)

    def test_raises_transport_error_on_read_failure(self, fake_transport, mocker):
        fake_transport.write = lambda data: None  # write succeeds
        mocker.patch.object(fake_transport, "read", side_effect=OSError("read error"))
        with pytest.raises(TransportError, match="read failed"):
            request(fake_transport, devnumber=1, request_id=0x0100)

    def test_skips_irrelevant_messages(self, make_fake_transport):
        """Should skip messages from wrong device and return the correct one."""
        effective_id = (0x0100 & 0xFFF0) | SW_ID
        wrong_device = _make_long_reply(99, effective_id, b"\xFF")  # wrong devnumber
        correct = _make_long_reply(1, effective_id, b"\xAA")
        t = make_fake_transport(responses=[wrong_device, correct])
        result = request(t, devnumber=1, request_id=0x0100)
        assert result is not None
        assert result[0] == 0xAA

    def test_accepts_xor_device_number(self, make_fake_transport):
        """Bluetooth devices may XOR devnumber with 0xFF."""
        effective_id = (0x0100 & 0xFFF0) | SW_ID
        reply = _make_long_reply(0x01 ^ 0xFF, effective_id, b"\xBB")
        t = make_fake_transport(responses=[reply])
        result = request(t, devnumber=1, request_id=0x0100)
        assert result is not None
        assert result[0] == 0xBB


# -- resolve_feature_index tests -----------------------------------------------


class TestResolveFeatureIndex:
    def test_returns_index_when_supported(self, fake_transport, mocker):
        mocker.patch("logi_host.protocol.request", return_value=b"\x05\x00\x00")
        result = resolve_feature_index(fake_transport, devnumber=1, feature_code=0x1814)
        assert result == 5

    def test_returns_none_when_not_supported(self, fake_transport, mocker):
        """Feature index 0 means not supported."""
        mocker.patch("logi_host.protocol.request", return_value=b"\x00\x00\x00")
        result = resolve_feature_index(fake_transport, devnumber=1, feature_code=0x1814)
        assert result is None

    def test_returns_none_on_timeout(self, fake_transport, mocker):
        mocker.patch("logi_host.protocol.request", return_value=None)
        result = resolve_feature_index(fake_transport, devnumber=1, feature_code=0x1814)
        assert result is None


# -- get_device_type tests -----------------------------------------------------


class TestGetDeviceType:
    def test_returns_type_on_success(self, fake_transport, mocker):
        mocker.patch("logi_host.protocol.request", return_value=b"\x03\x00")
        result = get_device_type(fake_transport, devnumber=1, feat_idx=0x03)
        assert result == 3  # Mouse

    def test_returns_none_on_failure(self, fake_transport, mocker):
        mocker.patch("logi_host.protocol.request", return_value=None)
        result = get_device_type(fake_transport, devnumber=1, feat_idx=0x03)
        assert result is None


# -- get_device_name tests -----------------------------------------------------


class TestGetDeviceName:
    def test_single_chunk_name(self, fake_transport, mocker):
        mocker.patch(
            "logi_host.protocol.request",
            side_effect=[
                b"\x09",  # getDeviceNameCount -> nameLen=9
                b"MX Master" + b"\x00" * 7,  # getDeviceName(0) -> 9 chars
            ],
        )
        result = get_device_name(fake_transport, devnumber=1, feat_idx=0x03)
        assert result == "MX Master"

    def test_multi_chunk_name(self, fake_transport, mocker):
        # Name "MX Master 3 Advanced" = 20 chars, needs 2 chunks (16 + 4)
        name = "MX Master 3 Adva"  # 16 chars in first chunk
        name2 = "nced"  # 4 chars in second chunk
        mocker.patch(
            "logi_host.protocol.request",
            side_effect=[
                b"\x14",  # getDeviceNameCount -> nameLen=20
                name.encode() + b"\x00" * 0,  # chunk 1: 16 bytes of payload
                name2.encode() + b"\x00" * 12,  # chunk 2: 4 relevant bytes
            ],
        )
        result = get_device_name(fake_transport, devnumber=1, feat_idx=0x03)
        assert result == "MX Master 3 Advanced"

    def test_returns_none_on_count_failure(self, fake_transport, mocker):
        mocker.patch("logi_host.protocol.request", return_value=None)
        result = get_device_name(fake_transport, devnumber=1, feat_idx=0x03)
        assert result is None

    def test_returns_none_on_zero_length(self, fake_transport, mocker):
        mocker.patch("logi_host.protocol.request", return_value=b"\x00")
        result = get_device_name(fake_transport, devnumber=1, feat_idx=0x03)
        assert result is None

    def test_returns_partial_name_on_chunk_timeout(self, fake_transport, mocker):
        mocker.patch(
            "logi_host.protocol.request",
            side_effect=[
                b"\x07",  # nameLen=7
                b"MX Keys" + b"\x00" * 9,  # chunk: 7 relevant chars
            ],
        )
        result = get_device_name(fake_transport, devnumber=1, feat_idx=0x03)
        assert result == "MX Keys"


# -- send_change_host tests ----------------------------------------------------


class TestSendChangeHost:
    def test_sends_correct_message(self, fake_transport):
        send_change_host(fake_transport, devnumber=2, feature_idx=0x07, target_host=1)
        assert len(fake_transport.written) == 1
        msg = fake_transport.written[0]
        assert len(msg) == MSG_LONG_LEN
        assert msg[0] == REPORT_LONG
        assert msg[1] == 0x02  # devnumber
        # request_id = (0x07 << 8) | (0x10 & 0xF0) | 0x08 = 0x0718
        assert msg[2] == 0x07
        assert msg[3] == 0x18
        assert msg[4] == 0x01  # target_host

    def test_host_0_for_first_slot(self, fake_transport):
        send_change_host(fake_transport, devnumber=1, feature_idx=0x05, target_host=0)
        msg = fake_transport.written[0]
        assert msg[4] == 0x00

    def test_raises_transport_error_on_write_failure(self, fake_transport, mocker):
        mocker.patch.object(fake_transport, "write", side_effect=OSError("gone"))
        with pytest.raises(TransportError, match="send_change_host failed"):
            send_change_host(fake_transport, devnumber=1, feature_idx=0x07, target_host=0)


# -- find_mouse tests ----------------------------------------------------------


class TestFindMouse:
    def test_finds_mouse_in_slot_1(self, fake_transport, mocker):
        # Slot 1: x0005 feat_idx=3, type=Mouse(3), name="MX Master 3", CHANGE_HOST feat_idx=7
        mocker.patch(
            "logi_host.protocol.resolve_feature_index",
            side_effect=[
                3,     # x0005 for slot 1
                7,     # CHANGE_HOST for slot 1
            ],
        )
        mocker.patch("logi_host.protocol.get_device_type", return_value=3)
        mocker.patch("logi_host.protocol.get_device_name", return_value="MX Master 3")

        result = find_mouse(fake_transport)
        assert result is not None
        slot, name, ch_feat = result
        assert slot == 1
        assert name == "MX Master 3"
        assert ch_feat == 7

    def test_skips_keyboard_finds_mouse_in_slot_2(self, fake_transport, mocker):
        # Slot 1: keyboard (type=0), Slot 2: mouse (type=3)
        resolve_calls = [
            3,     # x0005 for slot 1 (keyboard)
            4,     # x0005 for slot 2 (mouse)
            7,     # CHANGE_HOST for slot 2
        ]
        mocker.patch("logi_host.protocol.resolve_feature_index", side_effect=resolve_calls)
        mocker.patch("logi_host.protocol.get_device_type", side_effect=[0, 3])
        mocker.patch("logi_host.protocol.get_device_name", return_value="MX Master 3")

        result = find_mouse(fake_transport)
        assert result is not None
        slot, name, ch_feat = result
        assert slot == 2

    def test_returns_none_when_no_devices(self, fake_transport, mocker):
        mocker.patch("logi_host.protocol.resolve_feature_index", return_value=None)
        result = find_mouse(fake_transport)
        assert result is None

    def test_skips_mouse_without_change_host(self, fake_transport, mocker):
        # All 6 slots: mouse found but no CHANGE_HOST support
        resolve_calls = []
        type_calls = []
        for _ in range(6):
            resolve_calls.append(3)     # x0005 found
            resolve_calls.append(None)  # CHANGE_HOST not found
            type_calls.append(3)        # it's a mouse

        mocker.patch("logi_host.protocol.resolve_feature_index", side_effect=resolve_calls)
        mocker.patch("logi_host.protocol.get_device_type", side_effect=type_calls)
        mocker.patch("logi_host.protocol.get_device_name", return_value="Some Mouse")

        result = find_mouse(fake_transport)
        assert result is None

    def test_finds_trackpad(self, fake_transport, mocker):
        """Trackpad (type 4) should be treated as mouse-class."""
        mocker.patch("logi_host.protocol.resolve_feature_index", side_effect=[3, 7])
        mocker.patch("logi_host.protocol.get_device_type", return_value=4)  # Trackpad
        mocker.patch("logi_host.protocol.get_device_name", return_value="T650")

        result = find_mouse(fake_transport)
        assert result is not None
        assert result[0] == 1  # slot
        assert result[1] == "T650"

    def test_finds_trackball(self, fake_transport, mocker):
        """Trackball (type 5) should be treated as mouse-class."""
        mocker.patch("logi_host.protocol.resolve_feature_index", side_effect=[3, 7])
        mocker.patch("logi_host.protocol.get_device_type", return_value=5)  # Trackball
        mocker.patch("logi_host.protocol.get_device_name", return_value="MX Ergo")

        result = find_mouse(fake_transport)
        assert result is not None
        assert result[1] == "MX Ergo"


class TestGetCurrentHost:
    """Tests for get_current_host() — querying HOSTS_INFO (0x1815)."""

    def test_returns_1based_host_on_success(self, make_fake_transport):
        """Reply byte[3] = 2 (0-based) → returns 3 (1-based)."""
        hosts_info_idx = 0x0B
        request_id = (hosts_info_idx << 8) | 0x00 | SW_ID
        # Reply: capability_flags=0x03, reserved=0x00, numHosts=3, currentHost=2 (0-based)
        reply = _make_long_reply(0x01, request_id, b"\x03\x00\x03\x02")
        transport = make_fake_transport(responses=[reply])
        result = get_current_host(transport, 0x01, hosts_info_idx)
        assert result == 3

    def test_returns_host_1_for_0based_0(self, make_fake_transport):
        """Reply byte[3] = 0 (0-based) → returns 1 (1-based)."""
        hosts_info_idx = 0x0B
        request_id = (hosts_info_idx << 8) | 0x00 | SW_ID
        reply = _make_long_reply(0x01, request_id, b"\x03\x00\x03\x00")
        transport = make_fake_transport(responses=[reply])
        result = get_current_host(transport, 0x01, hosts_info_idx)
        assert result == 1

    def test_returns_none_on_timeout(self, make_fake_transport):
        """No reply → returns None."""
        transport = make_fake_transport(responses=[])
        result = get_current_host(transport, 0x01, 0x0B)
        assert result is None

    def test_returns_none_when_request_fails(self, make_fake_transport, mocker):
        """If the underlying request() returns None, get_current_host returns None."""
        mocker.patch("logi_host.protocol.request", return_value=None)
        transport = make_fake_transport()
        result = get_current_host(transport, 0x01, 0x0B)
        assert result is None


class TestIsReconnectionEvent:
    """Tests for is_reconnection_event() — detecting mouse reconnection."""

    def test_valid_reconnection_event(self):
        """Standard reconnection: report=0x11, slot=1, feat_idx=0x04, byte[4]=0x01."""
        raw = bytes([REPORT_LONG, 0x01, 0x04, 0x00, 0x01]) + b"\x00" * 15
        assert is_reconnection_event(raw, 0x01) is True

    def test_wrong_device_number(self):
        """Reconnection for slot 2 should not match slot 1."""
        raw = bytes([REPORT_LONG, 0x02, 0x04, 0x00, 0x01]) + b"\x00" * 15
        assert is_reconnection_event(raw, 0x01) is False

    def test_wrong_report_id(self):
        """Short report (0x10) is not a reconnection event."""
        raw = bytes([REPORT_SHORT, 0x01, 0x04, 0x00, 0x01, 0x00, 0x00])
        assert is_reconnection_event(raw, 0x01) is False

    def test_wrong_feature_index(self):
        """Feature index 0x05 is not the Wireless Device Status feature."""
        raw = bytes([REPORT_LONG, 0x01, 0x05, 0x00, 0x01]) + b"\x00" * 15
        assert is_reconnection_event(raw, 0x01) is False

    def test_wrong_status_byte(self):
        """Status byte 0x00 (disconnected) is not a reconnection."""
        raw = bytes([REPORT_LONG, 0x01, 0x04, 0x00, 0x00]) + b"\x00" * 15
        assert is_reconnection_event(raw, 0x01) is False

    def test_empty_bytes(self):
        assert is_reconnection_event(b"", 0x01) is False

    def test_none_bytes(self):
        assert is_reconnection_event(None, 0x01) is False

    def test_too_short(self):
        """Packet shorter than 5 bytes cannot be a reconnection event."""
        raw = bytes([REPORT_LONG, 0x01, 0x04, 0x00])
        assert is_reconnection_event(raw, 0x01) is False
