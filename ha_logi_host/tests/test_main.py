"""Tests for logi_host.main — main loop lifecycle, reconnect, shutdown."""

from __future__ import annotations

import signal
import threading
from unittest.mock import MagicMock, patch

import pytest

from logi_host.main import DISCOVER_RETRY_INTERVAL, PROBE_RETRY_INTERVAL, RECONNECT_INTERVAL, _setup_logging, run
from logi_host.transport import TransportError


class TestSetupLogging:
    def test_debug_level(self, mocker):
        mock_basic = mocker.patch("logi_host.main.logging.basicConfig")
        _setup_logging("debug")
        mock_basic.assert_called_once()
        assert mock_basic.call_args.kwargs["level"] == 10  # logging.DEBUG

    def test_info_level(self, mocker):
        mock_basic = mocker.patch("logi_host.main.logging.basicConfig")
        _setup_logging("info")
        assert mock_basic.call_args.kwargs["level"] == 20  # logging.INFO

    def test_invalid_level_defaults_to_info(self, mocker):
        mock_basic = mocker.patch("logi_host.main.logging.basicConfig")
        _setup_logging("nonsense")
        # getattr(logging, "NONSENSE", logging.INFO) -> INFO (20)
        assert mock_basic.call_args.kwargs["level"] == 20


class TestRunNoReceiver:
    """Test the main loop when no receiver is found."""

    def test_retries_when_no_receiver(self, mocker):
        mocker.patch.dict(
            "os.environ",
            {"MQTT_HOST": "localhost", "MQTT_PORT": "1883", "RECEIVER_TYPE": "unifying", "LOG_LEVEL": "warning"},
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)
        mocker.patch("logi_host.main.enumerate_receivers", return_value=[])

        # Simulate shutdown after first retry
        call_count = 0
        real_event = threading.Event()

        def fake_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                real_event.set()
                return True
            return False

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = lambda: call_count >= 2
        mock_shutdown.wait = fake_wait
        mock_shutdown.set = real_event.set
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)

        run()

        # Should have published offline status
        mock_mqtt.publish_status.assert_called_with("offline")

    def test_retries_with_device_path_not_found(self, mocker):
        """When DEVICE_PATH is set but no receiver matches, show helpful message."""
        mocker.patch.dict(
            "os.environ",
            {
                "MQTT_HOST": "localhost",
                "MQTT_PORT": "1883",
                "RECEIVER_TYPE": "unifying",
                "LOG_LEVEL": "warning",
                "DEVICE_PATH": "/dev/hidraw5",
            },
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)
        mock_enum = mocker.patch("logi_host.main.enumerate_receivers", return_value=[])

        call_count = 0

        def fake_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            return False

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = lambda: call_count >= 1
        mock_shutdown.wait = fake_wait
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)

        run()

        # enumerate_receivers should have been called with path_filter
        mock_enum.assert_called_with(receiver_type="unifying", path_filter=b"/dev/hidraw5")


class TestRunNoMouse:
    """Test the main loop when receiver found but no mouse."""

    def test_retries_when_no_mouse(self, mocker):
        mocker.patch.dict(
            "os.environ",
            {"MQTT_HOST": "localhost", "MQTT_PORT": "1883", "RECEIVER_TYPE": "unifying", "LOG_LEVEL": "warning"},
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)

        # Receiver found
        mock_receiver = MagicMock()
        mock_receiver.path = b"/dev/hidraw0"
        mock_receiver.receiver_type = "unifying"
        mock_receiver.pid = 0xC52B
        mocker.patch("logi_host.main.enumerate_receivers", return_value=[mock_receiver])

        mock_transport = MagicMock()
        mocker.patch("logi_host.main.HIDTransport", return_value=mock_transport)

        # No mouse found
        mock_find = mocker.patch("logi_host.main.find_mouse", return_value=None)

        call_count = 0

        def fake_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            return False

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = lambda: call_count >= 2
        mock_shutdown.wait = fake_wait
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)

        run()

        # find_mouse should have been called
        assert mock_find.call_count >= 1


