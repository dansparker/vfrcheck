"""Offline-Tests der Kernfunktionen (ohne Netzwerk). Ausführen: python -m pytest -q"""
import datetime as dt
import json

import pytest

from vfrcheck import route, sun, telegram_bot, verify
from vfrcheck.route import Point

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 11, 27, tzinfo=UTC)


def test_parse_flug_varianten():
    r, dep, alt, ex = telegram_bot.parse_flug("LOWW 47.84,16.22 lowg 12.12. 0800 4500 110".split(), NOW)
    assert r == ["LOWW", {"lat": 47.84, "lon": 16.22}, "LOWG"] and alt == 4500 and ex == {"tas_kt": 110}
    assert dep == dt.datetime(2026, 12, 12, 8, 0, tzinfo=UTC)
    _, _, _, ex = telegram_bot.parse_flug("LOWW LOWG 12.12. 08:00Z 4500 D-EABC altn=lowi".split(), NOW)
    assert ex == {"aircraft": "D-EABC", "alternate": "LOWI"}
    _, dep, _, ex = telegram_bot.parse_flug("3.1. 08:00 4500".split(), NOW, with_route=False)
    assert dep.year == 2027 and ex == {"tas_kt": telegram_bot.DEFAULT_TAS}
    for bad in ("LOWW LOWG 12.12.", "LOWW 12.12. 08:00 4500", "LOWW LOWG 12.12. 08:00 4500 quatsch"):
        with pytest.raises(ValueError):
            telegram_bot.parse_flug(bad.split(), NOW)


def test_routendateien(tmp_path):
    (tmp_path / "r.gpx").write_text('<gpx xmlns="http://www.topografix.com/GPX/1/1"><rte>'
                                    '<rtept lat="48.11" lon="16.57"><name>LOWW</name></rtept>'
                                    '<rtept lat="47.84" lon="16.22"><name>WN</name></rtept>'
                                    '<rtept lat="46.99" lon="15.44"><name>LOWG</name></rtept></rte></gpx>')
    (tmp_path / "r.fpl").write_text(
        '<flight-plan xmlns="http://www8.garmin.com/xmlschemas/FlightPlan/v1"><waypoint-table>'
        '<waypoint><identifier>LOWW</identifier><type>AIRPORT</type><lat>48.11</lat><lon>16.57</lon></waypoint>'
        '<waypoint><identifier>LOWG</identifier><type>AIRPORT</type><lat>46.99</lat><lon>15.44</lon></waypoint>'
        '</waypoint-table><route><route-point><waypoint-identifier>LOWW</waypoint-identifier></route-point>'
        '<route-point><waypoint-identifier>LOWG</waypoint-identifier></route-point></route></flight-plan>')
    assert [w.get("icao") for w in route.parse_gpx(tmp_path / "r.gpx")] == ["LOWW", None, "LOWG"]
    assert [w["icao"] for w in route.parse_fpl(tmp_path / "r.fpl")] == ["LOWW", "LOWG"]
    assert route._sd_coord("N474253.00 E0164415.00") == pytest.approx((47.7147, 16.7375), abs=1e-3)


def test_flugplan_mit_flugzeugprofil(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "aircraft").mkdir()
    (tmp_path / "aircraft" / "D-EABC.yaml").write_text("tas_kt: 105\nperformance:\n  climb_fpm: 600\n")
    f = tmp_path / "f.yaml"
    f.write_text("name: T\ndeparture: 2026-12-01T09:00Z\ncruise_alt_ft: 4500\naircraft: D-EABC\n"
                 "route:\n  - {lat: 48.1, lon: 16.5, name: A}\n  - {lat: 47.0, lon: 15.4, name: B}\n")
    fl = route.load_flight(f)
    assert fl["tas_kt"] == 105 and route.perf(fl)["climb_fpm"] == 600
    assert fl["departure"] == dt.datetime(2026, 12, 1, 9, 0, tzinfo=UTC)
    f.write_text("name: T\ndeparture: 2026-12-01T09:00\ncruise_alt_ft: 4500\nroute: [{lat: 48, lon: 16}, {lat: 47, lon: 15}]\n")
    with pytest.raises(ValueError):  # weder TAS noch Flugzeug
        route.load_flight(f)


def test_hoehenprofil_steigt_und_sinkt():
    fl = {"tas_kt": 100, "departure": NOW, "performance": {"climb_fpm": 500, "descent_fpm": 500}}
    pts = [Point(f"P{i}", 48, 16, NOW, 6000, dist=i * 5.0, leg=0) for i in range(13)]
    route.vertical_profile(pts, fl, [100] * len(pts), dep_elev=1000, dest_elev=1000)
    alts = [p.alt_ft for p in pts]
    assert alts[0] == 1000 and max(alts) == 6000 and alts[-1] == 2000  # Platzrunde = Platz + 1000 ft
    assert alts.index(6000) > 1  # Steigflug braucht Strecke


def test_daemmerung_wien():
    dawn, dusk = sun.civil_twilight(dt.date(2026, 11, 27), 48.11, 16.57)
    assert dt.time(5, 30) < dawn.time() < dt.time(5, 55) and dt.time(15, 25) < dusk.time() < dt.time(15, 50)


def test_metar_auswertung():
    assert verify._vis_m(" METAR X 271950Z 32012KT 4000 BR OVC006 ", None) == 4000
    assert verify._vis_m(" METAR X 271950Z 32012KT CAVOK ", None) == 10000
    assert verify._ceiling_ft([{"cover": "SCT", "base": 800}, {"cover": "BKN", "base": 1200}]) == 1200
    assert verify._ceiling_ft([{"cover": "FEW", "base": 800}]) is None


def test_statistik(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "LOG", tmp_path / "v.json")
    (tmp_path / "v.json").write_text(json.dumps([{"forecast": 80, "observed_vfr": True},
                                                 {"forecast": 30, "observed_vfr": True}]))
    s = verify.stats()
    assert s["n"] == 2 and s["hits"] == 1 and s["brier"] == pytest.approx((0.04 + 0.49) / 2)


def test_webseite_formular_js_escapes():
    """Das JavaScript im Formular darf keine Steuerzeichen und keine doppelten Escapes enthalten."""
    from vfrcheck import site
    assert not [c for c in site.FORM if ord(c) < 32 and c not in "\n\r\t"]
    assert r"split('\n')" in site.FORM and r"split('\\n')" not in site.FORM
