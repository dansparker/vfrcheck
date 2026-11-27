"""Lufträume entlang der Strecke (openAIP) und NOTAMs (autorouter). Beides optional per API-Zugang."""
import math
import os

import requests

from . import gramet
from .route import dist_nm

OPENAIP = "https://api.core.openaip.net/api/airspaces"
# openAIP-Typen, die für VFR relevant sind (Rest: FIR, Airways, …)
TYPES = {1: "R", 2: "D", 3: "P", 4: "CTR", 5: "TMZ", 6: "RMZ", 7: "TMA", 8: "TRA", 9: "TSA", 13: "ATZ"}
CLASSES = "ABCDEFG?U"  # icaoClass 0..8 (8 = unklassifiziert/SUA)
NEAR_FT = 1000          # Luftraum knapp über/unter der Planhöhe ebenfalls melden
# ICAO-Präfix -> (ISO-Land für openAIP, FIRs für NOTAMs)
REGION = {"LO": ("AT", ["LOVV"]), "ED": ("DE", ["EDGG", "EDMM", "EDWW"]), "ET": ("DE", ["EDGG", "EDMM", "EDWW"]),
          "LS": ("CH", ["LSAS"]), "LI": ("IT", ["LIMM", "LIPP", "LIRR"]), "LJ": ("SI", ["LJLA"]),
          "LK": ("CZ", ["LKAA"]), "LZ": ("SK", ["LZBB"]), "LH": ("HU", ["LHCC"]), "LD": ("HR", ["LDZO"]),
          "EP": ("PL", ["EPWW"]), "LF": ("FR", ["LFFF", "LFMM", "LFEE", "LFRR", "LFBB"])}


def _regions(fl):
    """(Land, FIRs) aller Länder, deren Flugplätze in der Route vorkommen."""
    prefixes = {w["icao"][:2] for w in fl["waypoints"] if w.get("icao")}
    return [REGION[p] for p in sorted(prefixes) if p in REGION]


def _limit_ft(lim, ground_ft):
    v = lim["value"] * (3.28084 if lim["unit"] == 0 else 100 if lim["unit"] == 6 else 1)
    return v + ground_ft if lim["referenceDatum"] == 0 else v  # GND -> MSL; STD ~ MSL


def _inside(lon, lat, ring):
    hit = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
            hit = not hit
    return hit


def _fetch_country(country, key):
    items, page = [], 1
    while page:
        r = requests.get(OPENAIP, headers={"x-openaip-api-key": key}, timeout=60,
                         params={"country": country, "type": list(TYPES), "limit": 1000, "page": page})
        r.raise_for_status()
        d = r.json()
        items += d.get("items", [])
        page = d.get("nextPage")
    return items


def check_airspaces(fl, points, ground):
    """Lufträume, die die Strecke (1-NM-Raster) auf/nahe Planhöhe berührt. ground: Geländehöhe je Stützpunkt."""
    key = os.environ.get("OPENAIP_API_KEY")
    if not key:
        return []
    spaces = [a for c in {r[0] for r in _regions(fl)} for a in _fetch_country(c, key)]
    # Strecke auf 1 NM verdichten
    track = []
    for a, b in zip(points, points[1:]):
        n = max(1, round(b.dist - a.dist))
        g = ground[points.index(b)] or 0
        track += [(a.lat + (b.lat - a.lat) * i / n, a.lon + (b.lon - a.lon) * i / n,
                   a.dist + (b.dist - a.dist) * i / n, b.alt_ft, g) for i in range(n)]
    hits = []
    for sp in spaces:
        geo = sp.get("geometry") or {}
        if geo.get("type") != "Polygon":
            continue
        ring = geo["coordinates"][0]
        xs, ys = [c[0] for c in ring], [c[1] for c in ring]
        cand = [t for t in track if min(ys) <= t[0] <= max(ys) and min(xs) <= t[1] <= max(xs)
                and _inside(t[1], t[0], ring)]
        if not cand:
            continue
        g = max(t[4] for t in cand)
        lo, hi = _limit_ft(sp["lowerLimit"], g), _limit_ft(sp["upperLimit"], g)
        alt = max(t[3] for t in cand)
        if lo - NEAR_FT > alt or hi < min(t[3] for t in cand):
            continue
        inside = lo <= alt <= hi
        hits.append({"name": sp.get("name", "?"), "type": TYPES.get(sp.get("type"), "?"),
                     "cls": CLASSES[sp["icaoClass"]] if isinstance(sp.get("icaoClass"), int) else "?",
                     "lo": lo, "hi": hi, "gnd": sp["lowerLimit"]["value"] == 0, "from": cand[0][2], "to": cand[-1][2], "inside": inside,
                     "notam": sp.get("byNotam"), "on_request": sp.get("onRequest")})
    return sorted(hits, key=lambda h: h["from"])


