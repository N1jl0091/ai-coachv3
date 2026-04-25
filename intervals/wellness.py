"""
Wellness helpers.

Pulls sleep, HRV, resting HR, steps, weight, and load metrics from
Intervals.icu and computes the derived numbers the coach needs:
TSB, ramp rate, HRV trend, sleep average, etc.
"""

from __future__ import annotations

from datetime import date as date_cls
from datetime import timedelta
from statistics import mean
from typing import Any

from intervals.client import IntervalsClient, get_client


async def get_today_snapshot(client: IntervalsClient | None = None) -> dict[str, Any]:
    """Fetch and summarise today's wellness."""
    client = client or get_client()
    today = date_cls.today().isoformat()
    raw = await client.get_wellness(today)
    return _summarise_one(raw)


async def get_week_trend(client: IntervalsClient | None = None) -> dict[str, Any]:
    """Aggregate the last 7 days of wellness into trend numbers."""
    client = client or get_client()
    today = date_cls.today()
    rows = await client.get_wellness_range(
        (today - timedelta(days=7)).isoformat(),
        today.isoformat(),
    )
    return _summarise_range(rows)


def _summarise_one(raw: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return {}
    ctl = raw.get("ctl") or 0
    atl = raw.get("atl") or 0
    return {
        "date": raw.get("id"),
        "ctl": round(ctl, 1),
        "atl": round(atl, 1),
        "tsb": round(ctl - atl, 1),
        "ramp_rate": raw.get("rampRate"),
        "hrv": raw.get("hrv"),
        "resting_hr": raw.get("restingHR"),
        "weight": raw.get("weight"),
        "sleep_hours": round((raw.get("sleepSecs") or 0) / 3600, 1) if raw.get("sleepSecs") else None,
        "sleep_score": raw.get("sleepScore"),
        "sleep_quality": raw.get("sleepQuality"),
        "readiness": raw.get("readiness"),
        "steps": raw.get("steps"),
        "fatigue": raw.get("fatigue"),
        "soreness": raw.get("soreness"),
        "mood": raw.get("mood"),
        "motivation": raw.get("motivation"),
    }


def _summarise_range(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"days": 0}
    hrv_values = [r["hrv"] for r in rows if r.get("hrv") is not None]
    sleep_values = [
        (r["sleepSecs"] or 0) / 3600 for r in rows if r.get("sleepSecs")
    ]
    rhr_values = [r["restingHR"] for r in rows if r.get("restingHR") is not None]
    return {
        "days": len(rows),
        "avg_hrv": round(mean(hrv_values), 1) if hrv_values else None,
        "avg_sleep_hours": round(mean(sleep_values), 1) if sleep_values else None,
        "avg_resting_hr": round(mean(rhr_values), 1) if rhr_values else None,
        "ctl_first": rows[-1].get("ctl") if rows else None,
        "ctl_last": rows[0].get("ctl") if rows else None,
        "atl_last": rows[0].get("atl") if rows else None,
        "rows": rows,
    }
