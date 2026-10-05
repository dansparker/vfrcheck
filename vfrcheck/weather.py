"""Wetterdaten: Open-Meteo-Ensembles (Strecke) und METAR/TAF (Flugplätze)."""
import requests

ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
# ICON-EPS (DWD, ~40 Member) + ECMWF-ENS (~51 Member)
MODELS = "icon_seamless_eps,ecmwf_ifs025_ensemble"
VARS = ["cloud_cover_low", "visibility", "temperature_2m", "dew_point_2m",
        "wind_gusts_10m", "precipitation", "cape"]


def ensemble(points):
    """Eine Anfrage für alle Stützpunkte; liefert Liste von Open-Meteo-Antworten."""
    r = requests.get(ENSEMBLE_URL, params={
        "latitude": ",".join(f"{p.lat:.4f}" for p in points),
        "longitude": ",".join(f"{p.lon:.4f}" for p in points),
        "hourly": ",".join(VARS),
        "models": MODELS,
        "forecast_days": 16,
        "wind_speed_unit": "kn",
        "timezone": "UTC",
    }, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else [data]


def metar_taf(icaos):
    if not icaos:
        return {}
    ids = ",".join(icaos)
    out = {i: {} for i in icaos}
    for kind in ("metar", "taf"):
        try:
            r = requests.get(f"https://aviationweather.gov/api/data/{kind}",
                             params={"ids": ids, "format": "json"}, timeout=30)
            r.raise_for_status()
            for row in r.json() or []:
                out.setdefault(row.get("icaoId"), {})[kind] = row.get("rawOb") or row.get("rawTAF")
        except Exception as e:  # nicht kritisch
            print(f"{kind} nicht verfügbar: {e}")
    return out
