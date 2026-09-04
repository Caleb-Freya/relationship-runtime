#!/usr/bin/env python3
"""Location + Weather bridge for Relationship Runtime.

A host-side collector that reads where your person is (and the weather there)
and pushes it into the Runtime's health-snapshot channel (POST /api/health),
so your AI's proactive loop can care about it — "it's cold and you're still
out, text her to bundle up", "she's been at the hospital for two hours".

Design goals:
- Runs on the same host as the Runtime; talks to it over localhost.
- Location is pluggable — an OwnTracks self-hosted file, or a manual lat/lon
  you set by hand. No wearable required.
- Weather + precise place names use AMap (高德). You bring your own AMap key
  (free to register). Non-China users can point WEATHER_PROVIDER at Open-Meteo
  (keyless, global) — see README.
- Zero third-party except the weather/geocode API you choose; the snapshot only
  ever goes to your own local Runtime.

Config: copy config.example.json -> config.json and fill it in.
"""
from __future__ import annotations
import json
import os
import ssl
import sys
import time
import urllib.request
from math import radians, sin, cos, asin, sqrt

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")

# Direct connection: strip inherited proxies (an AMap key is often IP-whitelisted,
# and the Runtime is on localhost).
_CTX = ssl.create_default_context()
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=_CTX),
)


def _get_json(url: str, timeout: float = 8.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "rr-location-weather-bridge/1.0"})
    with _OPENER.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def load_config() -> dict:
    with open(CONFIG) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Location sources
# ---------------------------------------------------------------------------
def get_location(cfg: dict) -> dict:
    """Return {lat, lon, acc, battery, tst}. Raises on failure."""
    src = cfg.get("location", {})
    kind = src.get("source", "owntracks_file")
    if kind == "manual":
        return {"lat": float(src["lat"]), "lon": float(src["lon"]),
                "acc": src.get("acc"), "battery": src.get("battery"),
                "tst": time.time()}
    if kind == "owntracks_file":
        with open(os.path.expanduser(src["path"])) as f:
            loc = json.load(f)
        return {"lat": loc["lat"], "lon": loc["lon"], "acc": loc.get("acc"),
                "battery": loc.get("batt"), "tst": loc.get("tst", time.time())}
    raise ValueError(f"unknown location source: {kind}")


# ---------------------------------------------------------------------------
# Geofence (which named place, if any)
# ---------------------------------------------------------------------------
def _haversine_m(a, b, c, d):
    r = 6371000
    dlat, dlon = radians(c - a), radians(d - b)
    h = sin(dlat / 2) ** 2 + cos(radians(a)) * cos(radians(c)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(h))


def which_place(lat: float, lon: float, cfg: dict) -> str | None:
    path = cfg.get("places_file")
    if not path:
        return None
    try:
        with open(os.path.join(HERE, path) if not os.path.isabs(path) else path) as f:
            places = json.load(f)
    except Exception:
        return None
    for pl in places:
        if _haversine_m(lat, lon, pl["lat"], pl["lon"]) <= pl.get("radius", 250):
            return pl["name"]
    return None


# ---------------------------------------------------------------------------
# Weather + reverse geocode
# ---------------------------------------------------------------------------
def _amap_adcode_place(lat, lon, key):
    url = (f"https://restapi.amap.com/v3/geocode/regeo?key={key}"
           f"&location={lon},{lat}&extensions=base")
    d = _get_json(url)
    comp = (d.get("regeocode") or {}).get("addressComponent") or {}
    adcode = comp.get("adcode") or ""
    formatted = (d.get("regeocode") or {}).get("formatted_address") or ""
    return adcode, formatted


def weather_amap(lat, lon, key) -> dict | None:
    adcode, place = _amap_adcode_place(lat, lon, key)
    out = {"place_address": place}
    if not adcode:
        return out or None
    url = (f"https://restapi.amap.com/v3/weather/weatherInfo?key={key}"
           f"&city={adcode}&extensions=base")
    lives = (_get_json(url).get("lives") or [])
    if lives:
        w = lives[0]
        out.update({"weather": w.get("weather"), "temp": _num(w.get("temperature")),
                    "wind": f"{w.get('winddirection')}风{w.get('windpower')}级",
                    "humidity": _num(w.get("humidity"))})
    return out


_WMO = {0: "晴", 1: "晴", 2: "多云", 3: "阴", 45: "雾", 48: "雾", 51: "小雨",
        53: "小雨", 55: "雨", 61: "小雨", 63: "雨", 65: "大雨", 71: "小雪",
        73: "雪", 75: "大雪", 80: "阵雨", 81: "阵雨", 82: "暴雨", 95: "雷雨"}


def weather_openmeteo(lat, lon, _key=None) -> dict | None:
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m")
    cur = (_get_json(url).get("current") or {})
    if not cur:
        return None
    return {"weather": _WMO.get(cur.get("weather_code"), "?"),
            "temp": _num(cur.get("temperature_2m")),
            "wind": f"{_num(cur.get('wind_speed_10m'))} km/h",
            "humidity": _num(cur.get("relative_humidity_2m"))}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Push to Relationship Runtime
# ---------------------------------------------------------------------------
def build_snapshot(cfg: dict) -> dict:
    loc = get_location(cfg)
    lat, lon = loc["lat"], loc["lon"]
    home_names = set(cfg.get("home_place_names", ["家", "home", "Home"]))
    place = which_place(lat, lon, cfg)

    provider = cfg.get("weather_provider", "amap")
    key = cfg.get("amap_key", "")
    if provider == "amap" and key:
        w = weather_amap(lat, lon, key) or {}
    else:
        w = weather_openmeteo(lat, lon) or {}

    snap = {
        # Runtime expects an ISO-8601 string here (it parses it), NOT an epoch float.
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "battery": loc.get("battery"),
        "location": {"lat": lat, "lon": lon, "acc": loc.get("acc")},
        "place": place or "外面",
        "out": place is None or place not in home_names,
        "loc_age_sec": int(time.time() - loc.get("tst", time.time())),
    }
    for k in ("weather", "temp", "wind", "humidity", "place_address"):
        if w.get(k) is not None:
            snap[k] = w[k]
    return snap


def push(cfg: dict, snap: dict) -> dict:
    url = cfg["runtime_url"].rstrip("/") + "/api/health"
    token = os.environ.get(cfg.get("runtime_token_env", ""), "") or cfg.get("runtime_token", "")
    req = urllib.request.Request(
        url, data=json.dumps(snap).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST")
    with _OPENER.open(req, timeout=6) as r:
        return json.loads(r.read().decode())


def main():
    cfg = load_config()
    snap = build_snapshot(cfg)
    printable = {k: v for k, v in snap.items() if k != "location"}
    print("snapshot:", json.dumps(printable, ensure_ascii=False))
    if "--dry-run" in sys.argv:
        print("(dry-run: not pushed)")
        return 0
    try:
        res = push(cfg, snap)
        print("pushed:", "ok" if res.get("ok") else res)
        return 0
    except Exception as e:
        print("push failed:", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