class TestRunWithDevicePath:
    """Test the main loop when DEVICE_PATH is configured."""

    def test_device_path_passed_to_enumerate(self, mocker):
        mocker.patch.dict(
            "os.environ",
            {
                "MQTT_HOST": "broker",
                "MQTT_PORT": "1883",
                "RECEIVER_TYPE": "unifying",
                "LOG_LEVEL": "info",
                "DEVICE_PATH": "/dev/hidraw2",
            },
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)

        mock_receiver = MagicMock()
        mock_receiver.path = b"/dev/hidraw2"
        mock_receiver.receiver_type = "unifying"
        mock_receiver.pid = 0xC52B
        mock_enum = mocker.patch("logi_host.main.enumerate_receivers", return_value=[mock_receiver])

        mock_transport = MagicMock()
        mocker.patch("logi_host.main.HIDTransport", return_value=mock_transport)
        mocker.patch("logi_host.main.find_mouse", return_value=(1, "MX Master 3", 0x0A))
        mocker.patch("logi_host.main.resolve_feature_index", return_value=0x0B)
        mocker.patch("logi_host.main.get_current_host", return_value=3)

        call_count = 0

        def fake_is_set():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = fake_is_set
        mock_shutdown.wait = MagicMock(return_value=False)
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)
        mocker.patch("logi_host.main.signal.signal")
        mock_transport.read.return_value = None

        run()

        # enumerate_receivers should have been called with path_filter
        mock_enum.assert_called_with(receiver_type="unifying", path_filter=b"/dev/hidraw2")
        mock_mqtt.connect.assert_called_once_with(mouse_name="MX Master 3")

    def test_empty_device_path_uses_autodiscovery(self, mocker):
        """Empty DEVICE_PATH should behave like no DEVICE_PATH."""
        mocker.patch.dict(
            "os.environ",
            {
                "MQTT_HOST": "broker",
                "MQTT_PORT": "1883",
                "RECEIVER_TYPE": "unifying",
                "LOG_LEVEL": "info",
                "DEVICE_PATH": "",
            },
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)
        mock_enum = mocker.patch("logi_host.main.enumerate_receivers", return_value=[])

        call_count = 0

        def fake_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            return False

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = lambda: call_count >= 1
        mock_shutdown.wait = fake_wait
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)

        run()

        # Should call without path_filter (None)
        mock_enum.assert_called_with(receiver_type="unifying", path_filter=None)


