"""VFR-Wahrscheinlichkeit: Anteil der Ensemble-Member, in denen die ganze Strecke VFR-tauglich ist."""
import datetime as dt
import math
from collections import Counter

M_TO_FT = 3.28084
BKN = 62  # % Bedeckung ab der eine Schicht als Ceiling zählt (5/8)
CONFIDENCE = 0.8  # Höhenband soll in 80 % der Member wolkenfrei sein


def _hour_index(times, eta):
    target = eta.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    if eta.minute >= 30:
        target += dt.timedelta(hours=1)
    key = target.strftime("%Y-%m-%dT%H:%M")
    return times.index(key) if key in times else None


def _member_suffixes(hourly):
    base = "cloud_cover_low"
    return [k[len(base):] for k in hourly if k.startswith(base)]


def cloud_base_msl(get, elev_ft):
    """Geschätzte Basis einer tiefen BKN/OVC-Schicht in ft MSL, None wenn keine solche Schicht."""
    low, t, td = get("cloud_cover_low"), get("temperature_2m"), get("dew_point_2m")
    if low is None or low < BKN:
        return None
    # Wolkenbasis-Schätzung über Taupunktdifferenz (~400 ft/°C); ohne T/Td konservativ 0
    return elev_ft + (max(0.0, t - td) * 400 if t is not None and td is not None else 0)


def altitude_band(bases, elev_ft, m):
    """Höhenband (ft MSL): unten Geländeabstand, oben Wolkenbasis, die 80 % der Member übertreffen."""
    lower = elev_ft + m["terrain_clearance_ft"]
    ranked = sorted(b if b is not None else math.inf for b in bases)
    base = ranked[int(len(ranked) * (1 - CONFIDENCE))] if ranked else math.inf
    return lower, base - m["clearance_ft"], base


def check_member(get, elev_ft, alt_ft, m):
    """Gründe, warum dieser Member an diesem Punkt nicht VFR-tauglich ist."""
    reasons = []
    vis = get("visibility")
    if vis is not None and vis < m["vis_m"]:
        reasons.append(f"Sicht < {m['vis_m']} m")
    base = cloud_base_msl(get, elev_ft)
    if base is not None:
        if base - elev_ft < m["ceiling_ft_agl"]:
            reasons.append(f"Ceiling < {m['ceiling_ft_agl']} ft AGL")
        elif base < alt_ft + m["clearance_ft"]:
            reasons.append("Wolkenbasis unter Reiseflughöhe")
    g = get("wind_gusts_10m")
    if g is not None and g > m["max_gust_kt"]:
        reasons.append(f"Böen > {m['max_gust_kt']} kt")
    p = get("precipitation")
    if p is not None and p > m["max_precip_mm"]:
        reasons.append("starker Niederschlag")
    c = get("cape")
    if c is not None and c > m["max_cape"]:
        reasons.append("Gewitterrisiko (CAPE)")
    return reasons


def assess(points, wx, minima):
    member_ok = None
    point_results = []
    for p, w in zip(points, wx):
        h = w["hourly"]
        idx = _hour_index(h["time"], p.eta)
        if idx is None:
            point_results.append({"point": p, "fail_pct": None, "reasons": {}})
            continue
        elev_ft = (w.get("elevation") or 0) * M_TO_FT
        suffixes = _member_suffixes(h)
        if member_ok is None:
            member_ok = {s: True for s in suffixes}
        reasons = Counter()
        fails = 0
        bases = []
        for s in suffixes:
            get = lambda v: (h.get(v + s) or [None] * (idx + 1))[idx]
            bases.append(cloud_base_msl(get, elev_ft))
            r = check_member(get, elev_ft, p.alt_ft, minima)
            if r:
                fails += 1
                member_ok[s] = False
                reasons.update(r)
        n = len(suffixes) or 1
        point_results.append({"point": p, "elev_ft": elev_ft, "fail_pct": 100 * fails / n,
                              "band": altitude_band(bases, elev_ft, minima),
                              "reasons": {k: 100 * v / n for k, v in reasons.most_common()}})
    covered = [r for r in point_results if r["fail_pct"] is not None]
    if not member_ok or len(covered) < len(point_results):
        prob = None  # außerhalb des Vorhersagezeitraums
    else:
        prob = 100 * sum(member_ok.values()) / len(member_ok)
    bands = [r["band"] for r in covered]
    route_band = (max(b[0] for b in bands), min(b[1] for b in bands)) if bands else None
    return {"probability": prob, "members": len(member_ok or {}), "points": point_results, "band": route_band}
