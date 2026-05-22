"""
grid_advisor.py
---------------
Queries the ElectricityMaps API for real-time grid carbon intensity
and 24-hour forecast for a given zone, then recommends the best
execution window and (optionally) greenest cloud regions.

Usage:
    python grid_advisor.py --zone IN-SO --key YOUR_API_KEY [--deploy]

Output (JSON on stdout):
    {
        "zone": "IN-SO",
        "current_intensity": 412,
        "best_time": {
            "datetime_utc": "2025-05-21T14:00:00Z",
            "time_ist": "19:30",
            "intensity": 290,
            "reduction_pct": 29.6
        },
        "cloud_regions": [   <- only present when --deploy flag passed
            {"provider": "AWS", "region": "eu-north-1", "location": "Sweden",
             "intensity_gco2_kwh": 38, "label": "Greenest option"},
            ...
        ],
        "error": null
    }
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    import urllib.request as _urllib
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_BASE       = "https://api.electricitymap.org/v3"
CACHE_FILE     = os.path.expanduser("~/.carbon_grid_cache.json")
CACHE_TTL_FORE = 15 * 60   # 15 min for forecast
CACHE_TTL_CURR = 5  * 60   # 5 min for current intensity

IST_OFFSET = timedelta(hours=5, minutes=30)

# Known-green cloud regions with approximate annual avg intensity (gCO2/kWh)
# Source: published cloud provider sustainability reports + electricitymaps data
CLOUD_REGIONS = [
    {"provider": "AWS",  "region": "eu-north-1",   "location": "Sweden",  "intensity_gco2_kwh": 38},
    {"provider": "GCP",  "region": "europe-north1", "location": "Finland", "intensity_gco2_kwh": 45},
    {"provider": "GCP",  "region": "europe-west1",  "location": "Belgium", "intensity_gco2_kwh": 167},
    {"provider": "AWS",  "region": "eu-west-1",    "location": "Ireland", "intensity_gco2_kwh": 316},
    {"provider": "AWS",  "region": "eu-central-1", "location": "Germany", "intensity_gco2_kwh": 334},
    {"provider": "AWS",  "region": "us-west-2",    "location": "US-NW (Pacific)", "intensity_gco2_kwh": 122},
]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _load_cache() -> dict:
    try:
        with open(CACHE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(CACHE_FILE, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def _cache_get(cache: dict, key: str, ttl: int):
    entry = cache.get(key)
    if entry and (time.time() - entry.get("ts", 0)) < ttl:
        return entry.get("data")
    return None


def _cache_set(cache: dict, key: str, data) -> None:
    cache[key] = {"ts": time.time(), "data": data}


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def _api_get(path: str, key: str) -> dict:
    url = f"{API_BASE}{path}"
    req = _urllib.Request(url, headers={"auth-token": key})
    try:
        with _urllib.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc


def get_current(zone: str, key: str, cache: dict) -> dict:
    ckey = f"current:{zone}"
    cached = _cache_get(cache, ckey, CACHE_TTL_CURR)
    if cached:
        return cached

    data = _api_get(f"/carbon-intensity/latest?zone={zone}", key)
    _cache_set(cache, ckey, data)
    _save_cache(cache)
    return data


def get_forecast(zone: str, key: str, cache: dict) -> dict:
    ckey = f"forecast:{zone}"
    cached = _cache_get(cache, ckey, CACHE_TTL_FORE)
    if cached:
        return cached

    data = _api_get(f"/carbon-intensity/forecast?zone={zone}", key)
    _cache_set(cache, ckey, data)
    _save_cache(cache)
    return data


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

def _utc_to_ist_str(dt_str: str) -> str:
    """Convert ISO8601 UTC string to 'HH:MM IST' label."""
    try:
        # ElectricityMaps returns strings like "2025-05-21T14:00:00.000Z"
        dt_str_clean = dt_str.rstrip("Z").split(".")[0]
        dt_utc = datetime.fromisoformat(dt_str_clean).replace(tzinfo=timezone.utc)
        dt_ist = dt_utc + IST_OFFSET
        return dt_ist.strftime("%H:%M")
    except Exception:
        return dt_str


def find_best_window(forecast_data: dict, current_intensity: float) -> dict:
    """
    From the forecast array find the hour with the lowest carbon intensity
    within the next 24 h.
    """
    entries = forecast_data.get("forecast", [])
    if not entries:
        return {}

    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc + timedelta(hours=24)

    window_entries = []
    for entry in entries:
        try:
            dt_str = entry.get("datetime", "")
            dt_str_clean = dt_str.rstrip("Z").split(".")[0]
            dt = datetime.fromisoformat(dt_str_clean).replace(tzinfo=timezone.utc)
            if now_utc <= dt <= cutoff:
                window_entries.append((dt, entry.get("carbonIntensity", float("inf"))))
        except Exception:
            continue

    if not window_entries:
        return {}

    best_dt, best_intensity = min(window_entries, key=lambda x: x[1])
    reduction_pct = 0.0
    if current_intensity and current_intensity > 0:
        reduction_pct = (current_intensity - best_intensity) / current_intensity * 100

    return {
        "datetime_utc": best_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_ist":      _utc_to_ist_str(best_dt.strftime("%Y-%m-%dT%H:%M:%SZ")),
        "intensity":     round(best_intensity, 1),
        "reduction_pct": round(reduction_pct, 1),
    }


def get_cloud_recommendations(current_intensity: float) -> list:
    """
    Return cloud regions sorted by intensity, labelled relative to current zone.
    Only include regions meaningfully greener than current (>10% reduction).
    """
    results = []
    for r in sorted(CLOUD_REGIONS, key=lambda x: x["intensity_gco2_kwh"]):
        if current_intensity and r["intensity_gco2_kwh"] < current_intensity * 0.9:
            saving = (current_intensity - r["intensity_gco2_kwh"]) / current_intensity * 100
            rec = dict(r)
            rec["saving_pct"] = round(saving, 1)
            results.append(rec)

    if results:
        results[0]["label"] = "Greenest option"

    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def advise(zone: str, key: str, include_deploy: bool = False) -> dict:
    """Core function — returns the advice dict. Used by both CLI and extension."""
    cache = _load_cache()
    result = {
        "zone": zone,
        "current_intensity": None,
        "best_time": {},
        "cloud_regions": [],
        "error": None,
    }

    try:
        curr_data = get_current(zone, key, cache)
        current_intensity = curr_data.get("carbonIntensity")
        result["current_intensity"] = current_intensity

        fore_data = get_forecast(zone, key, cache)
        result["best_time"] = find_best_window(fore_data, current_intensity)

        if include_deploy:
            result["cloud_regions"] = get_cloud_recommendations(current_intensity or 0)

    except RuntimeError as exc:
        result["error"] = str(exc)

    return result


def main():
    parser = argparse.ArgumentParser(description="Carbon grid advisor")
    parser.add_argument("--zone",   default="IN-SO",  help="ElectricityMaps zone (e.g. IN-SO)")
    parser.add_argument("--key",    required=True,     help="ElectricityMaps API key")
    parser.add_argument("--deploy", action="store_true", help="Include cloud region recommendations")
    args = parser.parse_args()

    result = advise(args.zone, args.key, include_deploy=args.deploy)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
