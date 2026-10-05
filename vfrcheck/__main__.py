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

from . import assess, gramet, notify, route, weather

STATE_DIR = Path("state")
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
                     + ("" if lo <= fl["cruise_alt_ft"] <= hi else f"  ⚠ geplante {fl['cruise_alt_ft']} ft liegt außerhalb"))
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


def report(fl, res, metars):
    p = res["probability"]
    lines = [f"Flug {fl['name']} – Abflug {fl['departure']:%Y-%m-%d %H:%M} UTC, {fl['cruise_alt_ft']} ft",
             f"VFR-Wahrscheinlichkeit: {p:.0f}% ({category(p)}), {res['members']} Ensemble-Member"
             if p is not None else "Außerhalb des Vorhersagezeitraums (max. ~15 Tage)", ""]
    lines += altitudes(fl, res)
    problems = [r for r in res["points"] if r["fail_pct"]]
    if problems:
        lines.append("Mögliche Probleme entlang der Strecke:")
        for r in sorted(problems, key=lambda r: -r["fail_pct"])[:10]:
            why = ", ".join(f"{k} {v:.0f}%" for k, v in list(r["reasons"].items())[:3])
            lines.append(f"- {r['point'].name} ({r['point'].eta:%H:%M}Z): {r['fail_pct']:.0f}% kritisch – {why}")
        lines.append("")
    for icao, d in metars.items():
        for kind in ("metar", "taf"):
            if d.get(kind):
                lines.append(d[kind])
    return "\n".join(lines)


def run(path, args):
    fl = route.load_flight(path)
    fl.setdefault("name", Path(path).stem)
    now = dt.datetime.now(dt.timezone.utc)
    if fl["departure"] < now - dt.timedelta(hours=1):
        print(f"{fl['name']}: Abflug liegt in der Vergangenheit – übersprungen")
        return None
    points = route.sample(fl)
    res = assess.assess(points, weather.ensemble(points), fl["minima"])
    icaos = [w["icao"] for w in fl["waypoints"] if w.get("icao")]
    text = report(fl, res, weather.metar_taf(icaos))
    print(text, "\n")

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
    if args.force_notify and not reason:
        reason = "Manuell ausgelöst"

    if reason and not args.dry_run:
        img = gramet.fetch(fl, points)
        subj = f"VFR {fl['name']}: {p:.0f}% {category(p)} ({reason})" if p is not None else f"VFR {fl['name']}"
        print("Gesendet über:", notify.send(subj, text, img) or "keinen Kanal (nicht konfiguriert)")
    state = dict(old)
    if p is not None and (reason or old_p is None):
        state.update(probability=p, updated=now.isoformat())

    # Briefing-Mail 2 h vor Abflug (einmalig)
    to_dep = fl["departure"] - now
    if (args.briefing or (abs(to_dep - BRIEFING_BEFORE) <= BRIEFING_WINDOW and not old.get("briefing_sent")))             and not args.dry_run:
        subj = f"Briefing {fl['name']} – Abflug {fl['departure']:%H:%M} UTC" + (
            f": {p:.0f}% {category(p)}" if p is not None else "")
        if notify.email(subj, text, gramet.fetch(fl, points)):
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
    files = args.files or sorted(glob.glob("flights/*.yaml"))
    summary = []
    for f in files:
        try:
            t = run(f, args)
            if t:
                summary.append(t)
        except Exception as e:
            print(f"{f}: Fehler: {e}")
            summary.append(f"{f}: Fehler: {e}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as s:
            s.write("\n\n---\n\n".join(f"```\n{t}\n```" for t in summary))


if __name__ == "__main__":
    main()
