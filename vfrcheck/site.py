"""Statische Webseite (GitHub Pages): Übersicht aller Flüge + Formular für neue Flugpläne."""
import datetime as dt
import os
from html import escape as e
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "dansparker/vfrcheck")
COLORS = {"GO": "var(--go)", "MARGINAL": "var(--marg)", "NO-GO": "var(--nogo)"}


def _cat(p):
    return None if p is None else "GO" if p >= 80 else "MARGINAL" if p >= 50 else "NO-GO"


def _spark(history):
    """Verlauf der Wahrscheinlichkeit als kleines SVG."""
    if len(history) < 2:
        return ""
    w, h = 240, 48
    vals = [v for _, v in history]
    pts = " ".join(f"{i * w / (len(vals) - 1):.1f},{h - v * h / 100:.1f}" for i, v in enumerate(vals))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" '
            f'aria-label="Verlauf {vals[0]}% bis {vals[-1]}%">'
            f'<line x1="0" x2="{w}" y1="{h * .2}" y2="{h * .2}" class="ref"/>'
            f'<line x1="0" x2="{w}" y1="{h * .5}" y2="{h * .5}" class="ref"/>'
            f'<polyline points="{pts}"/></svg>'
            f'<div class="muted small">Verlauf seit {history[0][0][5:16].replace("T", " ")} UTC</div>')


def _card(r):
    fl, res = r["fl"], r["res"]
    p = res["probability"]
    cat = _cat(p)
    dep = fl["departure"]
    left = dep - r["checked"]
    hrs = left.total_seconds() / 3600
    when = f"in {hrs:.0f} h" if hrs < 48 else f"in {hrs / 24:.1f} Tagen"
    legs = "".join(
        f"<tr><td>{e(lg['name'])}</td><td>{lg['dist']:.0f}</td><td>{lg['course']:03.0f}°</td><td>{lg['alt']}</td>"
        + (f"<td>{lg['wind'][0]:03.0f}°/{lg['wind'][1]:.0f}</td>"
           f"<td>{'Gegen' if lg['head'] >= 0 else 'Rücken'} {abs(lg['head']):.0f} · {abs(lg['cross']):.0f} v. {'re' if lg['cross'] >= 0 else 'li'}</td>"
           f"<td>{lg['gs']:.0f}</td><td>{lg['ete_min']:.0f}</td>" if lg["wind"] else "<td colspan=4>–</td>")
        + f"<td>{(str(round(lg['freezing_ft'] / 100) * 100)) if lg['freezing_ft'] else '–'}"
          f"{' <b class=warn>⚠ Eis ab ' + str(round(lg['icing_ft'] / 100) * 100) + '</b>' if lg['icing_ft'] else ''}</td></tr>"
        for lg in r["legs"])
    probs = "".join(f"<li>{e(x[2:])}</li>" for x in r["problems"]) or "<li class=muted>keine auffälligen Stellen</li>"
    alts = "".join(f"<li>{e(x.strip(' -·'))}</li>" for x in r["altitudes"])
    wx = "".join(f"<pre>{e(d[k])}</pre>" for d in r["metars"].values() for k in ("metar", "taf") if d.get(k))
    return f"""
<article class="card" id="{e(r['stem'])}">
  <header>
    <div>
      <h2>{e(fl['name'])}</h2>
      <div class="muted">Abflug {"Mo Di Mi Do Fr Sa So".split()[dep.weekday()]} {dep:%d.%m. %H:%M} UTC · {when} · {fl['cruise_alt_ft']} ft · TAS {fl['tas_kt']} kt</div>
    </div>
    <div class="prob" style="--c:{COLORS.get(cat, 'var(--muted)')}">
      <span class="num">{f'{p:.0f}%' if p is not None else '–'}</span>
      <span class="cat">{cat or 'außerhalb Vorhersage'}</span>
    </div>
  </header>
  {_spark(r['history'])}
  <div class="grid">
    <section><h3>Mögliche Flughöhen</h3><ul>{alts or '<li class=muted>–</li>'}</ul></section>
    <section><h3>Problemstellen</h3><ul>{probs}</ul></section>
  </div>
  <h3>Streckenabschnitte</h3>
  <div class="scroll"><table>
    <thead><tr><th>Abschnitt</th><th>NM</th><th>Kurs</th><th>ft</th><th>Wind kt</th><th>Gegen-/Rücken · Seite</th><th>GS</th><th>min</th><th>0 °C ft</th></tr></thead>
    <tbody>{legs}</tbody>
  </table></div>
  <h3>Querschnitt</h3>
  <a href="img/{e(r['stem'])}.png"><img src="img/{e(r['stem'])}.png" alt="Vertikalschnitt {e(fl['name'])}" loading="lazy"></a>
  <details><summary>METAR / TAF</summary>{wx or '<p class=muted>keine Daten</p>'}</details>
  <div class="muted small">geprüft {r['checked']:%d.%m. %H:%M} UTC · {res['members']} Ensemble-Member ·
    <a href="https://github.com/{REPO}/blob/main/flights/{e(r['stem'])}.yaml">Flugplan bearbeiten</a></div>
</article>"""


