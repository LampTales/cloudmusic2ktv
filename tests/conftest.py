from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    """Keep the test suite hermetic, including every NetEase API test."""

    def denied_connection(*args, **kwargs):
        raise AssertionError("tests must mock network access instead of opening a real socket")

    monkeypatch.setattr(socket.socket, "connect", denied_connection)
    monkeypatch.setattr(socket.socket, "connect_ex", denied_connection)
