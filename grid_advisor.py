"""
grid_advisor.py
---------------
Queries the ElectricityMaps API for real-time grid carbon intensity
and 24-hour forecast for a given zone, then recommends the best
execution window and (optionally) greenest cloud regions.

Usage:
    python grid_advisor.py --zone auto --key YOUR_API_KEY [--deploy]

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
from urllib.parse import urlencode

try:
    import urllib.request as _urllib
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
API_BASE       = "https://api.electricitymap.org"
CACHE_FILE     = os.path.expanduser("~/.carbon_grid_cache.json")
CACHE_TTL_FORE = 15 * 60   # 15 min for forecast
CACHE_TTL_CURR = 5  * 60   # 5 min for current intensity
CACHE_TTL_GEO  = 24 * 60 * 60  # 24 h for IP->(lat,lon)->zone

IST_OFFSET = timedelta(hours=5, minutes=30)




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

def _http_get_json(url: str, headers: dict | None = None, timeout_s: int = 10) -> dict:
    if not _HAS_URLLIB:
        raise RuntimeError("urllib is not available in this Python environment")

    req = _urllib.Request(url, headers=headers or {})
    try:
        with _urllib.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode())
    except Exception as exc:
        raise RuntimeError(f"HTTP request failed: {exc}") from exc


def _api_get(path: str, key: str | None) -> dict:
    url = f"{API_BASE}{path}"
    headers = {"auth-token": key} if key else {}
    try:
        return _http_get_json(url, headers=headers, timeout_s=10)
    except RuntimeError as exc:
        raise RuntimeError(f"API request failed: {exc}") from exc


def _api_get_v4(path: str, key: str | None) -> dict:
    if path.startswith("/v4/"):
        return _api_get(path, key)
    return _api_get(f"/v4{path}", key)


def _api_get_v3(path: str, key: str | None) -> dict:
    if path.startswith("/v3/"):
        return _api_get(path, key)
    return _api_get(f"/v3{path}", key)


def _api_get_compat(v4_path: str, v3_path: str, key: str | None) -> dict:
    """
    ElectricityMaps has both v3 and v4 APIs in the wild. Prefer v4 (needed for
    data-centers), but fall back to v3 for older tokens.
    """
    try:
        return _api_get(v4_path, key)
    except RuntimeError:
        return _api_get(v3_path, key)


def _parse_zone_key(zone_resp: dict) -> str | None:
    if not isinstance(zone_resp, dict):
        return None
    for k in ("zone", "zoneKey", "zone_key"):
        v = zone_resp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    data = zone_resp.get("data")
    if isinstance(data, dict):
        for k in ("zone", "zoneKey", "zone_key"):
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def _detect_lat_lon(cache: dict) -> tuple[float, float] | None:
    """
    Best-effort IP geolocation. Returns (lat, lon) or None.

    Privacy: this makes an outbound request to a public IP geolocation
    service unless ELECTRICITYMAPS_LAT/LON are provided.
    """
    lat_env = os.environ.get("ELECTRICITYMAPS_LAT") or os.environ.get("CARBON_ANALYZER_LAT")
    lon_env = os.environ.get("ELECTRICITYMAPS_LON") or os.environ.get("CARBON_ANALYZER_LON")
    if lat_env and lon_env:
        try:
            return float(lat_env), float(lon_env)
        except Exception:
            return None

    cached = _cache_get(cache, "geo:latlon", CACHE_TTL_GEO)
    if cached and isinstance(cached, dict):
        try:
            return float(cached["lat"]), float(cached["lon"])
        except Exception:
            pass

    providers = [
        ("https://ipapi.co/json/", ("latitude", "longitude")),
        ("https://ipwho.is/", ("latitude", "longitude")),
    ]

    for url, (k_lat, k_lon) in providers:
        try:
            data = _http_get_json(url, headers={}, timeout_s=5)
            lat = data.get(k_lat)
            lon = data.get(k_lon)
            if lat is None or lon is None:
                continue
            lat_f = float(lat)
            lon_f = float(lon)
            _cache_set(cache, "geo:latlon", {"lat": lat_f, "lon": lon_f})
            _save_cache(cache)
            return lat_f, lon_f
        except Exception:
            continue

    return None


def detect_local_zone(key: str | None, cache: dict) -> str | None:
    """
    Resolve the user's ElectricityMaps zone.

    Priority:
      1) Explicit env override (ELECTRICITYMAPS_ZONE / CARBON_ANALYZER_ZONE)
      2) Map IP-derived (lat,lon) to zone via /zone
    """
    zone_env = os.environ.get("ELECTRICITYMAPS_ZONE") or os.environ.get("CARBON_ANALYZER_ZONE")
    if zone_env and zone_env.strip():
        return zone_env.strip()

    latlon = _detect_lat_lon(cache)
    if not latlon:
        return None

    lat, lon = latlon
    q = urlencode({"lat": f"{lat:.6f}", "lon": f"{lon:.6f}"})
    zone_resp = _api_get_compat(f"/v4/zone?{q}", f"/v3/zone?{q}", key)
    return _parse_zone_key(zone_resp)


def get_current(zone: str, key: str, cache: dict) -> dict:
    ckey = f"current:{zone}"
    cached = _cache_get(cache, ckey, CACHE_TTL_CURR)
    if cached:
        return cached

    q = urlencode({"zone": zone})
    data = _api_get_compat(f"/v4/carbon-intensity/latest?{q}", f"/v3/carbon-intensity/latest?{q}", key)
    _cache_set(cache, ckey, data)
    _save_cache(cache)
    return data


def get_forecast(zone: str, key: str, cache: dict) -> dict:
    ckey = f"forecast:{zone}"
    cached = _cache_get(cache, ckey, CACHE_TTL_FORE)
    if cached:
        return cached

    q = urlencode({"zone": zone})
    data = _api_get_compat(f"/v4/carbon-intensity/forecast?{q}", f"/v3/carbon-intensity/forecast?{q}", key)
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
    Dynamic Volatility Earliest Good Enough:
      1) Compute min/max intensity in next 24h window.
      2) variance = (max - min) / max.
      3) dynamic_threshold = variance / 2.
      4) Pick the earliest hour that achieves at least dynamic_threshold
         reduction vs current_intensity.
      5) If none qualify, fall back to the absolute minimum in the window.
    """
    entries = forecast_data.get("forecast", [])
    if not entries:
        return {}

    now_utc = datetime.now(timezone.utc)
    cutoff  = now_utc + timedelta(hours=24)

    window_entries: list[tuple[datetime, float]] = []
    for entry in entries:
        try:
            dt_str = entry.get("datetime", "")
            dt_str_clean = dt_str.rstrip("Z").split(".")[0]
            dt = datetime.fromisoformat(dt_str_clean).replace(tzinfo=timezone.utc)
            if now_utc <= dt <= cutoff:
                intensity = entry.get("carbonIntensity", None)
                if intensity is None:
                    continue
                window_entries.append((dt, float(intensity)))
        except Exception:
            continue

    if not window_entries:
        return {}

    intensities = [i for _, i in window_entries]
    max_intensity = max(intensities) if intensities else 0.0
    min_intensity = min(intensities) if intensities else 0.0

    variance = 0.0
    if max_intensity and max_intensity > 0:
        variance = (max_intensity - min_intensity) / max_intensity

    dynamic_threshold = variance / 2.0

    # If current intensity is unknown/unusable, fall back to the minimum.
    if not current_intensity or current_intensity <= 0:
        chosen_dt, chosen_intensity = min(window_entries, key=lambda x: x[1])
    else:
        target_intensity = current_intensity * (1.0 - dynamic_threshold)
        window_entries_sorted = sorted(window_entries, key=lambda x: x[0])
        match = next((pair for pair in window_entries_sorted if pair[1] <= target_intensity), None)
        if match is not None:
            chosen_dt, chosen_intensity = match
        else:
            chosen_dt, chosen_intensity = min(window_entries, key=lambda x: x[1])

    reduction_pct = 0.0
    if current_intensity and current_intensity > 0:
        reduction_pct = (current_intensity - chosen_intensity) / current_intensity * 100

    return {
        "datetime_utc": chosen_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "time_ist":      _utc_to_ist_str(chosen_dt.strftime("%Y-%m-%dT%H:%M:%SZ")),
        "intensity":     round(chosen_intensity, 1),
        "reduction_pct": round(reduction_pct, 1),
    }


