"""GRAMET-ähnlicher Vertikalschnitt mit markierten Problemstellen."""
import io
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from .section import _uv, icing_levels  # noqa: E402

SHORT = {"Sicht": "VIS", "Ceiling": "CIG", "Wolkenbasis": "CLD", "Böen": "GUST",
         "starker": "RAIN", "Gewitter": "CB"}


def _short(reason):
    return next((v for k, v in SHORT.items() if reason.startswith(k)), reason[:4])


def _color(pct):
    return "#2e9d4a" if pct < 20 else "#e8a317" if pct < 50 else "#d62f2f"


def render(fl, points, res, profs, legs):
    x = np.array([p.dist for p in points])
    elev = np.array([r.get("elev_ft", 0) for r in res["points"]])
    ymax = max(10000, max(p.alt_ft for p in points) + 4000)

    fig, (ax, axp) = plt.subplots(2, 1, figsize=(13, 7.5), sharex=True,
                                  gridspec_kw={"height_ratios": [4, 1], "hspace": 0.05})
    # Wolken aus Druckflächen (deterministisch, ICON)
    ok = [i for i, pr in enumerate(profs) if pr is not None]
    if len(ok) >= 2:
        X = np.array([[x[i]] * len(profs[i]["z"]) for i in ok])
        Z = np.array([profs[i]["z"] for i in ok])
        C = np.nan_to_num(np.array([profs[i]["cloud"] for i in ok]))
        cf = ax.contourf(X, Z, C, levels=[10, 30, 50, 75, 90, 101], cmap="Greys", alpha=0.75, vmin=0, vmax=130)
        fig.colorbar(cf, ax=[ax, axp], pad=0.01, label="Bewölkung %")
        # Vereisung, Nullgradgrenze, Windfahnen
        for i in ok:
            ice = icing_levels(profs[i])
            ax.scatter([x[i]] * len(ice), ice, marker="*", s=40, color="#1f6fd1", zorder=5)
        frz = [(x[i], profs[i]["freezing_ft"]) for i in ok if profs[i]["freezing_ft"] is not None]
        if frz:
            ax.plot(*zip(*frz), color="#1f6fd1", ls="--", lw=1.3, label="0 °C")
        step = max(1, len(ok) // 15)
        for i in ok[::step]:
            pr = profs[i]
            sel = (pr["z"] <= ymax) & (pr["z"] > elev[i] + 200)
            u, v = _uv(pr["ws"][sel], pr["wd"][sel])
            ax.barbs([x[i]] * sel.sum(), pr["z"][sel], u, v, length=5.5, lw=0.7, color="#5a3d8a", zorder=6)
    # Höhenband (80 % unter tiefer Bewölkung), Ensemble-Wolkenbasis
    bands = [r.get("band") for r in res["points"]]
    if all(bands):
        lo = np.array([b[0] for b in bands])
        hi = np.clip([b[1] for b in bands], 0, ymax)
        ax.fill_between(x, lo, hi, where=hi >= lo, color="#2e9d4a", alpha=0.15, label="mögliche Flughöhen")
        base = np.array([b[2] if b[2] != math.inf else np.nan for b in bands])
        ax.plot(x, base, color="#888", ls=":", lw=1.5, label="Wolkenbasis (Ensemble, 80 %)")
    # Problemstellen hinterlegen
    for r, xi in zip(res["points"], x):
        if r["fail_pct"] and r["fail_pct"] >= 20:
            ax.axvspan(xi - 2.5, xi + 2.5, color=_color(r["fail_pct"]), alpha=0.12, lw=0)
    ax.fill_between(x, 0, elev, color="#8b6b43", zorder=4, label="Gelände (Stützpunkte)")
    ax.step(x, [p.alt_ft for p in points], where="pre", color="black", lw=2, label="geplante Höhe", zorder=7)
    ax.set_ylim(0, ymax)
    ax.set_ylabel("ft MSL")
    ax.grid(alpha=0.3)
    ax.set_xlim(0, x[-1])
    p = res["probability"]
    ax.set_title(f"{fl['name']}  ·  Abflug {fl['departure']:%d.%m. %H:%M} UTC  ·  "
                 + (f"VFR {p:.0f} %" if p is not None else "außerhalb Vorhersage"), fontsize=12, loc="left")
    # Wegpunkte + Wind je Abschnitt oben
    top = ax.secondary_xaxis("top")
    wp = [pt for pt in points if pt.waypoint]
    top.set_xticks([pt.dist for pt in wp], [pt.name for pt in wp], fontsize=9)
    starts = [0.0] + [pt.dist for pt in wp][:-1]
    for lg, x0, pt in zip(legs, starts, wp):
        if lg["wind"]:
            ax.text((x0 + pt.dist) / 2, ymax * 0.97, f"{lg['wind'][0]:03.0f}°/{lg['wind'][1]:.0f} kt\nGS {lg['gs']:.0f}",
                    ha="center", va="top", fontsize=8, color="#5a3d8a",
                    bbox={"fc": "white", "ec": "none", "alpha": 0.7})
    for pt in wp:
        ax.axvline(pt.dist, color="black", lw=0.5, alpha=0.4)

    # Streifen: Anteil kritischer Member + Hauptgrund
    fails = [r["fail_pct"] or 0 for r in res["points"]]
    axp.bar(x, fails, width=max(1.0, (x[-1] / len(x)) * 0.9), color=[_color(f) for f in fails])
    last = None
    for r, xi in zip(res["points"], x):
        if r["fail_pct"] and r["fail_pct"] >= 20 and r["reasons"]:
            lbl = "/".join(_short(k) for k in list(r["reasons"])[:2])
            if lbl != last:
                axp.text(xi, min(r["fail_pct"], 85) + 4, lbl, ha="center", fontsize=7.5, rotation=90)
            last = lbl
        else:
            last = None
    h, l = ax.get_legend_handles_labels()
    axp.legend(h, l, loc="upper left", fontsize=7.5, ncol=4, framealpha=0.85)
    axp.set_ylim(0, 100)
    axp.set_ylabel("kritisch %")
    axp.set_xlabel("NM  (VIS Sicht · CIG Ceiling · CLD Basis<Höhe · GUST Böen · RAIN Niederschlag · CB Gewitter · ★ Vereisung)",
                   fontsize=8)
    axp.grid(alpha=0.3)
    ticks = np.linspace(0, x[-1], 7)
    axp.set_xlim(0, x[-1])
    axp.set_xticks(ticks, [f"{t:.0f}\n{points[int(np.argmin(abs(x - t)))].eta:%H:%M}Z" for t in ticks], fontsize=8)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()
