"""Telegram-Befehle abholen (Polling, kein Server nötig) und Flugpläne anlegen/löschen.

Nur Nachrichten aus TELEGRAM_CHAT_ID werden beachtet. Alle Zeiten UTC.
"""
import datetime as dt
import json
import os
import re
from pathlib import Path

import requests
import yaml

FLIGHTS = Path("flights")
AIRCRAFT = Path("aircraft")
OFFSET_FILE = Path("state") / "telegram.json"
DEFAULT_TAS = 100
REG = re.compile(r"[A-Z]{1,2}-[A-Z0-9]{2,5}")  # Kennzeichen, z.B. D-EABC, OE-KXY
ROUTE_FILES = (".gpx", ".fpl", ".flightplan")

HELP = """vfrcheck – Befehle (Zeiten in UTC):
/flug LOWW LOAN LOWG 12.10. 08:00 4500 [TAS] [D-EABC] [altn=LOWI]
   Wegpunkte: ICAO oder lat,lon (z.B. 47.63,15.83)
   D-EABC = Flugzeugprofil (TAS, Steigrate …), altn = Ausweichplatz
Routendatei (.gpx, .fpl, .flightplan) senden mit Beschriftung: /flug 12.10. 08:00 4500 [TAS] [D-EABC]
/flugzeug D-EABC 105 500 [Steig-TAS] [Gipfelhöhe] – Profil anlegen (TAS, Steigrate fpm)
/flugzeuge – Profile anzeigen
/liste – geplante Flüge
/check NAME – sofort prüfen (NAME oder Anfang davon)
/loeschen NAME – Flug löschen
Hinweis: Befehle werden alle ~10 min abgeholt."""


def _api(method, **kw):
    r = requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{method}", timeout=60, **kw)
    r.raise_for_status()
    return r.json()["result"]


def _extra_text(extra):
    parts = [f"TAS {extra['tas_kt']} kt"] if "tas_kt" in extra else []
    parts += [f"Flugzeug {extra['aircraft']}"] if "aircraft" in extra else []
    parts += [f"Ausweichplatz {extra['alternate']}"] if "alternate" in extra else []
    return ", ".join(parts)


def reply(text):
    _api("sendMessage", data={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text[:4000]})


def _date(tok, now):
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", tok):
        return dt.date(*map(int, m.groups()))
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})?", tok)
    if not m:
        return None
    d, mo, y = int(m[1]), int(m[2]), m[3]
    if y:
        return dt.date(int(y) + (2000 if len(y) == 2 else 0), mo, d)
    date = dt.date(now.year, mo, d)
    return date if date >= now.date() else date.replace(year=now.year + 1)


def _waypoint(tok):
    if re.fullmatch(r"[A-Za-z]{2}[A-Za-z0-9]{2}", tok):
        return tok.upper()
    if m := re.fullmatch(r"(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", tok):
        return {"lat": float(m[1]), "lon": float(m[2])}
    raise ValueError(f"Unbekannter Wegpunkt: {tok}")


def parse_flug(args, now, with_route=True):
    """'/flug [Wegpunkte…] DATUM ZEIT HÖHE [TAS]' -> (route, departure, alt, tas)."""
    di = next((i for i, t in enumerate(args) if _date(t, now)), None)
    if di is None or len(args) < di + 3:
        raise ValueError("Format: /flug LOWW LOWG 12.10. 08:00 4500 [TAS]")
    route = [_waypoint(t) for t in args[:di]]
    if with_route and len(route) < 2:
        raise ValueError("Mindestens zwei Wegpunkte angeben")
    m = re.fullmatch(r"(\d{1,2}):?(\d{2})Z?", args[di + 1].upper())
    if not m:
        raise ValueError(f"Zeit nicht erkannt: {args[di + 1]} (z.B. 08:00)")
    dep = dt.datetime.combine(_date(args[di], now), dt.time(int(m[1]), int(m[2])), dt.timezone.utc)
    alt = int(args[di + 2])
    extra = {}
    for tok in args[di + 3:]:
        if tok.isdigit():
            extra["tas_kt"] = int(tok)
        elif REG.fullmatch(tok.upper()):
            extra["aircraft"] = tok.upper()
        elif tok.lower().startswith("altn="):
            extra["alternate"] = tok[5:].upper()
        else:
            raise ValueError(f"Unbekannte Angabe: {tok}")
    if "tas_kt" not in extra and "aircraft" not in extra:
        extra["tas_kt"] = DEFAULT_TAS
    return route, dep, alt, extra


