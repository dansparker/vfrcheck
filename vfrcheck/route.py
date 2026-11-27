"""Flugplan laden, Wegpunkte auflösen und die Strecke in Stützpunkte mit ETA zerlegen."""
import csv
import datetime as dt
import io
import json
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
    plan_ft: float = 0.0   # geplante Höhe (alt_ft wird zum tatsächlichen Steig-/Sinkprofil)

    def __post_init__(self):
        self.plan_ft = self.plan_ft or self.alt_ft


AIRCRAFT = Path("aircraft")


def load_aircraft(reg):
    """Flugzeugprofil aircraft/<Kennzeichen>.yaml: tas_kt, performance, minima (alles optional)."""
    p = AIRCRAFT / f"{reg.upper()}.yaml"
    if not p.exists():
        raise ValueError(f"Flugzeugprofil {reg} nicht gefunden (aircraft/{reg.upper()}.yaml)")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def load_flight(path):
    with open(path, encoding="utf-8") as f:
        fl = yaml.safe_load(f)
    if fl.get("aircraft"):  # Werte aus dem Flugzeugprofil, Angaben im Flugplan haben Vorrang
        ac = load_aircraft(fl["aircraft"])
        if not fl.get("tas_kt"):
            fl["tas_kt"] = ac.get("tas_kt")
        fl["performance"] = {**(ac.get("performance") or {}), **(fl.get("performance") or {})}
        fl["minima"] = {**(ac.get("minima") or {}), **(fl.get("minima") or {})}
    if not fl.get("tas_kt"):
        raise ValueError("tas_kt fehlt (im Flugplan oder im Flugzeugprofil angeben)")
    fl["minima"] = {**DEFAULT_MINIMA, **(fl.get("minima") or {})}
    fl.setdefault("alert", {}).setdefault("delta_pct", 15)
    fl["departure"] = _parse_time(fl["departure"])
    if fl.get("gpx") or fl.get("route_file"):
        fl["route"] = parse_route_file(Path(path).parent / (fl.get("route_file") or fl["gpx"]))
    fl["waypoints"] = [_resolve(w, fl["cruise_alt_ft"]) for w in fl["route"]]
    return fl


def _parse_time(v):
    """Abflugzeit; ohne Zeitzonenangabe gilt UTC."""
    t = v if isinstance(v, dt.datetime) else dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t.replace(tzinfo=dt.timezone.utc) if t.tzinfo is None else t.astimezone(dt.timezone.utc)


def parse_route_file(path):
    """Route aus GPX, Garmin-Flugplan (.fpl) oder SkyDemon (.flightplan)."""
    suffix = Path(path).suffix.lower()
    route = parse_fpl(path) if suffix == ".fpl" else parse_skydemon(path) if suffix == ".flightplan" else parse_gpx(path)
    for w in (route[0], route[-1]):  # Start/Ziel ohne ICAO: nächsten Flugplatz suchen (für METAR, Luftraum)
        if not w.get("icao"):
            icao = nearest_airport(w["lat"], w["lon"])
            if icao:
                w["icao"] = icao
                if not w.get("name") or re.fullmatch(r"WP\d+", w["name"]):
                    w["name"] = icao
    return route


def _local(e):
    return e.tag.rsplit("}", 1)[-1]


def parse_fpl(path):
    """Garmin-Flugplan (.fpl, XML): waypoint-table + route/route-point."""
    elems = list(ET.parse(path).getroot().iter())
    text = lambda e, tag: next(((c.text or "").strip() for c in e if _local(c) == tag), "")
    table = {}
    for e in elems:
        if _local(e) == "waypoint":
            table[text(e, "identifier")] = (float(text(e, "lat")), float(text(e, "lon")), text(e, "type"))
    route = []
    for e in elems:
        if _local(e) == "route-point":
            ident = text(e, "waypoint-identifier")
            if ident in table:
                lat, lon, typ = table[ident]
                w = {"lat": lat, "lon": lon, "name": ident}
                if typ.upper() == "AIRPORT" and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2}", ident):
                    w["icao"] = ident
                route.append(w)
    if len(route) < 2:
        raise ValueError(f"{path}: keine Route im Garmin-Flugplan gefunden")
    return route


_SD_COORD = re.compile(r"([NS])(\d{2})(\d{2})(\d{2}(?:\.\d+)?)\s+([EW])(\d{3})(\d{2})(\d{2}(?:\.\d+)?)")


def _sd_coord(s):
    m = _SD_COORD.search(s or "")
    if not m:
        return None
    lat = int(m[2]) + int(m[3]) / 60 + float(m[4]) / 3600
    lon = int(m[6]) + int(m[7]) / 60 + float(m[8]) / 3600
    return (-lat if m[1] == "S" else lat, -lon if m[5] == "W" else lon)


