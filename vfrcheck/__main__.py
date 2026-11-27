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

from . import airspace, assess, chart, gramet, notify, route, section, site, sun, telegram_bot, weather
from . import terrain as terrain_mod

STATE_DIR = Path("state")
ENS_NM = 15  # Abstand der Ensemble-Abfragen
BRIEFING_BEFORE = dt.timedelta(hours=2)
BRIEFING_WINDOW = dt.timedelta(minutes=50)  # Toleranz, da der Cron nur stündlich (und oft verspätet) läuft


def category(p):
    return "GO" if p >= 80 else "MARGINAL" if p >= 50 else "NO-GO"


def _ft(v):
    return "unbegrenzt" if v == float("inf") else f"{round(v / 100) * 100:.0f} ft"


def altitudes(fl, res):
    """Mögliche Flughöhen: Gelände + Abstand bis Wolkenbasis − Abstand (80 % der Member), je Abschnitt."""
    if not res["band"]:
        return []
    lo, hi = res["band"]
    lines = ["Mögliche Flughöhen (MSL, 80 % sicher unter tiefer Bewölkung):"]
    if lo <= hi:
        lines.append(f"- ganze Strecke: {_ft(lo)} bis {_ft(hi)}"
                     + ("" if lo <= fl["cruise_alt_ft"] <= hi else
                        f"  ⚠ geplante {fl['cruise_alt_ft']} ft liegt außerhalb (Gelände/Wolken)"))
    else:
        lines.append("- KEIN durchgehendes Höhenband")
    wps = fl["waypoints"]
    for k, (a, b) in enumerate(zip(wps, wps[1:])):
        bands = [r["band"] for r in res["points"] if "band" in r and r["point"].leg == k]
        if not bands:
            continue
        b_lo, b_hi = max(x[0] for x in bands), min(x[1] for x in bands)
        if lo > hi or (b_lo, b_hi) != (lo, hi):
            flag = " ⚠ zu eng" if b_lo > b_hi else ""
            lines.append(f"  · {a['name']}→{b['name']}: {_ft(b_lo)}–{_ft(b_hi)}{flag}")
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
        if lg.get("terrain_ft"):
            t += f" | Gelände max. {_ft(lg['terrain_ft'])} (±5 NM)"
        if lg.get("clouds"):
            b, top, share = lg["clouds"]
            t += f" | Wolken (ICON) {_ft(b)}–{_ft(top)} auf {share * 100:.0f}% des Abschnitts"
            if lg.get("on_top_ft"):
                t += f", on top ab ~{_ft(lg['on_top_ft'])} denkbar"
        if lg.get("lee"):
            t += f" | ⚠ Lee-/Rotorturbulenz möglich (Wind Kammhöhe {lg['lee']:.0f} kt)"
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


def report(fl, res, metars, legs=(), notes=()):
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
    for title, items in notes:
        if items:
            lines += [title] + list(items) + [""]
    for icao, d in metars.items():
        for kind in ("metar", "taf"):
            if d.get(kind):
                lines.append(d[kind])
    return "\n".join(lines)


def optional(what, fn, default):
    """Zusatzdaten holen; ein Fehler darf die Bewertung nicht abbrechen."""
    try:
        return fn()
    except Exception as e:
        print(f"{what} nicht verfügbar: {e}")
        return default


ALPS = (45.8, 48.2, 5.8, 16.5)  # lat_min, lat_max, lon_min, lon_max


