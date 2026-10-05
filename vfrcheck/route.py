"""Flugplan laden, Wegpunkte auflösen und die Strecke in Stützpunkte mit ETA zerlegen."""
import datetime as dt
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import requests
import yaml

NM_KM = 1.852
SAMPLE_NM = 5  # Standard-Abstand der Stützpunkte entlang der Strecke (im Flugplan: sample_nm)

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
    dist: float = 0.0      # NM ab Abflug
    waypoint: bool = False
    leg: int = 0           # Index des Streckenabschnitts


def load_flight(path):
    with open(path, encoding="utf-8") as f:
        fl = yaml.safe_load(f)
    fl["minima"] = {**DEFAULT_MINIMA, **(fl.get("minima") or {})}
    fl.setdefault("alert", {}).setdefault("delta_pct", 15)
    fl["departure"] = _parse_time(fl["departure"])
    if fl.get("gpx"):
        fl["route"] = parse_gpx(Path(path).parent / fl["gpx"])
    fl["waypoints"] = [_resolve(w, fl["cruise_alt_ft"]) for w in fl["route"]]
    return fl


def _parse_time(v):
    """Abflugzeit; ohne Zeitzonenangabe gilt UTC."""
    t = v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)


def parse_gpx(path):
    """Wegpunkte aus GPX: Route (rtept), sonst Wegpunkte (wpt), sonst Track (trkpt, ausgedünnt)."""
    local = lambda e: e.tag.rsplit("}", 1)[-1]
    elems = list(ET.parse(path).getroot().iter())
    for tag in ("rtept", "wpt", "trkpt"):
        pts = [e for e in elems if local(e) == tag]
        if len(pts) >= 2:
            break
    else:
        raise ValueError(f"{path}: keine Route/Wegpunkte gefunden")
    route = []
    for k, p in enumerate(pts):
        w = {"lat": float(p.get("lat")), "lon": float(p.get("lon"))}
        name = next(((c.text or "").strip() for c in p if local(c) == "name"), "")
        if name:
            w["name"] = name
            if re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2}", name):
                w["icao"] = name
        if route and k < len(pts) - 1 and dist_nm(route[-1], w) < 2:
            continue  # Track ausdünnen
        route.append(w)
    return route


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


def course(a, b):
    """Rechtweisender Kurs a->b in Grad."""
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return math.degrees(math.atan2(y, x)) % 360


def sample(fl):
    """Stützpunkte alle ~sample_nm NM, ETA aus TAS (ohne Windkorrektur)."""
    wps, tas, step = fl["waypoints"], fl["tas_kt"], fl.get("sample_nm", SAMPLE_NM)
    t, total = fl["departure"], 0.0
    pts = [Point(wps[0]["name"], wps[0]["lat"], wps[0]["lon"], t, wps[0]["alt_ft"], 0.0, True)]
    for leg, (a, b) in enumerate(zip(wps, wps[1:])):
        d = dist_nm(a, b)
        n = max(1, math.ceil(d / step))
        for i in range(1, n + 1):
            f = i / n
            name = b["name"] if i == n else f"{a['name']}→{b['name']} +{f * d:.0f}NM"
            pts.append(Point(name, a["lat"] + f * (b["lat"] - a["lat"]), a["lon"] + f * (b["lon"] - a["lon"]),
                             t + dt.timedelta(hours=f * d / tas), b["alt_ft"], total + f * d, i == n, leg))
        t += dt.timedelta(hours=d / tas)
        total += d
    return pts
