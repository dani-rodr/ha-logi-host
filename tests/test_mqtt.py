"""Tests for logi_host.mqtt — MQTT bridge, HA auto-discovery, command handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from logi_host.mqtt import (
    TOPIC_HOST_COMMAND,
    TOPIC_HOST_STATE,
    TOPIC_STATUS,
    MQTTBridge,
    _select_discovery_payload,
    _sensor_discovery_payload,
)


# -- Discovery payload tests ---------------------------------------------------


class TestSelectDiscoveryPayload:
    def test_with_mouse_name(self):
        payload = _select_discovery_payload("MX Master 3")
        assert payload["name"] == "MX Master 3 Host"
        assert payload["unique_id"] == "ha_logi_host_mouse_host"
        assert payload["command_topic"] == TOPIC_HOST_COMMAND
        assert payload["state_topic"] == TOPIC_HOST_STATE
        assert payload["availability_topic"] == TOPIC_STATUS
        assert payload["options"] == ["1", "2", "3"]
        assert payload["icon"] == "mdi:mouse"

    def test_without_mouse_name(self):
        payload = _select_discovery_payload(None)
        assert payload["name"] == "Mouse Host"

    def test_device_info_present(self):
        payload = _select_discovery_payload("Test Mouse")
        assert "device" in payload
        assert "identifiers" in payload["device"]
        assert payload["device"]["manufacturer"] == "Logitech"


class TestSensorDiscoveryPayload:
    def test_correct_structure(self):
        payload = _sensor_discovery_payload()
        assert payload["name"] == "Receiver Status"
        assert payload["unique_id"] == "ha_logi_host_receiver_status"
        assert payload["state_topic"] == TOPIC_STATUS
        assert payload["icon"] == "mdi:usb"

    def test_device_info_matches_select(self):
        select = _select_discovery_payload(None)
        sensor = _sensor_discovery_payload()
        assert select["device"] == sensor["device"]


# -- MQTTBridge tests ----------------------------------------------------------


class TestMQTTBridgeInit:
    @patch("logi_host.mqtt.mqtt.Client")
    def test_sets_last_will(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        mock_client.will_set.assert_called_once_with(
            TOPIC_STATUS, "offline", qos=1, retain=True,
        )

    @patch("logi_host.mqtt.mqtt.Client")
    def test_sets_credentials(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883, username="user", password="pass")
        mock_client.username_pw_set.assert_called_once_with("user", "pass")

    @patch("logi_host.mqtt.mqtt.Client")
    def test_no_credentials_when_username_is_none(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883, username=None)
        mock_client.username_pw_set.assert_not_called()


class TestMQTTBridgeConnect:
    @patch("logi_host.mqtt.mqtt.Client")
    def test_connect_calls_broker(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="mybroker", port=1884)
        bridge.connect(mouse_name="MX Master 3")
        mock_client.connect.assert_called_once_with("mybroker", 1884, keepalive=60)

    @patch("logi_host.mqtt.mqtt.Client")
    def test_start_calls_loop_start(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        bridge.start()
        mock_client.loop_start.assert_called_once()

    @patch("logi_host.mqtt.mqtt.Client")
    def test_stop_calls_loop_stop_and_disconnect(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        bridge.stop()
        mock_client.loop_stop.assert_called_once()
        mock_client.disconnect.assert_called_once()


class TestMQTTBridgePublish:
    @patch("logi_host.mqtt.mqtt.Client")
    def test_publish_host(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        bridge.publish_host(2)
        mock_client.publish.assert_called_once_with(
            TOPIC_HOST_STATE, "2", qos=1, retain=True,
        )

    @patch("logi_host.mqtt.mqtt.Client")
    def test_publish_status(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        bridge.publish_status("online")
        mock_client.publish.assert_called_once_with(
            TOPIC_STATUS, "online", qos=1, retain=True,
        )


# -- Callback tests ------------------------------------------------------------


class TestOnConnect:
    @patch("logi_host.mqtt.mqtt.Client")
    def test_on_connect_publishes_discovery_and_subscribes(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        bridge._mouse_name = "MX Master 3"

        # Simulate successful connection
        bridge._on_connect(mock_client, None, None, 0)

        assert bridge._connected is True
        # Should have published: select discovery, sensor discovery, online status
        assert mock_client.publish.call_count == 3
        # Should have subscribed to command topic
        mock_client.subscribe.assert_called_once_with(TOPIC_HOST_COMMAND, qos=1)

    @patch("logi_host.mqtt.mqtt.Client")
    def test_on_connect_failure_sets_not_connected(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        bridge._on_connect(mock_client, None, None, 5)  # rc=5 -> auth error

        assert bridge._connected is False
        mock_client.publish.assert_not_called()


class TestOnMessage:
    @patch("logi_host.mqtt.mqtt.Client")
    def test_valid_host_calls_callback(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        callback = MagicMock()
        bridge = MQTTBridge(host="localhost", port=1883, on_host_switch=callback)

        msg = MagicMock()
        msg.topic = TOPIC_HOST_COMMAND
        msg.payload = b"2"

        bridge._on_message(mock_client, None, msg)
        callback.assert_called_once_with(2)

    @patch("logi_host.mqtt.mqtt.Client")
    def test_all_valid_hosts(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        callback = MagicMock()
        bridge = MQTTBridge(host="localhost", port=1883, on_host_switch=callback)

        for host_num in (1, 2, 3):
            msg = MagicMock()
            msg.topic = TOPIC_HOST_COMMAND
            msg.payload = str(host_num).encode()
            bridge._on_message(mock_client, None, msg)

        assert callback.call_count == 3
        callback.assert_any_call(1)
        callback.assert_any_call(2)
        callback.assert_any_call(3)

    @patch("logi_host.mqtt.mqtt.Client")
    def test_invalid_string_ignored(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        callback = MagicMock()
        bridge = MQTTBridge(host="localhost", port=1883, on_host_switch=callback)

        msg = MagicMock()
        msg.topic = TOPIC_HOST_COMMAND
        msg.payload = b"abc"

        bridge._on_message(mock_client, None, msg)
        callback.assert_not_called()

    @patch("logi_host.mqtt.mqtt.Client")
    def test_out_of_range_ignored(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        callback = MagicMock()
        bridge = MQTTBridge(host="localhost", port=1883, on_host_switch=callback)

        for bad_val in (0, 4, -1, 99):
            msg = MagicMock()
            msg.topic = TOPIC_HOST_COMMAND
            msg.payload = str(bad_val).encode()
            bridge._on_message(mock_client, None, msg)

        callback.assert_not_called()

    @patch("logi_host.mqtt.mqtt.Client")
    def test_wrong_topic_ignored(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        callback = MagicMock()
        bridge = MQTTBridge(host="localhost", port=1883, on_host_switch=callback)

        msg = MagicMock()
        msg.topic = "some/other/topic"
        msg.payload = b"1"

        bridge._on_message(mock_client, None, msg)
        callback.assert_not_called()

    @patch("logi_host.mqtt.mqtt.Client")
    def test_no_callback_does_not_crash(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883, on_host_switch=None)

        msg = MagicMock()
        msg.topic = TOPIC_HOST_COMMAND
        msg.payload = b"1"

        # Should not raise
        bridge._on_message(mock_client, None, msg)


class TestOnDisconnect:
    @patch("logi_host.mqtt.mqtt.Client")
    def test_unexpected_disconnect_sets_not_connected(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        bridge._connected = True
        bridge._on_disconnect(mock_client, None, None, 1)  # rc=1 -> unexpected
        assert bridge._connected is False

    @patch("logi_host.mqtt.mqtt.Client")
    def test_clean_disconnect(self, mock_client_class):
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client

        bridge = MQTTBridge(host="localhost", port=1883)
        bridge._connected = True
        bridge._on_disconnect(mock_client, None, None, 0)  # rc=0 -> clean
        assert bridge._connected is False
