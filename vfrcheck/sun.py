"""Bürgerliche Dämmerung (Sonne 6° unter dem Horizont), NOAA-Näherung, Genauigkeit einige Minuten."""
import datetime as dt
import math


def civil_twilight(date, lat, lon):
    """(Beginn, Ende) der bürgerlichen Dämmerung in UTC; None bei Polartag/-nacht."""
    n = date.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    x = math.cos(math.radians(96)) / (math.cos(la) * math.cos(decl)) - math.tan(la) * math.tan(decl)
    if abs(x) > 1:
        return None
    ha = math.degrees(math.acos(x))
    midnight = dt.datetime.combine(date, dt.time(), dt.timezone.utc)
    return (midnight + dt.timedelta(minutes=720 - 4 * (lon + ha) - eqt),
            midnight + dt.timedelta(minutes=720 - 4 * (lon - ha) - eqt))


def daylight_notes(points, margin_min=30):
    """Warnungen, wenn Abflug vor Beginn oder Landung nach Ende der bürgerlichen Dämmerung liegt."""
    notes = []
    dep, arr = points[0], points[-1]
    tw_dep = civil_twilight(dep.eta.date(), dep.lat, dep.lon)
    tw_arr = civil_twilight(arr.eta.date(), arr.lat, arr.lon)
    if tw_arr:
        dusk = tw_arr[1]
        left = (dusk - arr.eta).total_seconds() / 60
        if left < 0:
            notes.append(f"⚠ Landung {arr.eta:%H:%M}Z NACH Ende der bürgerlichen Dämmerung ({dusk:%H:%M}Z) – Nachtflug!")
        elif left < margin_min:
            notes.append(f"⚠ Landung {arr.eta:%H:%M}Z nur {left:.0f} min vor Ende der bürgerl. Dämmerung ({dusk:%H:%M}Z)")
        else:
            notes.append(f"Tageslicht: Landung {arr.eta:%H:%M}Z, Ende bürgerl. Dämmerung {dusk:%H:%M}Z (+{left:.0f} min)")
    if tw_dep and dep.eta < tw_dep[0]:
        notes.append(f"⚠ Abflug {dep.eta:%H:%M}Z VOR Beginn der bürgerlichen Dämmerung ({tw_dep[0]:%H:%M}Z)")
    return notes