def _extract_list(resp: dict | list) -> list:
    if isinstance(resp, list):
        return resp
    if not isinstance(resp, dict):
        return []
    for k in ("dataCenters", "data_centers", "datacenters", "data", "results", "items"):
        v = resp.get(k)
        if isinstance(v, list):
            return v
    return []


def _friendly_provider(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p == "aws":
        return "AWS"
    if p == "gcp":
        return "GCP"
    return provider.upper() if provider else "UNKNOWN"


def _get_data_centers(provider: str, key: str, cache: dict) -> list[dict]:
    ckey = f"datacenters:{provider}"
    cached = _cache_get(cache, ckey, CACHE_TTL_FORE)
    if cached:
        return cached

    resp = None
    last_err = None
    for param_name in ("cloudCenterProvider", "dataCenterProvider"):
        q = urlencode({param_name: provider, "page": 1, "limit": 1000})
        try:
            resp = _api_get_v4(f"/v4/data-centers?{q}", key)
            break
        except RuntimeError as exc:
            last_err = exc
            resp = None

    if resp is None:
        raise RuntimeError(f"Could not list data centers for {provider}: {last_err}")

    items = _extract_list(resp)
    seen = set()
    out = []
    for dc in items:
        if not isinstance(dc, dict):
            continue
        region = dc.get("region") or dc.get("dataCenterRegion") or dc.get("data_center_region")
        if not isinstance(region, str) or not region.strip():
            continue
        region = region.strip()
        if region in seen:
            continue
        seen.add(region)
        out.append(dc)

    _cache_set(cache, ckey, out)
    _save_cache(cache)
    return out


def _get_dc_intensity(provider: str, region: str, key: str, cache: dict) -> float | None:
    ckey = f"dc_intensity:{provider}:{region}"
    cached = _cache_get(cache, ckey, CACHE_TTL_CURR)
    if cached and isinstance(cached, dict):
        v = cached.get("carbonIntensity")
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    q = urlencode({"dataCenterProvider": provider, "dataCenterRegion": region})
    data = _api_get_v4(f"/v4/carbon-intensity/latest?{q}", key)
    _cache_set(cache, ckey, data)
    _save_cache(cache)
    try:
        ci = data.get("carbonIntensity")
        return float(ci) if ci is not None else None
    except Exception:
        return None


def get_cloud_recommendations_dynamic(
    current_intensity: float,
    key: str,
    cache: dict,
    providers: tuple[str, ...] = ("aws", "gcp"),
    max_regions_per_provider: int = 40,
    max_seconds: float = 10.0,
) -> list[dict]:
    """
    Live cloud region recommendation via ElectricityMaps data center mapping.

    Returns a list sorted by live carbon intensity, and only includes regions
    that are meaningfully greener than the current zone (>10% reduction).
    """
    start = time.time()
    results: list[dict] = []

    for provider in providers:
        dcs = _get_data_centers(provider, key, cache)[: max_regions_per_provider]
        for dc in dcs:
            if time.time() - start > max_seconds:
                break
            region = dc.get("region") or dc.get("dataCenterRegion") or dc.get("data_center_region")
            if not isinstance(region, str) or not region.strip():
                continue
            region = region.strip()

            ci = _get_dc_intensity(provider, region, key, cache)
            if ci is None:
                continue

            if current_intensity and ci >= current_intensity * 0.9:
                continue

            saving = 0.0
            if current_intensity and current_intensity > 0:
                saving = (current_intensity - ci) / current_intensity * 100

            location = (
                dc.get("location")
                or dc.get("city")
                or dc.get("country")
                or dc.get("name")
                or ""
            )

            results.append(
                {
                    "provider": _friendly_provider(provider),
                    "region": region,
                    "location": location if isinstance(location, str) else "",
                    "intensity_gco2_kwh": round(ci, 1),
                    "saving_pct": round(saving, 1),
                }
            )

    results.sort(key=lambda r: r.get("intensity_gco2_kwh", float("inf")))
    if results:
        results[0]["label"] = "Greenest option"
    return results


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def advise(zone: str, key: str, include_deploy: bool = False) -> dict:
    """Core function — returns the advice dict. Used by both CLI and extension."""
    cache = _load_cache()

    resolved_zone = zone
    if not resolved_zone or resolved_zone.strip().lower() in {"auto", "detect", "ip"}:
        resolved_zone = detect_local_zone(key, cache) or zone

    result = {
        "zone": resolved_zone,
        "current_intensity": None,
        "best_time": {},
        "cloud_regions": [],
        "error": None,
    }

    try:
        if not resolved_zone or resolved_zone.strip().lower() in {"auto", "detect", "ip"}:
            raise RuntimeError("Could not auto-detect a local ElectricityMaps zone. Pass --zone <ZONE>.")

        curr_data = get_current(resolved_zone, key, cache)
        current_intensity = curr_data.get("carbonIntensity")
        result["current_intensity"] = current_intensity

        fore_data = get_forecast(resolved_zone, key, cache)
        result["best_time"] = find_best_window(fore_data, current_intensity)

        if include_deploy:
            result["cloud_regions"] = get_cloud_recommendations_dynamic(current_intensity or 0, key, cache)

    except RuntimeError as exc:
        result["error"] = str(exc)

    return result


def main():
    parser = argparse.ArgumentParser(description="Carbon grid advisor")
    parser.add_argument("--zone",   default="auto",  help="ElectricityMaps zone (e.g. IN-SO) or 'auto'")
    parser.add_argument("--key",    required=True,     help="ElectricityMaps API key")
    parser.add_argument("--deploy", action="store_true", help="Include cloud region recommendations")
    args = parser.parse_args()

    result = advise(args.zone, args.key, include_deploy=args.deploy)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
