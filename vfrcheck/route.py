"""Flugplan laden, Wegpunkte auflösen und die Strecke in Stützpunkte mit ETA zerlegen."""
import datetime as dt
import math
from dataclasses import dataclass

import requests
import yaml

NM_KM = 1.852
SAMPLE_NM = 20  # Abstand der Stützpunkte entlang der Strecke

DEFAULT_MINIMA = {
    "vis_m": 5000,          # Mindestsicht
    "ceiling_ft_agl": 1500,  # Mindest-Wolkenuntergrenze über Grund (BKN/OVC)
    "clearance_ft": 500,     # Abstand Reiseflughöhe -> Wolkenbasis
    "terrain_clearance_ft": 1000,  # Mindestabstand zum Gelände (Stützpunkt-Höhe, nicht Grat!)
    "max_gust_kt": 25,
    "max_precip_mm": 2.0,    # pro Stunde
    "max_cape": 800,         # J/kg, Gewitterneigung
}


@dataclass
class Point:
    name: str
    lat: float
    lon: float
    eta: dt.datetime
    alt_ft: float


def load_flight(path):
    with open(path, encoding="utf-8") as f:
        fl = yaml.safe_load(f)
    fl["minima"] = {**DEFAULT_MINIMA, **(fl.get("minima") or {})}
    fl.setdefault("alert", {}).setdefault("delta_pct", 15)
    fl["departure"] = _parse_time(fl["departure"])
    fl["waypoints"] = [_resolve(w, fl["cruise_alt_ft"]) for w in fl["route"]]
    return fl


def _parse_time(v):
    t = v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v))
    if t.tzinfo is None:
        raise ValueError("departure braucht eine Zeitzone, z.B. 2026-10-10T09:00:00+02:00")
    return t.astimezone(dt.timezone.utc)


def _resolve(w, cruise_alt):
    if isinstance(w, str):
        w = {"icao": w}
    if "lat" not in w:
        r = requests.get("https://aviationweather.gov/api/data/airport",
                         params={"ids": w["icao"], "format": "json"}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            raise ValueError(f"Flugplatz {w['icao']} nicht gefunden – bitte lat/lon angeben")
        w = {**w, "lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
    w.setdefault("name", w.get("icao", f"{w['lat']:.2f},{w['lon']:.2f}"))
    w.setdefault("alt_ft", cruise_alt)
    return w


def dist_nm(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h)) / NM_KM


def sample(fl):
    """Stützpunkte alle ~SAMPLE_NM NM, ETA aus TAS (ohne Windkorrektur)."""
    wps, tas = fl["waypoints"], fl["tas_kt"]
    t = fl["departure"]
    pts = [Point(wps[0]["name"], wps[0]["lat"], wps[0]["lon"], t, wps[0]["alt_ft"])]
    for a, b in zip(wps, wps[1:]):
        d = dist_nm(a, b)
        n = max(1, math.ceil(d / SAMPLE_NM))
        for i in range(1, n + 1):
            f = i / n
            name = b["name"] if i == n else f"{a['name']}→{b['name']} {f * d:.0f}NM"
            pts.append(Point(name, a["lat"] + f * (b["lat"] - a["lat"]), a["lon"] + f * (b["lon"] - a["lon"]),
                             t + dt.timedelta(hours=f * d / tas), b["alt_ft"]))
        t += dt.timedelta(hours=d / tas)
    return pts