def foehn_notes(fl, points, terrain):
    """Föhnindex (Druckdifferenz Bozen − Innsbruck), nur für Strecken über den Alpen."""
    alpine = any(ALPS[0] <= p.lat <= ALPS[1] and ALPS[2] <= p.lon <= ALPS[3] and (t or 0) >= 4000
                 for p, t in zip(points, terrain))
    if not alpine:
        return []
    dp = optional("Föhnindex", lambda: weather.foehn_dp(points[len(points) // 2].eta), None)
    if dp is None:
        return []
    if dp >= 4:
        return [f"⚠ Südföhn wahrscheinlich (Δp Bozen–Innsbruck {dp:+.1f} hPa): Turbulenz, Lee-Wellen, Staubewölkung im Süden"]
    if dp <= -4:
        return [f"⚠ Nordföhn wahrscheinlich (Δp Bozen–Innsbruck {dp:+.1f} hPa): Turbulenz, Staubewölkung im Norden"]
    return [f"Föhnindex unauffällig (Δp Bozen–Innsbruck {dp:+.1f} hPa)"]


def ensemble_for(points, sec, ground_ft=None):
    """Ensembles (~25 km Gitter) nur alle ~ENS_NM abfragen und auf die dichten Stützpunkte verteilen;
    Geländehöhe aus dem 90-m-Geländemodell, sonst aus ICON-D2."""
    idx = [0]
    for i, p in enumerate(points):
        if p.dist - points[idx[-1]].dist >= ENS_NM or (i == len(points) - 1 and i != idx[-1]):
            idx.append(i)
    wx = weather.ensemble([points[i] for i in idx])
    out = []
    for i, p in enumerate(points):
        j = min(range(len(idx)), key=lambda k: abs(points[idx[k]].dist - p.dist))
        w = dict(wx[j])
        if ground_ft and ground_ft[i] is not None:  # Geländemodell 90 m am Punkt
            w["elevation"] = ground_ft[i] / assess.M_TO_FT
        elif sec and sec[i].get("elevation") is not None:
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
    courses = [route.course(a, b) for a, b in zip(fl["waypoints"], fl["waypoints"][1:])]
    ter = optional("Gelände-Korridor", lambda: terrain_mod.corridor_max(points, courses), None)
    terrain = ter["max"] if ter else [None] * len(points)
    track = ter["track"] if ter else [None] * len(points)
    # Platzhöhen: Geländemodell am Punkt, sonst ICON-Gitterhöhe
    elev = lambda i: (ter["point"][i] if ter and ter["point"][i] is not None
                      else (profs[i] or {}).get("elev_ft") or 0)
    # Höhenprofil (Steigen/Sinken) und windkorrigierte ETAs, zweimal iteriert
    for _ in range(2):
        gs = section.point_gs(fl, points, profs) if sec else [fl["tas_kt"]] * len(points)
        tfn = (lambda i, a: section.temp_at(profs[i], a) if profs[i] is not None else None) if sec else None
        speed = route.vertical_profile(points, fl, gs, elev(0), elev(-1), tfn)
        route.retime(points, fl, speed)
        if sec:
            profs = section.profiles(points, sec)
    det_bases = [(section.cloud_layers(pr) or [(None,)])[0][0] if pr is not None else None for pr in profs]
    res = assess.assess(points, ensemble_for(points, sec, ter["point"] if ter else None), fl["minima"], terrain, det_bases)
    icaos = [w["icao"] for w in fl["waypoints"] if w.get("icao")]
    legs = section.legs(fl, points, profs, terrain)
    metars = weather.metar_taf(icaos)
    ground = [r.get("elev_ft") for r in res["points"]]
    spaces = optional("Lufträume", lambda: airspace.check_airspaces(fl, points, ground), [])
    notes = [("Luftraum entlang der Strecke (openAIP):", airspace.airspace_lines(spaces)),
             ("NOTAMs (autorouter):", optional("NOTAMs", lambda: airspace.notams(fl, points), [])),
             ("Steig-/Sinkflug:", route.profile_notes(points, fl, track)),
             ("Tageslicht / Föhn:", sun.daylight_notes(points) + foehn_notes(fl, points, terrain))]
    text = report(fl, res, metars, legs, notes)
    print(text, "\n")
    png = chart.render(fl, points, res, profs, legs, spaces)
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
    hist = state.get("history", [])
    if p is not None and (not hist or now - dt.datetime.fromisoformat(hist[-1][0]) >= dt.timedelta(minutes=30)):
        state["history"] = (hist + [[now.isoformat(timespec="minutes"), round(p)]])[-200:]

    # Briefing-Mail 2 h vor Abflug (einmalig)
    to_dep = fl["departure"] - now
    if ((args.briefing or (abs(to_dep - BRIEFING_BEFORE) <= BRIEFING_WINDOW and not old.get("briefing_sent")))
            and not args.dry_run):
        subj = f"Briefing {fl['name']} – Abflug {fl['departure']:%H:%M} UTC" + (
            f": {p:.0f}% {category(p)}" if p is not None else "")
        if notify.email(subj, text, images()):
            state["briefing_sent"] = now.isoformat()
            print("Briefing-Mail gesendet")

    if state != old and not args.dry_run:
        STATE_DIR.mkdir(exist_ok=True)
        state_file.write_text(json.dumps(state))
    return {"stem": Path(path).stem, "fl": fl, "res": res, "legs": legs, "metars": metars, "text": text,
            "png": png, "history": state.get("history", []), "checked": now,
            "altitudes": altitudes(fl, res)[1:-1], "problems": problem_segments(res, legs), "notes": notes}


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
    # Auslöser von der Webseite: {"full": true, "check": ["flights/x.yaml"]}
    payload = {}
    if os.environ.get("GITHUB_EVENT_NAME") == "repository_dispatch" and os.environ.get("GITHUB_EVENT_PATH"):
        payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()).get("client_payload") or {}
    forced |= set(payload.get("check") or [])
    if args.files:
        files = args.files
    else:
        # Cron alle 10 min holt Telegram-Befehle; volle Prüfung aller Flüge nur einmal pro Stunde
        files = sorted(glob.glob("flights/*.yaml"))
        hourly = False
        now = dt.datetime.now(dt.timezone.utc)
        last = STATE_DIR / "last_full_check.txt"
        hourly = (os.environ.get("GITHUB_EVENT_NAME") not in ("schedule", "repository_dispatch") or not last.exists()
                  or payload.get("full")
                  or now - dt.datetime.fromisoformat(last.read_text().strip()) >= dt.timedelta(minutes=55))
        if hourly and not args.dry_run:
            STATE_DIR.mkdir(exist_ok=True)
            last.write_text(now.isoformat())
        if not hourly:
            files = [f for f in files if str(Path(f)) in {str(Path(x)) for x in created | forced}]
    summary, results = [], []
    full = bool(args.files) or hourly
    for f in files:
        try:
            r = run(f, args, force=str(Path(f)) in {str(Path(x)) for x in forced})
            if r:
                summary.append(r["text"])
                results.append(r)
        except Exception as e:
            print(f"{f}: Fehler: {e}")
            summary.append(f"{f}: Fehler: {e}")
            if str(Path(f)) in {str(Path(x)) for x in created | forced}:
                telegram_bot.reply(f"⚠ {Path(f).stem}: {e}")
    if full:  # Webseite nur bei vollständigen Läufen neu bauen
        site.build(results, Path("site"))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as s:
            s.write("\n\n---\n\n".join(f"```\n{t}\n```" for t in summary))


if __name__ == "__main__":
    main()
