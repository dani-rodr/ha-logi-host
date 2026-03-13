"""Main entry point for ha-logi-host.

Lifecycle:
  1. Load config from environment variables (set by run.sh from HA add-on options)
  2. Enumerate HID devices, find the Logitech receiver
  3. Open the receiver, probe slots 1-6, find the mouse
  4. Resolve CHANGE_HOST feature index
  5. Connect to MQTT, publish HA auto-discovery
  6. Wait for host-switch commands from HA
  7. On SIGTERM/SIGINT, clean up and exit
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from .mqtt import MQTTBridge
from .protocol import find_mouse, send_change_host
from .transport import HIDTransport, TransportError, enumerate_receivers

log = logging.getLogger(__name__)

# Retry intervals
DISCOVER_RETRY_INTERVAL = 5  # seconds between receiver discovery retries
PROBE_RETRY_INTERVAL = 5  # seconds between mouse probe retries
RECONNECT_INTERVAL = 10  # seconds before re-discovering after transport loss


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def run() -> None:
    """Main application loop."""

    # -- Config from environment (set by run.sh via bashio) --------------------
    mqtt_host = os.environ.get("MQTT_HOST", "localhost")
    mqtt_port = int(os.environ.get("MQTT_PORT", "1883"))
    mqtt_user = os.environ.get("MQTT_USER") or None
    mqtt_pass = os.environ.get("MQTT_PASS") or None
    receiver_type = os.environ.get("RECEIVER_TYPE", "unifying")
    log_level = os.environ.get("LOG_LEVEL", "info")
    device_path = os.environ.get("DEVICE_PATH") or None

    _setup_logging(log_level)
    if device_path:
        log.info("ha-logi-host starting (receiver_type=%s, device_path=%s)", receiver_type, device_path)
    else:
        log.info("ha-logi-host starting (receiver_type=%s, auto-discover)", receiver_type)

    # -- Shutdown signal -------------------------------------------------------
    shutdown = threading.Event()

    def _signal_handler(signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        shutdown.set()

    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    # -- State -----------------------------------------------------------------
    transport: HIDTransport | None = None
    mqtt_bridge: MQTTBridge | None = None
    mouse_slot: int | None = None
    mouse_name: str | None = None
    change_host_feat_idx: int | None = None

    def _handle_host_switch(target_host: int) -> None:
        """Called by MQTT when HA sends a host-switch command (1-based)."""
        nonlocal transport, mouse_slot, change_host_feat_idx
        if transport is None or mouse_slot is None or change_host_feat_idx is None:
            log.warning("Cannot switch host — no mouse connected")
            return

        target_0based = target_host - 1
        log.info("Switching mouse to host %d (0-based: %d)", target_host, target_0based)
        try:
            send_change_host(transport, mouse_slot, change_host_feat_idx, target_0based)
            if mqtt_bridge:
                mqtt_bridge.publish_host(target_host)
        except TransportError as e:
            log.error("Failed to send CHANGE_HOST: %s", e)
            # Transport is broken — trigger reconnect
            _close_transport()

    def _close_transport() -> None:
        nonlocal transport, mouse_slot, change_host_feat_idx
        if transport:
            try:
                transport.close()
            except Exception:
                pass
            transport = None
        mouse_slot = None
        change_host_feat_idx = None

    # -- Main loop: discover → probe → serve → reconnect -----------------------

    try:
        # Start MQTT bridge (connects even before we find the receiver)
        mqtt_bridge = MQTTBridge(
            host=mqtt_host,
            port=mqtt_port,
            username=mqtt_user,
            password=mqtt_pass,
            on_host_switch=_handle_host_switch,
        )

        while not shutdown.is_set():
            # -- Step 1: Find the receiver -------------------------------------
            if transport is None:
                path_filter = device_path.encode() if device_path else None
                if device_path:
                    log.info("Looking for %s receiver at %s...", receiver_type, device_path)
                else:
                    log.info("Searching for %s receiver...", receiver_type)
                receivers = enumerate_receivers(receiver_type=receiver_type, path_filter=path_filter)

                if not receivers:
                    if device_path:
                        log.warning(
                            "No %s receiver found at %s. "
                            "Check the device_path in the add-on configuration. Retrying in %ds...",
                            receiver_type,
                            device_path,
                            DISCOVER_RETRY_INTERVAL,
                        )
                    else:
                        log.warning(
                            "No %s receiver found. Retrying in %ds...",
                            receiver_type,
                            DISCOVER_RETRY_INTERVAL,
                        )
                    if mqtt_bridge:
                        mqtt_bridge.publish_status("offline")
                    shutdown.wait(DISCOVER_RETRY_INTERVAL)
                    continue

                receiver = receivers[0]
                try:
                    transport = HIDTransport(receiver.path, receiver.receiver_type, receiver.pid)
                except TransportError as e:
                    log.error("Failed to open receiver: %s. Retrying in %ds...", e, DISCOVER_RETRY_INTERVAL)
                    shutdown.wait(DISCOVER_RETRY_INTERVAL)
                    continue

            # -- Step 2: Find the mouse ----------------------------------------
            if mouse_slot is None:
                log.info("Probing receiver for mouse...")
                result = find_mouse(transport)

                if result is None:
                    log.warning(
                        "No mouse found on receiver. Retrying in %ds...",
                        PROBE_RETRY_INTERVAL,
                    )
                    shutdown.wait(PROBE_RETRY_INTERVAL)
                    continue

                mouse_slot, mouse_name, change_host_feat_idx = result
                log.info(
                    "Ready: mouse='%s' slot=%d change_host_feat=0x%02X",
                    mouse_name,
                    mouse_slot,
                    change_host_feat_idx,
                )

                # Now that we know the mouse name, (re)connect MQTT with it
                try:
                    mqtt_bridge.connect(mouse_name=mouse_name)
                    mqtt_bridge.start()
                except Exception as e:
                    log.error("MQTT connection failed: %s", e)
                    shutdown.wait(RECONNECT_INTERVAL)
                    continue

            # -- Step 3: Idle — wait for MQTT commands or shutdown --------------
            # The MQTT loop runs in a background thread (via paho loop_start).
            # We just need to stay alive and periodically verify the transport
            # is still healthy by doing a non-blocking read.
            try:
                # Read with a short timeout — discards any unsolicited messages
                # (notifications, connection events, etc.) and acts as a health check.
                transport.read(timeout=2000)
            except TransportError as e:
                log.warning("Transport error (receiver disconnected?): %s", e)
                _close_transport()
                if mqtt_bridge:
                    mqtt_bridge.publish_status("offline")
                log.info("Will re-discover in %ds...", RECONNECT_INTERVAL)
                shutdown.wait(RECONNECT_INTERVAL)
                continue

    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        log.info("Shutting down...")
        if mqtt_bridge:
            mqtt_bridge.stop()
        _close_transport()
        log.info("Goodbye")