def _write_flight(stem, data):
    FLIGHTS.mkdir(exist_ok=True)
    path = FLIGHTS / f"{stem}.yaml"
    path.write_text("# angelegt per Telegram, Zeiten UTC\n" + yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    return str(path)


def _find(name):
    hits = sorted(p for p in FLIGHTS.glob("*.yaml") if p.stem.lower().startswith(name.lower()))
    if len(hits) != 1:
        raise ValueError(f"{len(hits)} Flüge passen zu '{name}' – siehe /liste")
    return hits[0]


def _handle(msg, now):
    """Liefert (neu angelegte Dateien, sofort zu prüfende Dateien)."""
    text = (msg.get("text") or msg.get("caption") or "").strip()
    cmd, *args = text.split() or [""]
    cmd = cmd.split("@")[0].lower()
    doc = msg.get("document")
    if cmd == "/flug" and doc:
        suffix = Path(doc.get("file_name", "")).suffix.lower()
        if suffix not in ROUTE_FILES:
            raise ValueError("Bitte eine Routendatei senden (.gpx, .fpl oder .flightplan)")
        _, dep, alt, extra = parse_flug(args, now, with_route=False)
        f = _api("getFile", data={"file_id": doc["file_id"]})
        data = requests.get(f"https://api.telegram.org/file/bot{os.environ['TELEGRAM_BOT_TOKEN']}/{f['file_path']}",
                            timeout=60).content
        stem = f"{dep:%Y%m%d-%H%M}-{re.sub(r'[^A-Za-z0-9]+', '-', Path(doc['file_name']).stem)[:30]}"
        FLIGHTS.mkdir(exist_ok=True)
        (FLIGHTS / f"{stem}{suffix}").write_bytes(data)
        path = _write_flight(stem, {"name": stem, "departure": dep.strftime("%Y-%m-%dT%H:%MZ"),
                                    "cruise_alt_ft": alt, "route_file": f"{stem}{suffix}", **extra})
        reply(f"✈ Flug angelegt: {stem}\nAbflug {dep:%d.%m. %H:%M} UTC, {alt} ft, {_extra_text(extra)} – Bewertung folgt.")
        return {path}, set()
    if cmd == "/flug":
        route, dep, alt, extra = parse_flug(args, now)
        names = [w if isinstance(w, str) else "WPT" for w in route]
        stem = f"{dep:%Y%m%d-%H%M}-{names[0]}-{names[-1]}"
        path = _write_flight(stem, {"name": f"{names[0]}-{names[-1]}", "departure": dep.strftime("%Y-%m-%dT%H:%MZ"),
                                    "cruise_alt_ft": alt, "route": route, **extra})
        reply(f"✈ Flug angelegt: {stem}\n{' → '.join(names)}\nAbflug {dep:%d.%m. %H:%M} UTC, {alt} ft, "
              f"{_extra_text(extra)} – Bewertung folgt.")
        return {path}, set()
    if cmd == "/flugzeug":
        if len(args) < 3 or not REG.fullmatch(args[0].upper()):
            raise ValueError("Format: /flugzeug D-EABC TAS Steigrate [Steig-TAS] [Gipfelhöhe]")
        reg, nums = args[0].upper(), [int(a) for a in args[1:]]
        perf = {"climb_fpm": nums[1]}
        if len(nums) > 2:
            perf["climb_tas_kt"] = nums[2]
        if len(nums) > 3:
            perf["ceiling_ft"] = nums[3]
        AIRCRAFT.mkdir(exist_ok=True)
        (AIRCRAFT / f"{reg}.yaml").write_text(yaml.safe_dump({"tas_kt": nums[0], "performance": perf}, sort_keys=False),
                                             encoding="utf-8")
        reply(f"🛩 Profil {reg} gespeichert: TAS {nums[0]} kt, " + ", ".join(f"{k} {v}" for k, v in perf.items()))
        return set(), set()
    if cmd == "/flugzeuge":
        lines = [f"• {p.stem}: {yaml.safe_load(p.read_text(encoding='utf-8'))}" for p in sorted(AIRCRAFT.glob("*.yaml"))]
        reply("\n".join(lines) or "Keine Flugzeugprofile. Anlegen mit /flugzeug D-EABC 105 500")
        return set(), set()
    if cmd == "/liste":
        lines = []
        for p in sorted(FLIGHTS.glob("*.yaml")):
            d = yaml.safe_load(p.read_text(encoding="utf-8"))
            lines.append(f"• {p.stem}  ({d.get('departure')}, {d.get('cruise_alt_ft')} ft)")
        reply("\n".join(lines) or "Keine Flüge geplant.")
    elif cmd == "/check" and args:
        p = _find(args[0])
        reply(f"Prüfe {p.stem} …")
        return set(), {str(p)}
    elif cmd in ("/loeschen", "/löschen") and args:
        p = _find(args[0])
        for f in (p, *(p.with_suffix(s) for s in ROUTE_FILES), Path("state") / f"{p.stem}.json"):
            f.unlink(missing_ok=True)
        reply(f"🗑 {p.stem} gelöscht")
    elif cmd in ("/hilfe", "/help", "/start"):
        reply(HELP)
    elif cmd.startswith("/"):
        reply("Unbekannter Befehl.\n\n" + HELP)
    return set(), set()


def process():
    """Neue Nachrichten abholen und ausführen. Liefert (neue Flugdateien, sofort zu prüfende Dateien)."""
    if not (os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID")):
        return set(), set()
    offset = json.loads(OFFSET_FILE.read_text()).get("offset", 0) if OFFSET_FILE.exists() else 0
    created, forced = set(), set()
    now = dt.datetime.now(dt.timezone.utc)
    try:
        updates = _api("getUpdates", data={"offset": offset, "timeout": 0})
    except Exception as e:
        print(f"Telegram getUpdates fehlgeschlagen: {e}")
        return created, forced
    for u in updates:
        offset = u["update_id"] + 1
        msg = u.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != os.environ["TELEGRAM_CHAT_ID"]:
            continue  # fremde Chats ignorieren
        try:
            c, f = _handle(msg, now)
            created |= c
            forced |= f
        except Exception as e:
            reply(f"⚠ {e}")
    OFFSET_FILE.parent.mkdir(exist_ok=True)
    OFFSET_FILE.write_text(json.dumps({"offset": offset}))
    return created, forced
