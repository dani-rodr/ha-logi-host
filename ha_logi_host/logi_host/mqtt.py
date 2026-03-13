"""MQTT client for Home Assistant integration.

Handles:
  - Connection to the HA Mosquitto broker
  - Publishing HA MQTT auto-discovery configs (select + sensor entities)
  - Publishing device state (current host, availability)
  - Subscribing to command topics and dispatching host-switch requests
"""

from __future__ import annotations

import json
import logging
from typing import Callable

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)

# -- MQTT topic layout ---------------------------------------------------------

_PREFIX = "ha_logi_host"

TOPIC_HOST_STATE = f"{_PREFIX}/mouse/host/state"
TOPIC_HOST_COMMAND = f"{_PREFIX}/mouse/host/set"
TOPIC_STATUS = f"{_PREFIX}/mouse/status"

# HA auto-discovery topics
_DISCOVERY_SELECT = f"homeassistant/select/{_PREFIX}_mouse_host/config"
_DISCOVERY_SENSOR = f"homeassistant/sensor/{_PREFIX}_mouse_status/config"

# -- HA auto-discovery payloads ------------------------------------------------

_DEVICE_INFO = {
    "identifiers": [_PREFIX],
    "name": "Logi Host Switch",
    "manufacturer": "Logitech",
    "model": "Unifying/Bolt Receiver",
    "sw_version": "0.1.0",
}


def _select_discovery_payload(mouse_name: str | None) -> dict:
    """Build the MQTT auto-discovery payload for the host select entity."""
    name = f"{mouse_name} Host" if mouse_name else "Mouse Host"
    return {
        "name": name,
        "unique_id": f"{_PREFIX}_mouse_host",
        "command_topic": TOPIC_HOST_COMMAND,
        "state_topic": TOPIC_HOST_STATE,
        "availability_topic": TOPIC_STATUS,
        "options": ["1", "2", "3"],
        "icon": "mdi:mouse",
        "device": _DEVICE_INFO,
    }


def _sensor_discovery_payload() -> dict:
    """Build the MQTT auto-discovery payload for the connection status sensor."""
    return {
        "name": "Receiver Status",
        "unique_id": f"{_PREFIX}_receiver_status",
        "state_topic": TOPIC_STATUS,
        "icon": "mdi:usb",
        "device": _DEVICE_INFO,
    }


# -- MQTTBridge ----------------------------------------------------------------


class MQTTBridge:
    """Manages the MQTT connection to Home Assistant's broker.

    Publishes auto-discovery configs so HA automatically creates entities.
    Subscribes to the command topic and calls the provided callback on
    host-switch requests.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str | None = None,
        password: str | None = None,
        on_host_switch: Callable[[int], None] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_host_switch = on_host_switch
        self._mouse_name: str | None = None
        self._connected = False

        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=_PREFIX,
        )

        if username:
            self._client.username_pw_set(username, password)

        # Set Last Will — if we disconnect unexpectedly, HA sees "offline"
        self._client.will_set(TOPIC_STATUS, "offline", qos=1, retain=True)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._client.on_disconnect = self._on_disconnect

    def connect(self, mouse_name: str | None = None) -> None:
        """Connect to the MQTT broker and publish auto-discovery configs."""
        self._mouse_name = mouse_name
        log.info("Connecting to MQTT broker at %s:%d", self._host, self._port)
        self._client.connect(self._host, self._port, keepalive=60)

    def start(self) -> None:
        """Start the MQTT network loop in a background thread."""
        self._client.loop_start()

    def stop(self) -> None:
        """Publish offline status and disconnect cleanly."""
        if self._connected:
            self.publish_status("offline")
        self._client.loop_stop()
        self._client.disconnect()
        log.info("MQTT disconnected")

    def publish_host(self, host: int) -> None:
        """Publish the current host number (1-based) to the state topic."""
        self._client.publish(TOPIC_HOST_STATE, str(host), qos=1, retain=True)
        log.info("Published host state: %d", host)

    def publish_status(self, status: str) -> None:
        """Publish availability status ('online' or 'offline')."""
        self._client.publish(TOPIC_STATUS, status, qos=1, retain=True)

    # -- MQTT callbacks --------------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        if rc != 0:
            log.error("MQTT connection failed with code %d", rc)
            return

        self._connected = True
        log.info("MQTT connected to %s:%d", self._host, self._port)

        # Publish auto-discovery configs
        client.publish(
            _DISCOVERY_SELECT,
            json.dumps(_select_discovery_payload(self._mouse_name)),
            qos=1,
            retain=True,
        )
        client.publish(
            _DISCOVERY_SENSOR,
            json.dumps(_sensor_discovery_payload()),
            qos=1,
            retain=True,
        )
        log.info("Published HA auto-discovery configs")

        # Publish online status
        self.publish_status("online")

        # Subscribe to command topic
        client.subscribe(TOPIC_HOST_COMMAND, qos=1)
        log.info("Subscribed to %s", TOPIC_HOST_COMMAND)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage) -> None:
        if msg.topic != TOPIC_HOST_COMMAND:
            return

        payload = msg.payload.decode("utf-8", errors="replace").strip()
        log.info("Received host switch command: '%s'", payload)

        try:
            host = int(payload)
        except ValueError:
            log.warning("Invalid host value: '%s' (expected 1, 2, or 3)", payload)
            return

        if host not in (1, 2, 3):
            log.warning("Host %d out of range (expected 1, 2, or 3)", host)
            return

        if self._on_host_switch:
            self._on_host_switch(host)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None) -> None:
        self._connected = False
        if rc != 0:
            log.warning("MQTT unexpected disconnect (rc=%d), will auto-reconnect", rc)
        else:
            log.info("MQTT disconnected cleanly")