def parse_skydemon(path):
    """SkyDemon-Flugplan (.flightplan, XML): PrimaryRoute@Start und je Abschnitt @To (Format N474253.00 E0164415.00)."""
    root = ET.parse(path).getroot()
    prim = next((e for e in root.iter() if _local(e) == "PrimaryRoute"), None)
    coords = []
    if prim is not None:
        coords.append(_sd_coord(prim.get("Start")))
        coords += [_sd_coord(c.get("To")) for c in prim if c.get("To")]
    if len([c for c in coords if c]) < 2:  # Fallback: alle Koordinaten-Attribute in Dokumentreihenfolge
        coords = [_sd_coord(v) for e in root.iter() for v in e.attrib.values() if _SD_COORD.search(v)]
    route = [{"lat": c[0], "lon": c[1], "name": f"WP{i}"} for i, c in enumerate(coords) if c]
    if len(route) < 2:
        raise ValueError(f"{path}: keine Route im SkyDemon-Flugplan gefunden")
    return route


def nearest_airport(lat, lon, max_nm=3):
    """ICAO des nächsten Flugplatzes (OurAirports) innerhalb max_nm, sonst None."""
    try:
        r = requests.get(OURAIRPORTS, timeout=120)
        r.raise_for_status()
    except requests.RequestException:
        return None
    best = None
    for row in csv.DictReader(io.StringIO(r.content.decode("utf-8"))):
        if not row.get("type", "").endswith("airport"):
            continue
        try:
            d = dist_nm({"lat": lat, "lon": lon}, {"lat": float(row["latitude_deg"]), "lon": float(row["longitude_deg"])})
        except ValueError:
            continue
        code = row.get("icao_code") or row.get("gps_code") or row.get("ident")
        if d <= max_nm and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{2}", code or "") and (best is None or d < best[1]):
            best = (code, d)
    return best[0] if best else None


def parse_gpx(path):
    """Wegpunkte aus GPX: Route (rtept), sonst Wegpunkte (wpt), sonst Track (trkpt, ausgedünnt)."""
    local = _local
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


AIRPORT_CACHE = Path("state") / "airports.json"
OURAIRPORTS = "https://davidmegginson.github.io/ourairports-data/airports.csv"


def airport(icao):
    """(lat, lon) eines Flugplatzes: Cache -> aviationweather.gov -> OurAirports (kennt auch kleine Plätze)."""
    icao = icao.upper()
    cache = json.loads(AIRPORT_CACHE.read_text()) if AIRPORT_CACHE.exists() else {}
    if icao in cache:
        return tuple(cache[icao])
    pos = None
    try:
        r = requests.get("https://aviationweather.gov/api/data/airport",
                         params={"ids": icao, "format": "json"}, timeout=30)
        data = r.json() if r.ok and r.text.strip() else []
        if data:
            pos = (float(data[0]["lat"]), float(data[0]["lon"]))
    except (requests.RequestException, ValueError):
        pass
    if pos is None:
        r = requests.get(OURAIRPORTS, timeout=120)
        r.raise_for_status()
        for row in csv.DictReader(io.StringIO(r.content.decode("utf-8"))):
            if icao in (row.get("ident"), row.get("icao_code"), row.get("gps_code")):
                pos = (float(row["latitude_deg"]), float(row["longitude_deg"]))
                break
    if pos is None:
        raise ValueError(f"Flugplatz {icao} nicht gefunden – bitte lat/lon angeben")
    AIRPORT_CACHE.parent.mkdir(exist_ok=True)
    AIRPORT_CACHE.write_text(json.dumps({**cache, icao: pos}, indent=0))
    return pos


def _resolve(w, cruise_alt):
    if isinstance(w, str):
        w = {"icao": w.upper()}
    if "lat" not in w:
        lat, lon = airport(w["icao"])
        w = {**w, "lat": lat, "lon": lon}
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
    """Stützpunkte alle ~sample_nm NM, ETA zunächst aus TAS (Windkorrektur danach mit retime)."""
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


DEFAULT_PERF = {
    "climb_fpm": 500,        # Steigrate auf Meereshöhe (ISA)
    "climb_tas_kt": None,    # Steiggeschwindigkeit, Standard 75 % der Reise-TAS
    "descent_fpm": 500,
    "descent_tas_kt": None,  # Standard: Reise-TAS
    "ceiling_ft": 14000,     # Dienstgipfelhöhe (Steigrate ~0)
    "pattern_ft": 1000,      # Platzrundenhöhe über Zielplatz
}


def perf(fl):
    p = {**DEFAULT_PERF, **(fl.get("performance") or {})}
    p["climb_tas_kt"] = p["climb_tas_kt"] or round(fl["tas_kt"] * 0.75)
    p["descent_tas_kt"] = p["descent_tas_kt"] or fl["tas_kt"]
    return p


