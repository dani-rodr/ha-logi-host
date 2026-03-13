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
