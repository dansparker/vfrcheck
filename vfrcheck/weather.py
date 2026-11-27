"""Wetterdaten: Open-Meteo-Ensembles (Strecke) und METAR/TAF (Flugplätze)."""
import datetime as dt
import math
import time

import requests

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
# ICON-EPS (DWD, ~40 Member) + ECMWF-ENS (~51 Member)
MODELS = "icon_seamless_eps,ecmwf_ifs025_ensemble"
VARS = ["cloud_cover_low", "visibility", "temperature_2m", "dew_point_2m",
        "wind_gusts_10m", "precipitation", "cape"]


FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
SECTION_MODELS = ["icon_seamless", "icon_eu", "best_match"]  # ICON-D2 (2 km) -> ICON-EU (7 km) -> Standard
LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500]  # hPa, bis ~FL180
CHUNK = 50  # Orte pro Anfrage


# Zeitfenster um den Flug: genug für alternative Abflugzeiten (−3 h … +3 h, nächster Tag)
PAD_START_H, PAD_END_H = 4, 28


def _get(url, points, params, pad_end_h=PAD_END_H, pad_start_h=PAD_START_H):
    """Abfrage für viele Orte, nur Stunden rund um den Flug; liefert eine Antwort pro Punkt."""
    start = min(p.eta for p in points) - dt.timedelta(hours=pad_start_h)
    end = max(p.eta for p in points) + dt.timedelta(hours=pad_end_h)
    out = []
    for i in range(0, len(points), CHUNK):
        part = points[i:i + CHUNK]
        r = requests.get(url, params={
            "latitude": ",".join(f"{p.lat:.4f}" for p in part),
            "longitude": ",".join(f"{p.lon:.4f}" for p in part),
            "start_hour": start.strftime("%Y-%m-%dT%H:00"),
            "end_hour": end.strftime("%Y-%m-%dT%H:00"),
            "wind_speed_unit": "kn", "timezone": "UTC", **params}, timeout=120)
        if r.status_code == 429:  # Open-Meteo-Limit: einmal warten und wiederholen
            time.sleep(65)
            r = requests.get(r.url, timeout=120)
        r.raise_for_status()
        data = r.json()
        out += data if isinstance(data, list) else [data]
    return out


def ensemble(points):
    return _get(ENSEMBLE_URL, points, {"hourly": ",".join(VARS), "models": MODELS})


def section(points):
    """Deterministischer Vertikalschnitt (Wolken, Temperatur, Wind, Höhe je Druckfläche)."""
    hourly = ["freezing_level_height"] + [f"{v}_{l}hPa" for l in LEVELS
                                          for v in ("cloud_cover", "temperature", "geopotential_height",
                                                    "wind_speed", "wind_direction")]
    # Puffer am Ende: ETAs verschieben sich nach der Windkorrektur.
    # Fällt ein Modell bei Open-Meteo aus (z.B. HTTP 500 für einzelne Druckflächen), das nächste probieren.
    for model in SECTION_MODELS:
        try:
            data = _get(FORECAST_URL, points, {"hourly": ",".join(hourly), "models": model})
            for d in data:
                d["model"] = model
            return data
        except requests.HTTPError as e:
            print(f"Vertikalschnitt mit {model} fehlgeschlagen: {e.response.status_code}")
    raise RuntimeError("kein Modell für den Vertikalschnitt verfügbar")


def foehn_dp(when):
    """Druckdifferenz Bozen − Innsbruck (hPa) als Föhnindex: > +4 Südföhn, < −4 Nordföhn."""
    from types import SimpleNamespace
    pts = [SimpleNamespace(lat=46.50, lon=11.35, eta=when), SimpleNamespace(lat=47.26, lon=11.39, eta=when)]
    data = _get(FORECAST_URL, pts, {"hourly": "pressure_msl"}, pad_end_h=1, pad_start_h=1)
    key = when.replace(minute=0, second=0, microsecond=0, tzinfo=None).strftime("%Y-%m-%dT%H:%M")
    p = [d["hourly"]["pressure_msl"][d["hourly"]["time"].index(key)] for d in data]
    return None if None in p else p[0] - p[1]


NEAR_NM = 40  # Umkreis für Ersatz-METAR bei Plätzen ohne eigene Meldung


def nearest_station(lat, lon, exclude=()):
    """Nächstgelegene Station mit METAR (bis NEAR_NM): (icao, Distanz NM) oder None."""
    d = NEAR_NM / 60
    r = requests.get("https://aviationweather.gov/api/data/metar", timeout=30, params={
        "bbox": f"{lat - d:.2f},{lon - d * 1.5:.2f},{lat + d:.2f},{lon + d * 1.5:.2f}", "format": "json"})
    rows = r.json() if r.ok and r.text.strip() else []
    best = None
    for row in rows:
        if row.get("icaoId") in exclude:
            continue
        dist = math.hypot(row["lat"] - lat, (row["lon"] - lon) * math.cos(math.radians(lat))) * 60
        if dist <= NEAR_NM and (best is None or dist < best[1]):
            best = (row["icaoId"], dist)
    return best


def metar_taf(icaos, coords=None):
    """METAR/TAF je Platz. Plätze ohne eigene Meldung bekommen die nächstgelegene Station (coords: icao -> (lat, lon))."""
    out = _metar_taf(icaos)
    for icao in icaos:
        if out.get(icao, {}).get("metar") or not coords or icao not in coords:
            continue
        try:
            near = nearest_station(*coords[icao], exclude={icao})
        except Exception as e:
            print(f"Ersatz-METAR für {icao} nicht verfügbar: {e}")
            continue
        if near:
            data = _metar_taf([near[0]]).get(near[0], {})
            out[icao] = {**out.get(icao, {}), "near": (near[0], near[1]), **{f"near_{k}": v for k, v in data.items()}}
    return out


def _metar_taf(icaos):
    if not icaos:
        return {}
    ids = ",".join(icaos)
    out = {i: {} for i in icaos}
    for kind in ("metar", "taf"):
        try:
            r = requests.get(f"https://aviationweather.gov/api/data/{kind}",
                             params={"ids": ids, "format": "json"}, timeout=30)
            r.raise_for_status()
            for row in (r.json() if r.text.strip() else []):  # kleine Plätze: leere Antwort
                out.setdefault(row.get("icaoId"), {})[kind] = row.get("rawOb") or row.get("rawTAF")
        except Exception as e:  # nicht kritisch
            print(f"{kind} nicht verfügbar: {e}")
    return out
