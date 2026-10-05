"""Vertikalschnitt aus Druckflächen-Daten: Wolken, Vereisung, Wind je Streckenabschnitt."""
import math

import numpy as np

from .assess import M_TO_FT, hour_index
from .route import course, dist_nm
from .weather import LEVELS

ICING_CLOUD = 50        # % Bewölkung auf der Druckfläche
ICING_T = (-10, 0)      # °C


def profiles(points, data):
    """Pro Stützpunkt: Höhen (ft), Bewölkung, Temperatur, Wind je Druckfläche + Nullgradgrenze."""
    out = []
    for p, d in zip(points, data):
        h = d["hourly"]
        i = hour_index(h["time"], p.eta)
        if i is None:
            out.append(None)
            continue
        g = lambda v: [(h.get(f"{v}_{l}hPa") or [None] * (i + 1))[i] for l in LEVELS]
        z = np.array(g("geopotential_height"), dtype=float) * M_TO_FT
        fl = (h.get("freezing_level_height") or [None] * (i + 1))[i]
        out.append({"z": z, "cloud": np.array(g("cloud_cover"), dtype=float),
                    "temp": np.array(g("temperature"), dtype=float),
                    "ws": np.array(g("wind_speed"), dtype=float), "wd": np.array(g("wind_direction"), dtype=float),
                    "freezing_ft": fl * M_TO_FT if fl is not None else None})
    return out


def _uv(ws, wd):
    r = np.radians(wd)
    return -ws * np.sin(r), -ws * np.cos(r)


def wind_at(prof, alt_ft):
    """Wind (Richtung, kt) in alt_ft, vektoriell zwischen Druckflächen interpoliert."""
    u, v = _uv(prof["ws"], prof["wd"])
    ok = ~np.isnan(prof["z"]) & ~np.isnan(u)
    if ok.sum() < 2:
        return None
    o = np.argsort(prof["z"][ok])
    zu = prof["z"][ok][o]
    uu, vv = np.interp(alt_ft, zu, u[ok][o]), np.interp(alt_ft, zu, v[ok][o])
    return uu, vv


def icing_levels(prof):
    """Höhen (ft) mit Vereisungsgefahr: Wolken + Temperatur zwischen -10 und 0 °C."""
    m = (prof["cloud"] >= ICING_CLOUD) & (prof["temp"] >= ICING_T[0]) & (prof["temp"] <= ICING_T[1])
    return prof["z"][m]


def legs(fl, points, profs):
    """Zusammenfassung pro Streckenabschnitt: Wind auf Planhöhe, Gegen-/Seitenwind, GS, Vereisung."""
    wps, out = fl["waypoints"], []
    for k, (a, b) in enumerate(zip(wps, wps[1:])):
        sel = [(p, pr) for p, pr in zip(points, profs) if p.leg == k and pr is not None]
        uv = [w for p, pr in sel if (w := wind_at(pr, p.alt_ft)) is not None]
        leg = {"name": f"{a['name']}→{b['name']}", "dist": dist_nm(a, b), "course": course(a, b),
               "alt": b["alt_ft"], "wind": None}
        if uv:
            u, v = np.mean([w[0] for w in uv]), np.mean([w[1] for w in uv])
            spd = math.hypot(u, v)
            wdir = math.degrees(math.atan2(-u, -v)) % 360
            rel = math.radians(wdir - leg["course"])
            head, cross = spd * math.cos(rel), spd * math.sin(rel)
            gs = fl["tas_kt"] - head
            max_spd = max(math.hypot(*w) for w in uv)
            leg.update(wind=(wdir, spd), max_wind=max_spd, head=head, cross=cross, gs=gs,
                       ete_min=60 * leg["dist"] / gs if gs > 0 else None)
        ice = [z for p, pr in sel for z in icing_levels(pr) if z <= p.alt_ft + 2000]
        leg["icing_ft"] = min(ice) if ice else None
        frz = [pr["freezing_ft"] for _, pr in sel if pr["freezing_ft"] is not None]
        leg["freezing_ft"] = min(frz) if frz else None
        out.append(leg)
    return out