class TestRunSuccess:
    """Test successful startup and host switching."""

    def test_successful_startup_and_mqtt_connect(self, mocker):
        mocker.patch.dict(
            "os.environ",
            {"MQTT_HOST": "broker", "MQTT_PORT": "1883", "RECEIVER_TYPE": "unifying", "LOG_LEVEL": "info"},
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)

        mock_receiver = MagicMock()
        mock_receiver.path = b"/dev/hidraw0"
        mock_receiver.receiver_type = "unifying"
        mock_receiver.pid = 0xC52B
        mocker.patch("logi_host.main.enumerate_receivers", return_value=[mock_receiver])

        mock_transport = MagicMock()
        mocker.patch("logi_host.main.HIDTransport", return_value=mock_transport)

        # Mouse found in slot 2
        mocker.patch("logi_host.main.find_mouse", return_value=(2, "MX Master 3", 0x07))
        mocker.patch("logi_host.main.resolve_feature_index", return_value=0x0B)
        mocker.patch("logi_host.main.get_current_host", return_value=3)

        # Transport health check succeeds once, then shutdown
        call_count = 0

        def fake_is_set():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = fake_is_set
        mock_shutdown.wait = MagicMock(return_value=False)
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)
        mocker.patch("logi_host.main.signal.signal")

        mock_transport.read.return_value = None  # health check returns no data (normal)

        run()

        # MQTT should be connected with the mouse name
        mock_mqtt.connect.assert_called_once_with(mouse_name="MX Master 3")
        mock_mqtt.start.assert_called_once()
        mock_mqtt.stop.assert_called_once()

    def test_publishes_initial_host_on_startup(self, mocker):
        """When HOSTS_INFO is supported, current host should be published on startup."""
        mocker.patch.dict(
            "os.environ",
            {"MQTT_HOST": "broker", "MQTT_PORT": "1883", "RECEIVER_TYPE": "unifying", "LOG_LEVEL": "info"},
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)

        mock_receiver = MagicMock()
        mock_receiver.path = b"/dev/hidraw0"
        mock_receiver.receiver_type = "unifying"
        mock_receiver.pid = 0xC52B
        mocker.patch("logi_host.main.enumerate_receivers", return_value=[mock_receiver])

        mock_transport = MagicMock()
        mocker.patch("logi_host.main.HIDTransport", return_value=mock_transport)

        mocker.patch("logi_host.main.find_mouse", return_value=(1, "MX Master 3", 0x0A))
        mocker.patch("logi_host.main.resolve_feature_index", return_value=0x0B)
        mocker.patch("logi_host.main.get_current_host", return_value=3)
        mocker.patch("logi_host.main.is_reconnection_event", return_value=False)

        call_count = 0

        def fake_is_set():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = fake_is_set
        mock_shutdown.wait = MagicMock(return_value=False)
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)
        mocker.patch("logi_host.main.signal.signal")
        mock_transport.read.return_value = None

        run()

        # Should publish initial host state = 3
        mock_mqtt.publish_host.assert_called_with(3)

    def test_no_initial_host_when_hosts_info_unsupported(self, mocker):
        """When HOSTS_INFO is not supported, no initial host should be published."""
        mocker.patch.dict(
            "os.environ",
            {"MQTT_HOST": "broker", "MQTT_PORT": "1883", "RECEIVER_TYPE": "unifying", "LOG_LEVEL": "info"},
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)

        mock_receiver = MagicMock()
        mock_receiver.path = b"/dev/hidraw0"
        mock_receiver.receiver_type = "unifying"
        mock_receiver.pid = 0xC52B
        mocker.patch("logi_host.main.enumerate_receivers", return_value=[mock_receiver])

        mock_transport = MagicMock()
        mocker.patch("logi_host.main.HIDTransport", return_value=mock_transport)

        mocker.patch("logi_host.main.find_mouse", return_value=(1, "MX Master 3", 0x0A))
        # HOSTS_INFO not supported
        mocker.patch("logi_host.main.resolve_feature_index", return_value=None)

        call_count = 0

        def fake_is_set():
            nonlocal call_count
            call_count += 1
            return call_count > 1

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = fake_is_set
        mock_shutdown.wait = MagicMock(return_value=False)
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)
        mocker.patch("logi_host.main.signal.signal")
        mock_transport.read.return_value = None

        run()

        # publish_host should NOT have been called (no initial host)
        mock_mqtt.publish_host.assert_not_called()


class TestRunReconnectionDetection:
    """Test that reconnection events in the idle loop trigger host re-query."""

    def test_reconnection_event_publishes_host(self, mocker):
        mocker.patch.dict(
            "os.environ",
            {"MQTT_HOST": "broker", "MQTT_PORT": "1883", "RECEIVER_TYPE": "unifying", "LOG_LEVEL": "info"},
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)

        mock_receiver = MagicMock()
        mock_receiver.path = b"/dev/hidraw0"
        mock_receiver.receiver_type = "unifying"
        mock_receiver.pid = 0xC52B
        mocker.patch("logi_host.main.enumerate_receivers", return_value=[mock_receiver])

        mock_transport = MagicMock()
        mocker.patch("logi_host.main.HIDTransport", return_value=mock_transport)

        mocker.patch("logi_host.main.find_mouse", return_value=(1, "MX Master 3", 0x0A))
        mocker.patch("logi_host.main.resolve_feature_index", return_value=0x0B)

        # First get_current_host call (startup) returns 3,
        # second call (after reconnection) returns 3
        mock_get_host = mocker.patch("logi_host.main.get_current_host", side_effect=[3, 3])

        # First read returns a reconnection packet, second read returns None
        reconnect_packet = b"\x11\x01\x04\x00\x01" + b"\x00" * 15
        mock_transport.read.side_effect = [reconnect_packet, None]

        # is_reconnection_event: first call (for reconnect_packet) returns True,
        # second call (for None) returns False
        mock_is_recon = mocker.patch("logi_host.main.is_reconnection_event", side_effect=[True, False])

        call_count = 0

        def fake_is_set():
            nonlocal call_count
            call_count += 1
            return call_count > 2  # two iterations of the idle loop

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = fake_is_set
        mock_shutdown.wait = MagicMock(return_value=False)
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)
        mocker.patch("logi_host.main.signal.signal")

        run()

        # get_current_host should be called twice: once at startup, once on reconnect
        assert mock_get_host.call_count == 2
        # publish_host should be called twice: once for initial host, once for reconnect
        assert mock_mqtt.publish_host.call_count == 2
        mock_mqtt.publish_host.assert_called_with(3)


