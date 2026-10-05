"""vfrcheck: prüft Flugpläne in flights/*.yaml und meldet starke Änderungen der VFR-Wahrscheinlichkeit.

Aufruf:  python -m vfrcheck [--dry-run] [--force-notify] [flights/xyz.yaml ...]
"""
import argparse
import datetime as dt
import glob
import json
import os
import sys
from pathlib import Path

from . import assess, chart, gramet, notify, route, section, telegram_bot, weather

STATE_DIR = Path("state")
ENS_NM = 15  # Abstand der Ensemble-Abfragen
BRIEFING_BEFORE = dt.timedelta(hours=2)
BRIEFING_WINDOW = dt.timedelta(minutes=50)  # Toleranz, da der Cron nur stündlich (und oft verspätet) läuft


def category(p):
    return "GO" if p >= 80 else "MARGINAL" if p >= 50 else "NO-GO"


def _ft(v):
    return "unbegrenzt" if v == float("inf") else f"{round(v / 100) * 100:.0f} ft"


def altitudes(fl, res):
    """Mögliche Flughöhen: Gelände + Abstand bis Wolkenbasis − Abstand (80 % der Member)."""
    if not res["band"]:
        return []
    lo, hi = res["band"]
    lines = ["Mögliche Flughöhen (MSL, 80 % sicher unter tiefer Bewölkung):"]
    if lo <= hi:
        lines.append(f"- ganze Strecke: {_ft(lo)} bis {_ft(hi)}"
                     + ("" if lo <= fl["cruise_alt_ft"] <= hi else f"  ⚠ geplante {fl['cruise_alt_ft']} ft liegt außerhalb (Gelände/Wolken)"))
    else:
        lines.append("- KEIN durchgehendes Höhenband – Abschnitte:")
    for r in res["points"]:
        if "band" not in r:
            continue
        b_lo, b_hi, base = r["band"]
        if base != float("inf") or b_lo > hi:
            flag = " ⚠ zu eng" if b_lo > b_hi else ""
            lines.append(f"  · {r['point'].name}: {_ft(b_lo)}–{_ft(b_hi)} (Basis ~{_ft(base)}){flag}")
    lines.append("")
    return lines


def leg_lines(legs):
    lines = ["Streckenabschnitte (Wind auf Planhöhe, ICON):"]
    for lg in legs:
        t = f"- {lg['name']}: {lg['dist']:.0f} NM, Kurs {lg['course']:03.0f}°, {lg['alt']} ft"
        if lg["wind"]:
            side = "rechts" if lg["cross"] >= 0 else "links"
            t += (f" | Wind {lg['wind'][0]:03.0f}°/{lg['wind'][1]:.0f} kt (max {lg['max_wind']:.0f}), "
                  f"{'Gegenwind' if lg['head'] >= 0 else 'Rückenwind'} {abs(lg['head']):.0f} kt, "
                  f"Seitenwind {abs(lg['cross']):.0f} kt von {side}, GS {lg['gs']:.0f} kt"
                  + (f", {lg['ete_min']:.0f} min" if lg["ete_min"] else ""))
        if lg["freezing_ft"]:
            t += f" | 0 °C {_ft(lg['freezing_ft'])}"
        if lg["icing_ft"]:
            t += f" | ⚠ Vereisung ab {_ft(lg['icing_ft'])}"
        lines.append(t)
    return lines + [""]


def problem_segments(res, legs, threshold=10):
    """Benachbarte kritische Stützpunkte zu Abschnitten zusammenfassen."""
    segs, cur = [], []
    for r in res["points"] + [{"fail_pct": None}]:
        if r["fail_pct"] and r["fail_pct"] >= threshold:
            cur.append(r)
        elif cur:
            worst = max(cur, key=lambda r: r["fail_pct"])
            segs.append((cur[0]["point"], cur[-1]["point"], worst))
            cur = []
    lines = []
    for a, b, w in sorted(segs, key=lambda s: -s[2]["fail_pct"])[:8]:
        where = legs[a.leg]["name"] if legs else a.name
        why = ", ".join(f"{k} {v:.0f}%" for k, v in list(w["reasons"].items())[:3])
        lines.append(f"- {where}, NM {a.dist:.0f}–{b.dist:.0f} ({a.eta:%H:%M}–{b.eta:%H:%M}Z): "
                     f"bis {w['fail_pct']:.0f}% kritisch – {why}")
    return lines


def report(fl, res, metars, legs=()):
    p = res["probability"]
    lines = [f"Flug {fl['name']} – Abflug {fl['departure']:%Y-%m-%d %H:%M} UTC, {fl['cruise_alt_ft']} ft",
             f"VFR-Wahrscheinlichkeit: {p:.0f}% ({category(p)}), {res['members']} Ensemble-Member"
             if p is not None else "Außerhalb des Vorhersagezeitraums (max. ~15 Tage)", ""]
    lines += altitudes(fl, res)
    if legs:
        lines += leg_lines(legs)
    problems = problem_segments(res, legs)
    if problems:
        lines += ["Mögliche Probleme entlang der Strecke:"] + problems + [""]
    for icao, d in metars.items():
        for kind in ("metar", "taf"):
            if d.get(kind):
                lines.append(d[kind])
    return "\n".join(lines)


