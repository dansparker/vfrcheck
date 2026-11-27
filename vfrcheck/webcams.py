"""Webcams in der Nähe von Start, Ziel, Ausweichplatz und entlang der Strecke (Windy Webcams API v3).

Braucht einen kostenlosen Key (api.windy.com/keys) in WINDY_API_KEY. Es werden nur Links auf die
Webcam-Seiten ausgegeben: Bildadressen der Gratis-Stufe verfallen nach 10 Minuten.
"""
import math
import os

import requests

URL = "https://api.windy.com/webcams/api/v3/webcams"
ROUTE_STEP_NM = 30


def _km(a_lat, a_lon, b_lat, b_lon):
    return math.hypot(a_lat - b_lat, (a_lon - b_lon) * math.cos(math.radians(a_lat))) * 111.2


def nearby(lat, lon, radius_km, limit):
    key = os.environ.get("WINDY_API_KEY")
    if not key:
        return []
    r = requests.get(URL, headers={"x-windy-api-key": key}, timeout=30, params={
        "nearby": f"{lat:.3f},{lon:.3f},{radius_km}", "include": "location,urls", "limit": 20})
    r.raise_for_status()
    cams = []
    for c in r.json().get("webcams", []):
        loc, urls = c.get("location") or {}, c.get("urls") or {}
        if c.get("status", "active") != "active" or not urls.get("detail") or "latitude" not in loc:
            continue
        cams.append((_km(lat, lon, loc["latitude"], loc["longitude"]), c.get("title", "Webcam"), urls["detail"]))
    return sorted(cams)[:limit]


def lines(fl, points, alternate=None):
    """Textzeilen mit Links, gruppiert nach Ort; leer ohne API-Key."""
    if not os.environ.get("WINDY_API_KEY"):
        return []
    places = [(fl["waypoints"][0]["name"], points[0].lat, points[0].lon, 15, 3)]
    nxt = ROUTE_STEP_NM
    for p in points[1:-1]:
        if p.dist >= nxt and points[-1].dist - p.dist > ROUTE_STEP_NM / 2:
            places.append((f"Strecke NM {p.dist:.0f}", p.lat, p.lon, 10, 2))
            nxt = p.dist + ROUTE_STEP_NM
    places.append((fl["waypoints"][-1]["name"], points[-1].lat, points[-1].lon, 15, 3))
    if alternate:
        places.append((f"Ausweichplatz {alternate['name']}", alternate["lat"], alternate["lon"], 15, 2))
    out, seen = [], set()
    for name, lat, lon, radius, limit in places:
        cams = [c for c in nearby(lat, lon, radius, limit + 2) if c[2] not in seen][:limit]
        seen.update(c[2] for c in cams)
        if cams:
            out.append(f"- {name}: " + " · ".join(f"{title} ({km:.0f} km) {url}" for km, title, url in cams))
    return out