def airspace_lines(hits):
    out = []
    for h in hits:
        lo = "GND" if h["gnd"] else f"{round(h['lo'] / 100) * 100:.0f} ft"
        flags = (" · aktiv per NOTAM" if h["notam"] else "") + (" · auf Anfrage" if h["on_request"] else "")
        cls = f" (Kl. {h['cls']})" if h["cls"] not in "?U" else ""
        where = "DURCH" if h["inside"] else "knapp unter/über"
        warn = "⚠ " if h["inside"] and h["type"] in ("R", "D", "P", "TRA", "TSA", "CTR", "TMA") else ""
        out.append(f"- {warn}{h['type']} {h['name']}{cls}: {lo}–{round(h['hi'] / 100) * 100:.0f} ft, "
                   f"{where}, NM {h['from']:.0f}–{h['to']:.0f}{flags}")
    return out


def _garmin(v):
    return v * 90 / (1 << 30)


def notams(fl, points, corridor_nm=10, max_items=15):
    """NOTAMs der Flugplätze und FIRs, gültig im Flugzeitraum, räumlich und in der Höhe nahe der Strecke."""
    headers = gramet.auth_header()
    if not headers:
        return []
    airports = [w["icao"] for w in fl["waypoints"] if w.get("icao")]
    firs = sorted({f for r in _regions(fl) for f in r[1]})
    start, end = points[0].eta.timestamp() - 3600, points[-1].eta.timestamp() + 3600
    rows, offset = [], 0
    while True:
        r = requests.get(f"{gramet.API}/notam", headers=headers, timeout=60, params={
            "itemas": "[" + ",".join(f'"{i}"' for i in airports + firs) + "]",
            "offset": offset, "limit": 100, "startvalidity": int(start), "endvalidity": int(end)})
        r.raise_for_status()
        d = r.json()
        rows += d.get("rows", [])
        offset += 100
        if offset >= d.get("total", 0) or offset >= 1000:
            break
    max_alt = max(p.alt_ft for p in points) + 2000
    out = []
    for n in rows:
        if n.get("endvalidity", math.inf) < start or n.get("startvalidity", 0) > end:
            continue
        is_ad = any(a in (n.get("itema") or []) for a in airports)
        if not is_ad:
            lat, lon, rad = n.get("lat"), n.get("lon"), n.get("radius") or 0
            if lat is None or lon is None or rad >= 100:  # FIR-weite NOTAMs überspringen
                continue
            c = {"lat": _garmin(lat), "lon": _garmin(lon)}
            if min(dist_nm(c, {"lat": p.lat, "lon": p.lon}) for p in points) > rad + corridor_nm:
                continue
            if (n.get("lower") or 0) * 100 > max_alt:  # Untergrenze in FL
                continue
        text = " ".join((n.get("iteme") or "").split())
        out.append((0 if is_ad else 1, f"- {n.get('series', '')}{n.get('number', '')}/{str(n.get('year', ''))[-2:]} "
                                       f"{'/'.join(n.get('itema') or [])}: {text[:180]}"))
    return [t for _, t in sorted(out)][:max_items]