def ensemble_for(points, sec):
    """Ensembles (~25 km Gitter) nur alle ~ENS_NM abfragen und auf die dichten Stützpunkte verteilen;
    Geländehöhe kommt aus dem feineren Modell (ICON-D2)."""
    idx = [0]
    for i, p in enumerate(points):
        if p.dist - points[idx[-1]].dist >= ENS_NM or (i == len(points) - 1 and i != idx[-1]):
            idx.append(i)
    wx = weather.ensemble([points[i] for i in idx])
    out = []
    for i, p in enumerate(points):
        j = min(range(len(idx)), key=lambda k: abs(points[idx[k]].dist - p.dist))
        w = dict(wx[j])
        if sec and sec[i].get("elevation") is not None:
            w["elevation"] = sec[i]["elevation"]
        out.append(w)
    return out


def run(path, args, force=False):
    fl = route.load_flight(path)
    fl.setdefault("name", Path(path).stem)
    now = dt.datetime.now(dt.timezone.utc)
    if fl["departure"] < now - dt.timedelta(hours=1):
        print(f"{fl['name']}: Abflug liegt in der Vergangenheit – übersprungen")
        return None
    points = route.sample(fl)
    try:
        sec = weather.section(points)
    except Exception as e:
        print(f"Vertikalschnitt nicht verfügbar: {e}")
        sec = None
    profs = section.profiles(points, sec) if sec else [None] * len(points)
    res = assess.assess(points, ensemble_for(points, sec), fl["minima"])
    icaos = [w["icao"] for w in fl["waypoints"] if w.get("icao")]
    legs = section.legs(fl, points, profs)
    text = report(fl, res, weather.metar_taf(icaos), legs)
    print(text, "\n")
    png = chart.render(fl, points, res, profs, legs)
    Path("charts").mkdir(exist_ok=True)
    (Path("charts") / f"{Path(path).stem}.png").write_bytes(png)

    def images():
        g = gramet.fetch(fl, points)
        return [("querschnitt.png", png)] + ([("gramet.png", g)] if g else [])

    state_file = STATE_DIR / f"{Path(path).stem}.json"
    old = json.loads(state_file.read_text()) if state_file.exists() else {}
    p, old_p = res["probability"], old.get("probability")
    reason = None
    if p is not None:
        if old_p is None:
            reason = "Erste Bewertung"
        elif abs(p - old_p) >= fl["alert"]["delta_pct"]:
            reason = f"Änderung {old_p:.0f}% → {p:.0f}%"
        elif category(p) != category(old_p):
            reason = f"Kategorie {category(old_p)} → {category(p)}"
    if (args.force_notify or force) and not reason:
        reason = "Manuell ausgelöst"

    if reason and not args.dry_run:
        subj = f"VFR {fl['name']}: {p:.0f}% {category(p)} ({reason})" if p is not None else f"VFR {fl['name']}"
        print("Gesendet über:", notify.send(subj, text, images()) or "keinen Kanal (nicht konfiguriert)")
    state = dict(old)
    if p is not None and (reason or old_p is None):
        state.update(probability=p, updated=now.isoformat())

    # Briefing-Mail 2 h vor Abflug (einmalig)
    to_dep = fl["departure"] - now
    if (args.briefing or (abs(to_dep - BRIEFING_BEFORE) <= BRIEFING_WINDOW and not old.get("briefing_sent")))             and not args.dry_run:
        subj = f"Briefing {fl['name']} – Abflug {fl['departure']:%H:%M} UTC" + (
            f": {p:.0f}% {category(p)}" if p is not None else "")
        if notify.email(subj, text, images()):
            state["briefing_sent"] = now.isoformat()
            print("Briefing-Mail gesendet")

    if state != old and not args.dry_run:
        STATE_DIR.mkdir(exist_ok=True)
        state_file.write_text(json.dumps(state, indent=1))
    return text


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--dry-run", action="store_true", help="nichts senden, keinen Zustand speichern")
    ap.add_argument("--force-notify", action="store_true")
    ap.add_argument("--briefing", action="store_true", help="Briefing-Mail sofort senden")
    args = ap.parse_args()
    created, forced = set(), set()
    if not args.dry_run:
        created, forced = telegram_bot.process()
    if args.files:
        files = args.files
    else:
        # Cron alle 10 min holt Telegram-Befehle; volle Prüfung aller Flüge nur einmal pro Stunde
        files = sorted(glob.glob("flights/*.yaml"))
        now = dt.datetime.now(dt.timezone.utc)
        last = STATE_DIR / "last_full_check.txt"
        hourly = (os.environ.get("GITHUB_EVENT_NAME") != "schedule" or not last.exists()
                  or now - dt.datetime.fromisoformat(last.read_text().strip()) >= dt.timedelta(minutes=55))
        if hourly and not args.dry_run:
            STATE_DIR.mkdir(exist_ok=True)
            last.write_text(now.isoformat())
        if not hourly:
            files = [f for f in files if str(Path(f)) in {str(Path(x)) for x in created | forced}]
    summary = []
    for f in files:
        try:
            t = run(f, args, force=str(Path(f)) in {str(Path(x)) for x in forced})
            if t:
                summary.append(t)
        except Exception as e:
            print(f"{f}: Fehler: {e}")
            summary.append(f"{f}: Fehler: {e}")
            if str(Path(f)) in {str(Path(x)) for x in created | forced}:
                telegram_bot.reply(f"⚠ {Path(f).stem}: {e}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as s:
            s.write("\n\n---\n\n".join(f"```\n{t}\n```" for t in summary))


if __name__ == "__main__":
    main()
