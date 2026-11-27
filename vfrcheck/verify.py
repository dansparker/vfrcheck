"""Nachkontrolle: Vorhersage vs. tatsächliche METARs an Start- und Zielplatz, daraus die Trefferquote."""
import datetime as dt
import json
import re
from pathlib import Path

import requests

STATE = Path("state")
LOG = STATE / "verification.json"
WINDOW = dt.timedelta(minutes=45)  # METAR so nah wie möglich an Abflug/Ankunft, max. ±45 min


def _vis_m(raw, visib):
    if "CAVOK" in raw:
        return 10000
    m = re.search(r"\s(\d{4})(?:NDV)?\s", f" {raw} ")
    if m:
        return int(m[1])
    try:  # JSON 'visib' in Statute Miles, z.B. "10+"
        return float(str(visib).rstrip("+")) * 1609
    except (TypeError, ValueError):
        return None


def _ceiling_ft(clouds):
    bases = [c.get("base") for c in clouds or [] if c.get("cover") in ("BKN", "OVC", "OVX", "VV") and c.get("base") is not None]
    return min(bases) if bases else None


def observed(station, when, vis_min, ceil_min):
    """(VFR erfüllt?, METAR) für die Meldung, die `when` am nächsten liegt; (None, None) ohne Daten."""
    hours = max(2, min(48, int((dt.datetime.now(dt.timezone.utc) - when).total_seconds() // 3600) + 2))
    r = requests.get("https://aviationweather.gov/api/data/metar",
                     params={"ids": station, "format": "json", "hours": hours}, timeout=30)
    rows = r.json() if r.ok and r.text.strip() else []
    best = None
    for row in rows:
        t = dt.datetime.fromisoformat(row["reportTime"].replace("Z", "+00:00"))
        if abs(t - when) <= WINDOW and (best is None or abs(t - when) < abs(best[0] - when)):
            best = (t, row)
    if not best:
        return None, None
    row = best[1]
    vis, ceil = _vis_m(row.get("rawOb", ""), row.get("visib")), _ceiling_ft(row.get("clouds"))
    ok = (vis is None or vis >= vis_min) and (ceil is None or ceil >= ceil_min)
    return ok, row.get("rawOb")


def process():
    """Abgeschlossene Flüge (Ankunft + 1 h vorbei) einmalig nachkontrollieren. Liefert neue Einträge."""
    now = dt.datetime.now(dt.timezone.utc)
    log = json.loads(LOG.read_text()) if LOG.exists() else []
    new = []
    for f in sorted(STATE.glob("*.json")):
        try:
            st = json.loads(f.read_text())
        except (ValueError, OSError):
            continue
        info = st.get("verify") if isinstance(st, dict) else None
        if not info or st.get("verified"):
            continue
        arr = dt.datetime.fromisoformat(info["arrival"])
        if now < arr + dt.timedelta(hours=1):
            continue
        dep = dt.datetime.fromisoformat(info["departure"])
        results = []
        try:
            for station, when in ((info["dep_station"], dep), (info["dest_station"], arr)):
                results.append(observed(station, when, info["vis_m"], info["ceiling_ft"]) if station else (None, None))
        except Exception as e:
            print(f"Nachkontrolle {f.stem} nicht möglich: {e}")
            continue
        oks = [ok for ok, _ in results if ok is not None]
        entry = {"flight": f.stem, "name": info["name"], "departure": info["departure"],
                 "forecast": info["forecast"], "observed_vfr": all(oks) if oks else None,
                 "metars": [m for _, m in results if m]}
        log.append(entry)
        new.append(entry)
        st["verified"] = now.isoformat()
        f.write_text(json.dumps(st))
    if new:
        LOG.write_text(json.dumps(log, indent=1))
    return new


def stats():
    """Trefferquote und Brier-Score über alle nachkontrollierten Flüge mit Beobachtung."""
    log = json.loads(LOG.read_text()) if LOG.exists() else []
    rows = [e for e in log if e.get("observed_vfr") is not None and e.get("forecast") is not None]
    if not rows:
        return None
    hits = sum((e["forecast"] >= 50) == e["observed_vfr"] for e in rows)
    brier = sum((e["forecast"] / 100 - e["observed_vfr"]) ** 2 for e in rows) / len(rows)
    return {"n": len(rows), "hits": hits, "brier": brier}


def line(entry):
    obs = {True: "VFR beobachtet", False: "NICHT VFR beobachtet", None: "keine METAR-Daten"}[entry["observed_vfr"]]
    return f"Nachkontrolle {entry['name']}: Vorhersage {entry['forecast']:.0f}% – {obs}"
