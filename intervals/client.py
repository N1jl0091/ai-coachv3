"""
Async Intervals.icu API client.

Wraps every HTTP call. Logs each request as a structured event. Translates
HTTP errors into IntervalsAPIError with status_code preserved.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, timedelta
from typing import Any

import httpx

from config import settings
from db.logs import log_event
from intervals.exceptions import IntervalsAPIError, IntervalsNotFoundError

logger = logging.getLogger(__name__)

BASE_URL = "https://intervals.icu/api/v1"


class IntervalsClient:
    """Async client for the Intervals.icu REST API."""

    def __init__(
        self,
        athlete_id: str | None = None,
        api_key: str | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.athlete_id = athlete_id or settings.INTERVALS_ATHLETE_ID
        self.api_key = api_key or settings.INTERVALS_API_KEY
        if not self.athlete_id or not self.api_key:
            raise RuntimeError(
                "INTERVALS_ATHLETE_ID and INTERVALS_API_KEY must be set."
            )
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            auth=("API_KEY", self.api_key),
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "IntervalsClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── READ ────────────────────────────────────────────────────────────────

    async def get_athlete(self) -> dict:
        return await self._get(f"/athlete/{self.athlete_id}")

    async def get_wellness(self, date_str: str) -> dict:
        try:
            return await self._get(f"/athlete/{self.athlete_id}/wellness/{date_str}")
        except IntervalsNotFoundError:
            return {}

    async def get_wellness_range(
        self, oldest: str, newest: str, fields: str | None = None
    ) -> list[dict]:
        params: dict[str, str] = {"oldest": oldest, "newest": newest}
        if fields:
            params["fields"] = fields
        else:
            params["fields"] = (
                "id,ctl,atl,rampRate,hrv,sleepSecs,sleepScore,sleepQuality,"
                "restingHR,weight,steps,readiness,fatigue,soreness,mood,motivation"
            )
        result = await self._get(f"/athlete/{self.athlete_id}/wellness", params=params)
        return result if isinstance(result, list) else []

    async def get_events(
        self, oldest: str, newest: str, category: str = "WORKOUT"
    ) -> list[dict]:
        params = {"oldest": oldest, "newest": newest, "category": category}
        result = await self._get(f"/athlete/{self.athlete_id}/events", params=params)
        return result if isinstance(result, list) else []

    async def get_event(self, event_id: int) -> dict:
        return await self._get(f"/athlete/{self.athlete_id}/events/{event_id}")

    async def get_activities(
        self, oldest: str, newest: str, fields: str | None = None
    ) -> list[dict]:
        params: dict[str, str] = {"oldest": oldest, "newest": newest}
        if fields:
            params["fields"] = fields
        else:
            params["fields"] = (
                "id,start_date_local,type,name,moving_time,distance,"
                "icu_training_load,average_heartrate,icu_weighted_avg_watts,"
                "icu_intensity,icu_efficiency_factor,decoupling,compliance,"
                "perceived_exertion,icu_rpe,feel,strava_id"
            )
        result = await self._get(
            f"/athlete/{self.athlete_id}/activities", params=params
        )
        return result if isinstance(result, list) else []

    async def get_activity(self, activity_id: str, intervals: bool = True) -> dict:
        params = {"intervals": str(intervals).lower()}
        return await self._get(f"/activity/{activity_id}", params=params)

    async def get_activity_power_hr(self, activity_id: str) -> dict:
        try:
            return await self._get(f"/activity/{activity_id}/power-vs-hr.json")
        except IntervalsAPIError:
            return {}

    async def get_activity_power_curve(self, activity_id: str) -> dict:
        try:
            return await self._get(f"/activity/{activity_id}/power-curve.json")
        except IntervalsAPIError:
            return {}

    async def get_sport_settings(self, sport: str | None = None) -> Any:
        if sport:
            return await self._get(
                f"/athlete/{self.athlete_id}/sport-settings/{sport}"
            )
        return await self._get(f"/athlete/{self.athlete_id}/sport-settings")

    # ── WRITE ───────────────────────────────────────────────────────────────

    async def create_event(self, event: dict) -> dict:
    # Intervals requires datetime not just date
        if event.get("start_date_local") and len(str(event["start_date_local"])) == 10:
            event["start_date_local"] = event["start_date_local"] + "T00:00:00"
    
        result = await self._post(
            f"/athlete/{self.athlete_id}/events", json=event, write=True
        )
        await log_event(
            "intervals_write",
            f"Created event '{event.get('name')}' on {event.get('start_date_local')}",
            metadata={"event_name": event.get("name"), "event_id": result.get("id") if isinstance(result, dict) else None},
        )
        return result

    async def update_event(self, event_id: int, updates: dict) -> dict:
        result = await self._put(
            f"/athlete/{self.athlete_id}/events/{event_id}", json=updates, write=True
        )
        await log_event(
            "intervals_write",
            f"Updated event {event_id}: {list(updates.keys())}",
            metadata={"event_id": event_id, "fields": list(updates.keys())},
        )
        return result

    async def delete_event(self, event_id: int) -> dict:
        result = await self._delete(
            f"/athlete/{self.athlete_id}/events/{event_id}", write=True
        )
        await log_event(
            "intervals_write",
            f"Deleted event {event_id}",
            metadata={"event_id": event_id},
            severity="warn",
        )
        return result

    async def bulk_create_events(self, events: list[dict]) -> list[dict]:
        result = await self._post(
            f"/athlete/{self.athlete_id}/events/bulk", json=events, write=True
        )
        await log_event(
            "intervals_write",
            f"Bulk created {len(events)} events",
            metadata={"count": len(events)},
        )
        return result if isinstance(result, list) else []

    async def bulk_delete_events(self, event_ids: list[int]) -> dict:
        payload = [{"id": eid} for eid in event_ids]
        result = await self._put(
            f"/athlete/{self.athlete_id}/events/bulk-delete", json=payload, write=True
        )
        await log_event(
            "intervals_write",
            f"Bulk deleted {len(event_ids)} events",
            metadata={"event_ids": event_ids},
            severity="warn",
        )
        return result

    async def move_event(self, event_id: int, new_date: str) -> dict:
        if len(new_date) == 10:
            new_date = new_date + "T00:00:00"
        return await self.update_event(event_id, {"start_date_local": new_date})

    async def update_athlete(self, updates: dict) -> dict:
        return await self._put(f"/athlete/{self.athlete_id}", json=updates, write=True)

    # ── CONTEXT SNAPSHOT ────────────────────────────────────────────────────

    async def build_context_snapshot(self) -> dict:
        """
        Parallel fetch of everything context_builder.py needs.
        Failures on individual endpoints don't kill the whole snapshot.
        """
        today = date.today().isoformat()
        week_ago = (date.today() - timedelta(days=7)).isoformat()
        two_weeks = (date.today() + timedelta(days=14)).isoformat()

        results = await asyncio.gather(
            self.get_wellness(today),
            self.get_wellness_range(week_ago, today),
            self.get_activities(week_ago, today),
            self.get_events(today, two_weeks, category="WORKOUT"),
            self.get_athlete(),
            return_exceptions=True,
        )

        def _safe(r: Any, default: Any) -> Any:
            return default if isinstance(r, Exception) else r

        return {
            "today_wellness": _safe(results[0], {}),
            "wellness_7d": _safe(results[1], []),
            "activities_7d": _safe(results[2], []),
            "planned_14d": _safe(results[3], []),
            "athlete": _safe(results[4], {}),
        }

    # ── HTTP HELPERS ────────────────────────────────────────────────────────

    async def _get(self, path: str, params: dict | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, json: Any, *, write: bool = False) -> Any:
        return await self._request("POST", path, json_body=json, write=write)

    async def _put(self, path: str, json: Any, *, write: bool = False) -> Any:
        return await self._request("PUT", path, json_body=json, write=write)

    async def _delete(self, path: str, *, write: bool = False) -> Any:
        return await self._request("DELETE", path, write=write)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: Any = None,
        write: bool = False,
    ) -> Any:
        start = time.perf_counter()
        try:
            response = await self._client.request(
                method, path, params=params, json=json_body
            )
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - start) * 1000)
            await log_event(
                "intervals_write" if write else "intervals_read",
                f"{method} {path} — network error: {exc}",
                latency_ms=elapsed,
                severity="error",
            )
            raise IntervalsAPIError(
                f"Network error calling {method} {path}: {exc}"
            ) from exc

        elapsed = int((time.perf_counter() - start) * 1000)

        if response.status_code == 404:
            await log_event(
                "intervals_write" if write else "intervals_read",
                f"{method} {path} → 404",
                latency_ms=elapsed,
                severity="warn",
            )
            raise IntervalsNotFoundError(
                f"404 from {method} {path}",
                status_code=404,
                body=response.text,
            )

        if not response.is_success:
            await log_event(
                "intervals_write" if write else "intervals_read",
                f"{method} {path} → {response.status_code}: {response.text[:200]}",
                latency_ms=elapsed,
                severity="error",
            )
            raise IntervalsAPIError(
                f"{method} {path} failed with {response.status_code}: {response.text[:200]}",
                status_code=response.status_code,
                body=response.text,
            )

        # Successful read calls log as "intervals_read"; successful writes
        # are logged at the helper level (above) so we have richer metadata.
        if not write:
            await log_event(
                "intervals_read",
                f"{method} {path}",
                latency_ms=elapsed,
            )

        if response.status_code == 204 or not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            return response.text


# Singleton convenience — most call sites just want one shared client.
_singleton: IntervalsClient | None = None


def get_client() -> IntervalsClient:
    global _singleton
    if _singleton is None:
        _singleton = IntervalsClient()
    return _singleton


async def close_client() -> None:
    global _singleton
    if _singleton is not None:
        await _singleton.close()
        _singleton = None