FORM = """
<article class="card" id="neu">
  <h2>Neuer Flug</h2>
  <p class="muted">Erzeugt den Flugplan und öffnet ihn im GitHub-Editor – dort nur noch „Commit changes“ klicken.
    Alternativ per Telegram: <code>/flug LOWW LOWG 12.10. 08:00 4500</code></p>
  <form id="f">
    <label>Name <input name="name" placeholder="LOWW-LOWG" required></label>
    <label>Abflug (UTC) <input name="dep" type="datetime-local" required></label>
    <label>Höhe ft <input name="alt" type="number" value="4500" step="500" required></label>
    <label>TAS kt <input name="tas" type="number" value="100" required></label>
    <label class="wide"><span>Route – ein Wegpunkt pro Zeile: ICAO oder <code>lat,lon</code> oder <code>lat,lon,Name,Höhe</code></span>
      <textarea name="route" rows="4" placeholder="LOWW&#10;47.84,16.22,Wiener Neustadt&#10;LOWG"></textarea></label>
    <label class="wide"><span>… oder GPX-Datei (vorher <a href="https://github.com/__REPO__/upload/main/flights" target="_blank">hochladen</a>)</span>
      <input name="gpx" placeholder="meine-route.gpx"></label>
    <details class="wide"><summary>Minima (optional)</summary>
      <div class="row">
        <label>Sicht m <input name="vis_m" type="number" value="5000"></label>
        <label>Ceiling ft AGL <input name="ceiling_ft_agl" type="number" value="1500"></label>
        <label>Max. Böen kt <input name="max_gust_kt" type="number" value="25"></label>
        <label>Geländeabstand ft <input name="terrain_clearance_ft" type="number" value="1000"></label>
      </div></details>
    <button class="wide">Auf GitHub anlegen</button>
  </form>
  <pre id="out" hidden></pre>
</article>
<script>
document.getElementById('f').addEventListener('submit', ev => {
  ev.preventDefault();
  const d = Object.fromEntries(new FormData(ev.target));
  const route = d.route.split('\\n').map(s => s.trim()).filter(Boolean).map(l => {
    const p = l.split(',').map(s => s.trim());
    if (p.length === 1) return '  - ' + p[0].toUpperCase();
    let s = `  - {lat: ${p[0]}, lon: ${p[1]}`;
    if (p[2]) s += `, name: "${p[2].replace(/"/g, '')}"`;
    if (p[3]) s += `, alt_ft: ${p[3]}`;
    return s + '}';
  });
  if (!d.gpx && route.length < 2) { alert('Mindestens zwei Wegpunkte oder eine GPX-Datei angeben'); return; }
  const yaml = [`name: ${d.name}`, `departure: ${d.dep}:00Z   # UTC`, `tas_kt: ${d.tas}`, `cruise_alt_ft: ${d.alt}`,
    d.gpx ? `gpx: ${d.gpx}` : 'route:\\n' + route.join('\\n'),
    'minima:', ...['vis_m', 'ceiling_ft_agl', 'max_gust_kt', 'terrain_clearance_ft'].map(k => `  ${k}: ${d[k]}`)].join('\\n') + '\\n';
  const file = d.dep.slice(0, 16).replace(/[-:T]/g, '').replace(/^(\\d{8})/, '$1-') + '-' + d.name.replace(/[^A-Za-z0-9]+/g, '-') + '.yaml';
  const out = document.getElementById('out'); out.hidden = false; out.textContent = yaml;
  window.open(`https://github.com/__REPO__/new/main/flights?filename=${encodeURIComponent(file)}&value=${encodeURIComponent(yaml)}`, '_blank');
});
</script>"""

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--fg:#1c2330;--muted:#667085;--line:#e3e6eb;--go:#1f8f45;--marg:#c27c0e;--nogo:#c62f2f;--acc:#2563eb}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0f1318;--card:#171c23;--fg:#e6e9ee;--muted:#98a2b3;--line:#2a313b;--go:#3ccf74;--marg:#f0a32a;--nogo:#ff6464;--acc:#6ea0ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}a{color:var(--acc)}
.top{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin:8px 0 16px}
.top h1{margin:0;font-size:26px}.muted{color:var(--muted)}.small{font-size:13px}
.overview{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:16px}
.chip{display:flex;gap:8px;align-items:center;padding:6px 12px;border:1px solid var(--line);border-radius:999px;background:var(--card);text-decoration:none;color:var(--fg)}
.chip b{color:var(--c)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px;margin-bottom:18px}
.card header{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
h2{margin:0 0 2px;font-size:21px}h3{font-size:14px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);margin:16px 0 6px}
.prob{text-align:right;color:var(--c)}.prob .num{font-size:38px;font-weight:700;line-height:1;display:block}.prob .cat{font-weight:600;font-size:13px}
.spark{width:240px;max-width:100%;height:48px;display:block;margin-top:8px}.spark polyline{fill:none;stroke:var(--acc);stroke-width:2}.spark .ref{stroke:var(--line);stroke-dasharray:3 3}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}ul{margin:0;padding-left:18px}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:14px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}th{color:var(--muted);font-weight:600}
.warn{color:var(--nogo)}img{width:100%;border-radius:8px;border:1px solid var(--line);background:#fff}
pre{white-space:pre-wrap;font-size:13px;background:var(--bg);padding:8px;border-radius:8px;margin:6px 0}
details{margin-top:10px}summary{cursor:pointer;color:var(--acc)}
form{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
.wide{grid-column:1/-1}.row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:8px}
input,textarea{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:8px}
button{font:inherit;font-weight:600;padding:10px;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer}
@media (max-width:700px){.grid,form,.row{grid-template-columns:1fr}.prob .num{font-size:30px}}
"""


def build(results, out):
    (out / "img").mkdir(parents=True, exist_ok=True)
    for r in results:
        (out / "img" / f"{r['stem']}.png").write_bytes(r["png"])
    results = sorted(results, key=lambda r: r["fl"]["departure"])
    chips = ""
    for r in results:
        p = r["res"]["probability"]
        chips += (f'<a class="chip" href="#{e(r["stem"])}" style="--c:{COLORS.get(_cat(p), "var(--muted)")}">'
                  f'{e(r["fl"]["name"])} <span class="muted">{r["fl"]["departure"]:%d.%m. %H:%MZ}</span>'
                  f'<b>{"–" if p is None else f"{p:.0f}%"}</b></a>')
    now = dt.datetime.now(dt.timezone.utc)
    body = "".join(_card(r) for r in results) or '<p class="muted">Keine geplanten Flüge.</p>'
    html = f"""<!doctype html>
<html lang="de"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>vfrcheck</title><meta http-equiv="refresh" content="900"><style>{CSS}</style></head>
<body><main>
<div class="top"><h1>vfrcheck</h1><span class="muted small">Stand {now:%d.%m.%Y %H:%M} UTC · aktualisiert stündlich ·
  <a href="#neu">Neuer Flug</a> · <a href="https://github.com/{REPO}">GitHub</a></span></div>
<nav class="overview">{chips}</nav>
{body}
{FORM.replace('__REPO__', REPO)}
<p class="muted small">Ensemble-Daten: Open-Meteo (ICON-EPS, ECMWF-ENS), Schnitt: ICON-D2; METAR/TAF: aviationweather.gov.
Kein Ersatz für ein offizielles Briefing – die Entscheidung trifft der PIC.</p>
</main></body></html>"""
    (out / "index.html").write_text(html, encoding="utf-8")
