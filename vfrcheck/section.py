"""Vertikalschnitt aus Druckflächen-Daten: Wolken, Vereisung, Wind je Streckenabschnitt."""
import math

import numpy as np

from .assess import M_TO_FT, hour_index
from .route import course, dist_nm
from .weather import LEVELS

ICING_CLOUD = 50        # % Bewölkung auf der Druckfläche
ICING_T = (-10, 0)      # °C
LAYER_CLOUD = 50        # % Bedeckung auf einer Druckfläche, ab der sie als Wolkenschicht zählt
BKN_CLOUD = 62          # ab hier (5/8) zählt eine Schicht als Ceiling bzw. nicht durchfliegbar
ICON_WEIGHT_SHORT = 0.35  # Gewicht von ICON in der Wahrscheinlichkeit bis 48 h (ICON-D2, 2 km)
ICON_WEIGHT_LONG = 0.20   # danach (ICON-EU/global)
ON_TOP_MAX_FT = 10000   # VFR on top nur vorschlagen, wenn die Tops darunter liegen
LEE_WIND_KT = 25        # Wind auf Kammhöhe, ab dem Lee-/Rotorturbulenz gemeldet wird
MOUNTAIN_FT = 3000      # Gelände, ab dem ein Abschnitt als Gebirge gilt


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
        elev = d.get("elevation")
        out.append({"elev_ft": elev * M_TO_FT if elev is not None else None, "z": z, "cloud": np.array(g("cloud_cover"), dtype=float),
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


def cloud_layers(prof, thresh=LAYER_CLOUD):
    """Wolkenschichten [(Basis, Top, max. Bedeckung)] in ft MSL aus den Druckflächen (nur über Grund)."""
    o = np.argsort(prof["z"])
    z, c = prof["z"][o], prof["cloud"][o]
    ground = prof.get("elev_ft") or 0
    layers = []
    for i in range(len(z)):
        if np.isnan(z[i]) or np.isnan(c[i]) or z[i] < ground or c[i] < thresh:
            continue
        lo = max(ground, (z[i] + z[i - 1]) / 2) if i > 0 else z[i]
        hi = (z[i] + z[i + 1]) / 2 if i < len(z) - 1 else z[i]
        if layers and lo <= layers[-1][1] + 1:
            layers[-1] = [layers[-1][0], hi, max(layers[-1][2], c[i])]
        else:
            layers.append([lo, hi, c[i]])
    return [tuple(l) for l in layers]


def apply_icon(res, points, profs, fl, sec):
    """ICON-Wolkenschichten in die Bewertung einrechnen (erkennt Hochnebel/Stratus, den die
    Ensemble-Schätzung über die Taupunktdifferenz oft übersieht).
    Je Punkt: tiefe BKN/OVC-Schicht unter den Ceiling-Minima oder Flugweg in/nahe einer Schicht = kritisch.
    Gewicht: ICON-D2-Bereich (<= 48 h) 35 %, danach 20 %. Ergebnis = (1-w)·Ensemble + w·ICON."""
    if not sec:
        return res
    m = fl["minima"]
    lead_h = (points[0].eta - fl["departure"].now(points[0].eta.tzinfo)).total_seconds() / 3600
    w = ICON_WEIGHT_SHORT if lead_h <= 48 else ICON_WEIGHT_LONG
    icon_ok = True
    for p, r, pr in zip(points, res["points"], profs):
        r["icon_reasons"] = []
        if pr is None or r.get("fail_pct") is None:
            continue
        ground = max(r.get("elev_ft") or 0, pr.get("elev_ft") or 0)
        layers = [l for l in cloud_layers(pr) if l[2] >= BKN_CLOUD]
        if layers and layers[0][0] - ground < m["ceiling_ft_agl"]:
            r["icon_reasons"].append(f"Wolken tief (ICON) ~{max(0, layers[0][0] - ground):.0f} ft AGL")
        elif any(b - m["clearance_ft"] < p.alt_ft < t + m["clearance_ft"] for b, t, _ in layers):
            r["icon_reasons"].append("Flugweg in/nahe Wolken (ICON)")
        bad = bool(r["icon_reasons"])
        icon_ok &= not bad
        r["fail_ens"] = r["fail_pct"]
        r["fail_pct"] = (1 - w) * r["fail_pct"] + w * 100 * bad
    res["icon_ok"], res["icon_weight"], res["icon_model"] = icon_ok, w, sec[0].get("model", "ICON")
    if res["probability"] is not None:
        res["probability_ens"] = res["probability"]
        res["probability"] = (1 - w) * res["probability"] + w * 100 * icon_ok
    return res


def point_gs(fl, points, profs):
    """Groundspeed je Stützpunkt aus Wind auf Planhöhe (mit Vorhaltewinkel); TAS wenn keine Daten."""
    courses = [course(a, b) for a, b in zip(fl["waypoints"], fl["waypoints"][1:])]
    tas, out = fl["tas_kt"], []
    for p, pr in zip(points, profs):
        w = wind_at(pr, p.alt_ft) if pr is not None else None
        if w is None:
            out.append(tas)
            continue
        c = math.radians(courses[p.leg])
        along = w[0] * math.sin(c) + w[1] * math.cos(c)    # Rückenwindanteil
        cross = w[0] * math.cos(c) - w[1] * math.sin(c)
        out.append(math.sqrt(max(tas ** 2 - cross ** 2, 0)) + along)
    return out


def temp_at(prof, alt_ft):
    """Temperatur (°C) in alt_ft, zwischen Druckflächen interpoliert."""
    ok = ~np.isnan(prof["z"]) & ~np.isnan(prof["temp"])
    if ok.sum() < 2:
        return None
    o = np.argsort(prof["z"][ok])
    return float(np.interp(alt_ft, prof["z"][ok][o], prof["temp"][ok][o]))


def icing_levels(prof):
    """Höhen (ft) mit Vereisungsgefahr: Wolken + Temperatur zwischen -10 und 0 °C."""
    m = (prof["cloud"] >= ICING_CLOUD) & (prof["temp"] >= ICING_T[0]) & (prof["temp"] <= ICING_T[1])
    return prof["z"][m]


def legs(fl, points, profs, terrain=None):
    """Zusammenfassung pro Streckenabschnitt: Wind, GS, Wolkenschichten, Vereisung, Gelände, Lee-Turbulenz."""
    wps, out = fl["waypoints"], []
    terrain = terrain or [None] * len(points)
    for k, (a, b) in enumerate(zip(wps, wps[1:])):
        sel = [(p, pr) for p, pr in zip(points, profs) if p.leg == k and pr is not None]
        ter = [t for p, t in zip(points, terrain) if p.leg == k and t is not None]
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
        # Wolkenschichten (ICON): tiefste Basis, höchstes Top der untersten Schicht, Anteil bedeckter Punkte
        low = [cloud_layers(pr)[0] for _, pr in sel if cloud_layers(pr)]
        leg["clouds"] = (min(l[0] for l in low), max(l[1] for l in low), len(low) / len(sel)) if low else None
        if low and len(low) == len(sel) and leg["clouds"][1] + 1000 <= ON_TOP_MAX_FT:
            leg["on_top_ft"] = leg["clouds"][1] + 1000
        else:
            leg["on_top_ft"] = None
        leg["terrain_ft"] = max(ter) if ter else None
        # Lee-/Rotorturbulenz: starker Wind auf Kammhöhe über Gebirge
        leg["lee"] = None
        if leg["terrain_ft"] and leg["terrain_ft"] >= MOUNTAIN_FT:
            crest = [math.hypot(*w) for p, pr in sel if (w := wind_at(pr, leg["terrain_ft"])) is not None]
            if crest and max(crest) >= LEE_WIND_KT:
                leg["lee"] = max(crest)
        out.append(leg)
    return out
