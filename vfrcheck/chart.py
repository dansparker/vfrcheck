"""GRAMET-ähnlicher Vertikalschnitt mit Ampel für kritische Bereiche."""
import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import LineCollection  # noqa: E402

from .section import _uv, cloud_layers, icing_levels  # noqa: E402

SHORT = {"Sicht": "Sicht", "Ceiling": "Wolken tief", "Wolkenbasis": "Wolken", "Böen": "Böen",
         "starker": "Regen", "Gewitter": "Gewitter"}
GREEN, YELLOW, RED = "#2e9d4a", "#f2b705", "#d62f2f"
LEVEL_COLOR = [GREEN, YELLOW, RED]
SKY = "#eef5fb"
# Bewölkung je Druckfläche: Klassen ähnlich FEW/SCT/BKN/OVC
CLOUD_LEVELS = [25, 50, 75, 90, 101]
CLOUD_COLORS = ["#dfe3e8", "#bcc3cc", "#959eaa", "#6f7985"]
CLOUD_NAMES = ["Wolken 25–50 % (SCT)", "50–75 % (BKN)", "75–90 % (BKN+)", "> 90 % (OVC)"]


def _short(reason):
    return next((v for k, v in SHORT.items() if reason.startswith(k)), reason[:6])


def risk(points, res, profs, minima):
    """Ampelstufe (0 grün, 1 gelb, 2 rot) und Gründe je Stützpunkt.
    Quellen: kritischer Anteil (Ensemble + ICON-Wolken), Vereisung (ICON), Gelände unter dem Profil."""
    out = []
    for p, r, pr in zip(points, res["points"], profs):
        level, why = 0, []
        f = r.get("fail_pct") or 0
        if r.get("icon_reasons"):  # ICON sieht Wolken auf dem Flugweg bzw. unter den Minima
            level = 2
            why += [x.split(" ~")[0] for x in r["icon_reasons"]]
        if f >= 20:
            level = max(level, 2 if f >= 50 else 1)
            why += [f"{_short(k)} {v:.0f}%" for k, v in list(r["reasons"].items())[:2]]
        if pr is not None:
            if any(abs(z - p.alt_ft) < 1000 for z in icing_levels(pr)):
                level = max(level, 1)
                why.append("Vereisung")
        ground = r.get("elev_ft")
        if ground is not None and p.alt_ft - ground < 300 and 0 < p.dist < points[-1].dist - 0.5:
            level = 2
            why.append("Gelände")
        out.append((level, why))
    return out


