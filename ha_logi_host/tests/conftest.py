"""Shared test fixtures for ha-logi-host.

Provides FakeTransport — a purpose-built stub that replaces the real HIDTransport.
Captures writes, replays pre-queued reads, and tracks close state.
"""

from __future__ import annotations

import pytest


class FakeTransport:
    """In-memory stub for HIDTransport. No real HID I/O."""

    def __init__(self, responses: list[bytes] | None = None, receiver_type: str = "unifying", pid: int = 0xC52B):
        self.path = b"/dev/hidraw_fake"
        self.receiver_type = receiver_type
        self.pid = pid
        self.written: list[bytes] = []
        self.closed = False
        self._responses: list[bytes | None] = list(responses) if responses else []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def read(self, timeout: int = 500) -> bytes | None:
        if self._responses:
            return self._responses.pop(0)
        return None

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_transport():
    """Empty FakeTransport — no pre-loaded responses."""
    return FakeTransport()


@pytest.fixture
def make_fake_transport():
    """Factory fixture: create FakeTransport instances with pre-loaded response queues."""

    def _make(
        responses: list[bytes] | None = None,
        receiver_type: str = "unifying",
        pid: int = 0xC52B,
    ) -> FakeTransport:
        return FakeTransport(responses=responses, receiver_type=receiver_type, pid=pid)

    return _make
