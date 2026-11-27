"""Höchstes Gelände im Korridor um die Strecke (Copernicus-DEM 90 m über die Open-Meteo-Elevation-API)."""
import hashlib
import json
import math
import time
from pathlib import Path

import requests

URL = "https://api.open-meteo.com/v1/elevation"
M_TO_FT = 3.28084
CORRIDOR_NM = 5   # halbe Korridorbreite
CROSS_STEPS = 4   # Abtastpunkte je Seite quer zur Strecke (~1.25 NM Raster)
CACHE = Path("state") / "terrain"  # Gelände ändert sich nicht -> pro Route zwischenspeichern


def _offset(lat, lon, brg, nm):
    d = nm * 1.852 / 6371
    la1, lo1, b = map(math.radians, (lat, lon, brg))
    la2 = math.asin(math.sin(la1) * math.cos(d) + math.cos(la1) * math.sin(d) * math.cos(b))
    lo2 = lo1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(la1), math.cos(d) - math.sin(la1) * math.sin(la2))
    return math.degrees(la2), math.degrees(lo2)


def corridor_max(points, courses):
    """Geländehöhen (ft) je Stützpunkt, inkl. halbem Weg zum Vorgänger:
    {"max": Maximum im Korridor ±CORRIDOR_NM, "track": Maximum direkt auf der Kurslinie, "point": am Punkt}."""
    key = hashlib.sha1(json.dumps([(round(p.lat, 4), round(p.lon, 4)) for p in points]).encode()).hexdigest()[:16]
    cache = CACHE / f"{key}.json"
    if cache.exists():
        data = json.loads(cache.read_text())
        if isinstance(data, dict) and "point" in data:
            return data
    out = _corridor_max(points, courses)
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({k: [round(v) if v is not None else None for v in vals] for k, vals in out.items()}))
    return out


def _corridor_max(points, courses):
    rows = []  # pro Stützpunkt: Liste von (lat, lon)
    for i, p in enumerate(points):
        crs = courses[p.leg]
        centers = [(p.lat, p.lon)]
        if i:
            q = points[i - 1]
            centers.append(((p.lat + q.lat) / 2, (p.lon + q.lon) / 2))
        row = []
        for la, lo in centers:
            row.append((la, lo))
            for k in range(1, CROSS_STEPS + 1):
                for side in (-90, 90):
                    row.append(_offset(la, lo, crs + side, CORRIDOR_NM * k / CROSS_STEPS))
        rows.append(row)
    flat = [c for row in rows for c in row]
    elev = []
    for j in range(0, len(flat), 100):  # max. 100 Koordinaten pro Anfrage
        part = flat[j:j + 100]
        r = requests.get(URL, params={"latitude": ",".join(f"{a:.4f}" for a, _ in part),
                                      "longitude": ",".join(f"{b:.4f}" for _, b in part)}, timeout=60)
        if r.status_code == 429:  # Open-Meteo-Limit: einmal warten und wiederholen
            time.sleep(65)
            r = requests.get(r.url, timeout=60)
        r.raise_for_status()
        elev += r.json()["elevation"]
    out, k = {"max": [], "track": [], "point": []}, 0
    per_center = 1 + 2 * CROSS_STEPS  # Mittelpunkt + Querpunkte; Mittelpunkte liegen auf der Kurslinie
    for row in rows:
        vals = elev[k:k + len(row)]
        corridor = [v for v in vals if v is not None]
        track = [v for v in vals[::per_center] if v is not None]
        out["max"].append(max(corridor) * M_TO_FT if corridor else None)
        out["track"].append(max(track) * M_TO_FT if track else None)
        out["point"].append(vals[0] * M_TO_FT if vals[0] is not None else None)
        k += len(row)
    return out