class TestRunTransportError:
    """Test reconnect on transport error."""

    def test_transport_error_triggers_reconnect(self, mocker):
        mocker.patch.dict(
            "os.environ",
            {"MQTT_HOST": "localhost", "MQTT_PORT": "1883", "RECEIVER_TYPE": "unifying", "LOG_LEVEL": "warning"},
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)

        mock_receiver = MagicMock()
        mock_receiver.path = b"/dev/hidraw0"
        mock_receiver.receiver_type = "unifying"
        mock_receiver.pid = 0xC52B
        mocker.patch("logi_host.main.enumerate_receivers", return_value=[mock_receiver])

        mock_transport = MagicMock()
        mocker.patch("logi_host.main.HIDTransport", return_value=mock_transport)
        mocker.patch("logi_host.main.find_mouse", return_value=(2, "MX Master 3", 0x07))
        mocker.patch("logi_host.main.resolve_feature_index", return_value=None)

        # First read raises TransportError, then shutdown
        mock_transport.read.side_effect = TransportError("device gone")

        call_count = 0

        def fake_is_set():
            nonlocal call_count
            call_count += 1
            return call_count > 2  # allow: first loop iteration + reconnect wait

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = fake_is_set
        mock_shutdown.wait = MagicMock(return_value=False)
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)
        mocker.patch("logi_host.main.signal.signal")

        run()

        # Should publish offline after transport error
        mock_mqtt.publish_status.assert_any_call("offline")
        # Transport should be closed
        mock_transport.close.assert_called()


class TestRunOpenFailure:
    """Test when opening the receiver fails."""

    def test_open_failure_retries(self, mocker):
        mocker.patch.dict(
            "os.environ",
            {"MQTT_HOST": "localhost", "MQTT_PORT": "1883", "RECEIVER_TYPE": "unifying", "LOG_LEVEL": "warning"},
        )
        mocker.patch("logi_host.main._setup_logging")

        mock_mqtt = MagicMock()
        mocker.patch("logi_host.main.MQTTBridge", return_value=mock_mqtt)

        mock_receiver = MagicMock()
        mock_receiver.path = b"/dev/hidraw0"
        mock_receiver.receiver_type = "unifying"
        mock_receiver.pid = 0xC52B
        mocker.patch("logi_host.main.enumerate_receivers", return_value=[mock_receiver])

        # Opening transport fails
        mock_transport_cls = mocker.patch(
            "logi_host.main.HIDTransport", side_effect=TransportError("permission denied"),
        )

        call_count = 0

        def fake_wait(timeout=None):
            nonlocal call_count
            call_count += 1
            return False

        mock_shutdown = MagicMock()
        mock_shutdown.is_set = lambda: call_count >= 2
        mock_shutdown.wait = fake_wait
        mocker.patch("logi_host.main.threading.Event", return_value=mock_shutdown)

        run()

        # Should have tried to open transport at least once
        assert mock_transport_cls.call_count >= 1