def render(fl, points, res, profs, legs, spaces=()):
    x = np.array([p.dist for p in points])
    elev = np.array([r.get("elev_ft") or 0 for r in res["points"]], dtype=float)
    alt = np.array([p.alt_ft for p in points])
    ymax = max(10000, alt.max() + 4000)
    lv = risk(points, res, profs, fl["minima"])
    half = (x[1] - x[0]) / 2 if len(x) > 1 else 1

    fig, (axr, ax) = plt.subplots(2, 1, figsize=(13, 8), sharex=True,
                                  gridspec_kw={"height_ratios": [0.55, 4], "hspace": 0.04})
    ax.set_facecolor(SKY)

    # --- Ampelband oben: kritische Bereiche entlang der Strecke ---
    for xi, (level, why) in zip(x, lv):
        axr.axvspan(xi - half, xi + half, color=LEVEL_COLOR[level], lw=0)
    # Beschriftung je zusammenhängendem gelb/rotem Abschnitt
    i = 0
    while i < len(lv):
        if lv[i][0] == 0:
            i += 1
            continue
        j = i
        while j + 1 < len(lv) and lv[j + 1][0] > 0:
            j += 1
        worst = max(range(i, j + 1), key=lambda k: lv[k][0])
        txt = ", ".join(dict.fromkeys(w for k in range(i, j + 1) for w in lv[k][1]))
        axr.text((x[i] + x[j]) / 2, 0.5, txt[:60], ha="center", va="center", fontsize=8, fontweight="bold",
                 color="white" if lv[worst][0] == 2 else "black")
        # Bereich im Schnitt hinterlegen
        ax.axvspan(x[i] - half, x[j] + half, color=LEVEL_COLOR[lv[worst][0]], alpha=0.18, lw=0, zorder=0.5)
        i = j + 1
    axr.set_yticks([])
    axr.set_ylim(0, 1)
    axr.set_ylabel("Ampel", rotation=0, ha="right", va="center", fontsize=9)
    p_ = res["probability"]
    axr.set_title(f"{fl['name']}  ·  Abflug {fl['departure']:%d.%m. %H:%M} UTC  ·  "
                  + (f"VFR-Wahrscheinlichkeit {p_:.0f} %" if p_ is not None else "außerhalb Vorhersage"),
                  fontsize=12, loc="left")
    top = axr.secondary_xaxis("top")
    wp = [pt for pt in points if pt.waypoint]
    top.set_xticks([pt.dist for pt in wp], [pt.name for pt in wp], fontsize=9)

    # --- Wolken aus ICON (Druckflächen) ---
    ok = [i for i, pr in enumerate(profs) if pr is not None]
    handles = []
    if len(ok) >= 2:
        for k in ok:  # je Stützpunkt die Schichten als Block, Farbe nach Bedeckung
            for b, t, c in cloud_layers(profs[k], thresh=CLOUD_LEVELS[0]):
                col = CLOUD_COLORS[max(i for i, lv_ in enumerate(CLOUD_LEVELS[:-1]) if c >= lv_)]
                ax.add_patch(mpatches.Rectangle((x[k] - half, b), 2 * half, t - b, color=col, lw=0, zorder=2))
        handles += [mpatches.Patch(color=c, label=n) for c, n in zip(CLOUD_COLORS, CLOUD_NAMES)]
        # Wolkenschichten (ab 50 %) beschriften
        i = 0
        while i < len(ok):
            lay = cloud_layers(profs[ok[i]])
            if not lay:
                i += 1
                continue
            j = i
            while j + 1 < len(ok) and cloud_layers(profs[ok[j + 1]]):
                j += 1
            group = [cloud_layers(profs[ok[k]])[0] for k in range(i, j + 1)]
            b, t = min(g[0] for g in group), max(g[1] for g in group)
            if t > 0 and b < ymax:
                ax.text((x[ok[i]] + x[ok[j]]) / 2, min((b + t) / 2, ymax * 0.9),
                        f"Wolken\n{round(b / 100) * 100:.0f}–{round(t / 100) * 100:.0f} ft",
                        ha="center", va="center", fontsize=8, color="#1f2937", zorder=9,
                        bbox={"fc": "white", "ec": "#6b7280", "alpha": 0.8, "boxstyle": "round,pad=0.25"})
            i = j + 1
        # Vereisung, Nullgradgrenze, Windfahnen
        for k in ok:
            ice = icing_levels(profs[k])
            ax.scatter([x[k]] * len(ice), ice, marker="*", s=45, color="#1f6fd1", zorder=5)
        handles.append(plt.Line2D([], [], marker="*", ls="", color="#1f6fd1", label="Vereisungsgefahr"))
        frz = [(x[k], profs[k]["freezing_ft"]) for k in ok if profs[k]["freezing_ft"] is not None]
        if frz:
            ax.plot(*zip(*frz), color="#1f6fd1", ls="--", lw=1.3, zorder=5)
            handles.append(plt.Line2D([], [], color="#1f6fd1", ls="--", label="0 °C-Grenze"))
        step = max(1, len(ok) // 15)
        for k in ok[::step]:
            pr = profs[k]
            sel = (pr["z"] <= ymax) & (pr["z"] > elev[k] + 200)
            u, v = _uv(pr["ws"][sel], pr["wd"][sel])
            ax.barbs([x[k]] * sel.sum(), pr["z"][sel], u, v, length=5.5, lw=0.7, color="#5a3d8a", zorder=6)

    # --- mögliche Flughöhen (Ensemble + ICON) ---
    bands = [r.get("band") for r in res["points"]]
    if all(bands):
        lo = np.array([b[0] for b in bands])
        hi = np.clip([b[1] for b in bands], 0, ymax)
        ax.fill_between(x, lo, hi, where=hi >= lo, color=GREEN, alpha=0.13, zorder=1)
        handles.append(mpatches.Patch(color=GREEN, alpha=0.3, label="mögliche Flughöhen"))

    # --- Gelände ---
    ter = np.array([r.get("terrain_ft") or np.nan for r in res["points"]], dtype=float)
    if not np.isnan(ter).all():
        ax.fill_between(x, 0, ter, color="#c9a97a", zorder=1.5)  # seitliches Gelände: hinter den Wolken
        handles.append(mpatches.Patch(color="#c9a97a", label="Gelände max. ±5 NM"))
    ax.fill_between(x, 0, elev, color="#8b6b43", zorder=4)
    handles.append(mpatches.Patch(color="#8b6b43", label="Gelände auf Kurs"))

    for sp in spaces:  # Lufträume als Rahmen auf ihrer Höhe
        hi_ = min(sp["hi"], ymax)
        col = RED if sp["type"] in ("R", "D", "P", "TRA", "TSA") else "#1f6fd1"
        ax.add_patch(mpatches.Rectangle((sp["from"], sp["lo"]), max(sp["to"] - sp["from"], 0.5),
                                        hi_ - sp["lo"], fill=False, ec=col, lw=1.2, zorder=8))
        ax.text(sp["from"] + 0.5, min(hi_, ymax * 0.93) - 150, f"{sp['type']} {sp['name']}"[:28], fontsize=7,
                color=col, va="top", zorder=8)

    # --- Flugweg: Planhöhe + Höhenprofil in Ampelfarben ---
    ax.step(x, [p.plan_ft for p in points], where="pre", color="#555", lw=1, ls="--", zorder=7)
    handles.append(plt.Line2D([], [], color="#555", ls="--", label="Planhöhe"))
    seg = np.stack([np.column_stack([x[:-1], alt[:-1]]), np.column_stack([x[1:], alt[1:]])], axis=1)
    ax.add_collection(LineCollection(seg, colors="black", linewidths=6, zorder=7))
    ax.add_collection(LineCollection(seg, colors=[LEVEL_COLOR[max(a[0], b[0])] for a, b in zip(lv, lv[1:])],
                                     linewidths=3.5, zorder=7))
    handles.append(plt.Line2D([], [], color=GREEN, lw=3.5, label="Flugweg (Farbe = Ampel)"))

    for lg, a, pt in zip(legs, wp, wp[1:]):
        if lg["wind"]:
            ax.text((a.dist + pt.dist) / 2, ymax * 0.97, f"Wind {lg['wind'][0]:03.0f}°/{lg['wind'][1]:.0f} kt\nGS {lg['gs']:.0f} kt",
                    ha="center", va="top", fontsize=8, color="#5a3d8a", zorder=9,
                    bbox={"fc": "white", "ec": "none", "alpha": 0.8})
    for pt in wp:
        ax.axvline(pt.dist, color="black", lw=0.5, alpha=0.4, zorder=6)

    ax.set_ylim(0, ymax)
    ax.set_xlim(0, x[-1])
    ax.set_ylabel("Höhe ft MSL")
    ax.grid(alpha=0.3, zorder=0)
    ticks = np.linspace(0, x[-1], 7)
    ax.set_xticks(ticks, [f"{t:.0f} NM\n{points[int(np.argmin(abs(x - t)))].eta:%H:%M}Z" for t in ticks], fontsize=8)
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=5, fontsize=8, frameon=False)
    fig.text(0.01, -0.06,
             "So liest du die Grafik: Oben die Ampel – grün = unkritisch, gelb = Vorsicht (20–50 % der Wetterszenarien "
             "kritisch oder Vereisung), rot = kritisch (≥ 50 %, ICON-Wolken auf dem Flugweg/zu tief oder zu nah am Gelände).\n"
             "Grau = Wolken aus dem ICON-Modell (je dunkler, desto dichter). Grün = Höhenbereich zwischen "
             "Gelände + Abstand und Wolkenbasis. Violette Fahnen = Wind je Höhe (langer Strich 10 kt, kurzer 5 kt).",
             fontsize=8, color="#374151", va="top")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
