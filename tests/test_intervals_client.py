"""
Tests for `intervals.client.IntervalsClient` using a mocked httpx transport.
We hit the real `IntervalsClient` code paths but don't make network calls.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from intervals.client import IntervalsClient
from intervals.exceptions import IntervalsAPIError, IntervalsNotFoundError


def _client_with_handler(handler) -> IntervalsClient:
    """Build a client whose underlying httpx uses MockTransport."""
    transport = httpx.MockTransport(handler)
    client = IntervalsClient(athlete_id="i1", api_key="key")
    # Replace the inner client with one wired to the mock transport.
    asyncio.get_event_loop()
    client._client = httpx.AsyncClient(
        base_url="https://intervals.icu/api/v1",
        auth=("API_KEY", "key"),
        transport=transport,
        headers={"Content-Type": "application/json"},
    )
    return client


@pytest.mark.asyncio
async def test_get_athlete_returns_json():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/athlete/i1"
        return httpx.Response(200, json={"id": "i1", "name": "Test Athlete"})

    client = _client_with_handler(handler)
    try:
        athlete = await client.get_athlete()
        assert athlete["id"] == "i1"
        assert athlete["name"] == "Test Athlete"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_404_maps_to_not_found_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = _client_with_handler(handler)
    try:
        with pytest.raises(IntervalsNotFoundError):
            await client.get_event(99999)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_500_maps_to_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server boom")

    client = _client_with_handler(handler)
    try:
        with pytest.raises(IntervalsAPIError) as exc_info:
            await client.get_athlete()
        assert exc_info.value.status_code == 500
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_network_error_maps_to_api_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client_with_handler(handler)
    try:
        with pytest.raises(IntervalsAPIError):
            await client.get_athlete()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_204_returns_empty_dict():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    client = _client_with_handler(handler)
    try:
        result = await client.delete_event(123)
        assert result == {}
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_create_event_posts_json():
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = request.content.decode("utf-8") if request.content else ""
        return httpx.Response(200, json={"id": 42, "ok": True})

    client = _client_with_handler(handler)
    try:
        result = await client.create_event({"category": "WORKOUT", "name": "Test"})
        assert result["id"] == 42
        assert captured["method"] == "POST"
        assert "events" in captured["path"]
        assert "WORKOUT" in captured["body"]
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_build_context_snapshot_handles_partial_failure():
    """If wellness fails but athlete succeeds, snapshot still returns sensibly."""
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/athlete/i1"):
            return httpx.Response(200, json={"id": "i1"})
        if "/wellness" in path:
            return httpx.Response(500, text="boom")
        # Default success for everything else.
        return httpx.Response(200, json=[])

    client = _client_with_handler(handler)
    try:
        snap = await client.build_context_snapshot()
        # The snapshot is a dict; keys may include athlete + safe defaults.
        assert isinstance(snap, dict)
        assert snap.get("athlete", {}).get("id") == "i1"
    finally:
        await client.close()
