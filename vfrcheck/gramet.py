"""GRAMET-Querschnitt über die autorouter.aero-API (kostenloser Account nötig)."""
import os

import requests

API = "https://api.autorouter.aero/v1.0"


def _coord(w):
    lat, lon = w["lat"], w["lon"]
    return (f"{int(abs(lat)):02d}{int(abs(lat) % 1 * 60):02d}{'N' if lat >= 0 else 'S'}"
            f"{int(abs(lon)):03d}{int(abs(lon) % 1 * 60):02d}{'E' if lon >= 0 else 'W'}")


def fetch(fl, points):
    user, pw = os.environ.get("AUTOROUTER_USER"), os.environ.get("AUTOROUTER_PASS")
    if not (user and pw):
        return None
    try:
        tok = requests.post(f"{API}/oauth2/token", data={
            "grant_type": "client_credentials", "client_id": user, "client_secret": pw}, timeout=30)
        tok.raise_for_status()
        eet = int((points[-1].eta - points[0].eta).total_seconds())
        r = requests.get(f"{API}/met/gramet", headers={"Authorization": f"Bearer {tok.json()['access_token']}"},
                         params={"waypoints": " ".join(w.get("icao") or _coord(w) for w in fl["waypoints"]),
                                 "altitude": fl["cruise_alt_ft"], "departuretime": int(fl["departure"].timestamp()),
                                 "totaleet": eet, "format": "png"}, timeout=90)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"GRAMET nicht verfügbar: {e}")
        return None