def vertical_profile(points, fl, gs, dep_elev, dest_elev, temp_at=None):
    """Höhe je Stützpunkt: Steigflug ab Platzhöhe (Steigrate sinkt mit der Dichtehöhe), Höhenwechsel zwischen
    Abschnitten, Sinkflug auf Platzrundenhöhe am Ziel. Setzt p.alt_ft; liefert die Fluggeschwindigkeit je Punkt."""
    pf = perf(fl)
    wind = [g - fl["tas_kt"] for g in gs]  # Wind-Längskomponente aus der Reise-GS
    n = len(points)
    # Rückwärts: höchste Höhe, von der aus der Sinkflug die Platzrunde noch rechtzeitig erreicht
    ceiling = [0.0] * n
    ceiling[-1] = dest_elev + pf["pattern_ft"]
    for i in range(n - 2, -1, -1):
        d = points[i + 1].dist - points[i].dist
        ceiling[i] = ceiling[i + 1] + pf["descent_fpm"] * 60 * d / max(30, pf["descent_tas_kt"] + wind[i + 1])
    alt, speed = [dep_elev], [max(30, pf["climb_tas_kt"] + wind[0])]
    for i in range(1, n):
        d = points[i].dist - points[i - 1].dist
        target = min(points[i].plan_ft, ceiling[i])
        if target > alt[-1] + 1:
            v = max(30, pf["climb_tas_kt"] + wind[i])
            t = temp_at(i, alt[-1]) if temp_at else None
            isa = 15 - 1.98 * alt[-1] / 1000
            da = alt[-1] + (120 * (t - isa) if t is not None else 0)  # Dichtehöhe
            rate = pf["climb_fpm"] * max(0.05, 1 - da / pf["ceiling_ft"])
            alt.append(min(target, alt[-1] + rate * 60 * d / v))
        elif target < alt[-1] - 1:
            v = max(30, pf["descent_tas_kt"] + wind[i])
            alt.append(max(target, alt[-1] - pf["descent_fpm"] * 60 * d / v))
        else:
            v = gs[i]
            alt.append(target)
        speed.append(v)
    for p, a in zip(points, alt):
        p.alt_ft = a
    return speed


def profile_notes(points, fl, track):
    """TOC/TOD und Warnung, wenn das Gelände auf Kurs im Steig-/Sinkflug zu knapp wird."""
    pf, notes = perf(fl), []
    cruise = points[1].plan_ft if len(points) > 1 else points[0].plan_ft
    toc = next((p for p in points if p.alt_ft >= cruise - 50), None)
    if toc:
        notes.append(f"Steigflug: {cruise:.0f} ft erreicht bei NM {toc.dist:.0f} ({toc.eta:%H:%M}Z) "
                     f"mit {pf['climb_fpm']} fpm / {pf['climb_tas_kt']} kt")
    else:
        top = max(points, key=lambda p: p.alt_ft)
        notes.append(f"⚠ Reiseflughöhe {cruise:.0f} ft wird nicht erreicht (max. {top.alt_ft:.0f} ft bei NM {top.dist:.0f})")
    tod = next((p for p in reversed(points[:-1]) if p.alt_ft >= p.plan_ft - 50), None)
    if tod:
        notes.append(f"Sinkflug ab NM {tod.dist:.0f} ({tod.eta:%H:%M}Z) mit {pf['descent_fpm']} fpm "
                     f"auf {points[-1].alt_ft:.0f} ft Platzrunde")
    dep, dest = points[0], points[-1]
    worst = None
    for p, t in zip(points, track):
        if t is None or p.dist - dep.dist < 1 or dest.dist - p.dist < 1:
            continue  # unmittelbar am Platz
        margin = p.alt_ft - t
        if margin < 500 and (worst is None or margin < worst[1]):
            worst = (p, margin, t)
    if worst:
        p, margin, t = worst
        climbing = p.alt_ft < p.plan_ft - 50 and p.dist < dest.dist / 2
        state = "UNTER dem Gelände" if margin < 0 else f"nur {margin:.0f} ft darüber"
        msg = f"⚠ {'Steigflug' if climbing else 'Flugweg'}: bei NM {p.dist:.0f} {p.alt_ft:.0f} ft, Gelände auf Kurs {t:.0f} ft ({state})"
        if climbing:
            minutes = max(0.1, (p.eta - dep.eta).total_seconds() / 60)
            msg += f" – nötig wären ~{(t + 500 - dep.alt_ft) / minutes:.0f} fpm oder Höhe über dem Platz gewinnen"
        notes.append(msg)
    return notes


def retime(points, fl, gs):
    """ETAs mit Groundspeed je Stützpunkt neu berechnen (Mittel der GS an beiden Enden des Teilstücks)."""
    t = fl["departure"]
    points[0].eta = t
    for i in range(1, len(points)):
        d = points[i].dist - points[i - 1].dist
        v = max(20.0, (gs[i - 1] + gs[i]) / 2)
        t += dt.timedelta(hours=d / v)
        points[i].eta = t
    return points
