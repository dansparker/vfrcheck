# vfrcheck

Prüft anhand eines Flugplans, wie wahrscheinlich ein Flug unter **VFR-Bedingungen** durchführbar ist, zeigt kritische Stellen entlang der Strecke und meldet starke Änderungen per **Telegram, E-Mail oder Signal**.

## So funktioniert es

1. Flugplan in `flights/*.yaml` (Wegpunkte als ICAO-Code oder lat/lon, Abflugzeit, TAS, Reiseflughöhe, persönliche Minima).
2. Die Strecke wird alle **5 NM** (`sample_nm` im Flugplan) in Stützpunkte zerlegt, jeder bekommt eine ETA. Ensembles (~25 km Gitter) werden alle ~15 NM abgefragt, Gelände/Wolkenschnitt/Wind dicht aus **ICON-D2** (2 km).
3. Für jeden Punkt werden **Ensemble-Vorhersagen** von [Open-Meteo](https://open-meteo.com) abgefragt (kostenlos):
   - **ICON-EPS** (DWD, ~40 Member) und **ECMWF-ENS** (~51 Member), zusammen rund 90 Szenarien
   - Sicht, tiefe Bewölkung, Wolkenbasis (geschätzt über die Taupunktdifferenz), Böen, Niederschlag, CAPE
4. **Wahrscheinlichkeit** = Anteil der Member, in denen *jeder* Punkt der Strecke die Minima erfüllt.
   Kategorien: ≥80 % GO, ≥50 % MARGINAL, darunter NO-GO.
5. **Problemstellen**: pro Punkt der Anteil kritischer Member samt Grund (z. B. „Semmering: 35 % kritisch, Ceiling < 1500 ft“).
6. **METAR/TAF** der Flugplätze (aviationweather.gov) und optional ein **GRAMET**-Querschnitt (autorouter) werden mitgeschickt.
7. **Mögliche Flughöhen**: Höhenband pro Punkt und für die ganze Strecke. Untergrenze = Geländehöhe des Stützpunkts + `terrain_clearance_ft` (1000 ft), Obergrenze = Wolkenbasis, die in 80 % der Member übertroffen wird, − `clearance_ft` (500 ft). Liegt die geplante Höhe außerhalb, wird gewarnt.
8. **Briefing-Mail 2 h vor Abflug** mit aktueller Zusammenfassung (+ GRAMET), einmalig pro Flug (`--briefing` sendet sofort).
9. **Wind je Streckenabschnitt** auf Planhöhe: Richtung/Stärke, Gegen-/Rückenwind, Seitenwind, Groundspeed, Flugzeit, Nullgradgrenze, Vereisungsgefahr (Wolken bei −10…0 °C).
10. **Querschnitt-Grafik** (GRAMET-ähnlich, `charts/*.png`, wird mit Telegram/E-Mail verschickt und im Actions-Lauf als Download abgelegt): Gelände, Wolken je Höhe, 0 °C-Linie, Vereisung (★), Windfahnen, geplante Höhe, mögliches Höhenband (grün), Problemstellen farbig hinterlegt und im unteren Streifen mit Grund (VIS/CIG/CLD/GUST/RAIN/CB).
11. Benachrichtigt wird bei der ersten Bewertung, bei einer Änderung um ≥ `delta_pct` Prozentpunkte oder bei einem Wechsel der Kategorie.

## Webseite

**https://dansparker.github.io/vfrcheck/**: Übersicht aller geplanten Flüge mit Wahrscheinlichkeit, Verlauf, Flughöhen, Wind je Abschnitt, Problemstellen, Querschnitt und METAR/TAF. Sie wird stündlich per GitHub Pages neu erzeugt.
Mit dem Formular „Neuer Flug“ wird der Flugplan im GitHub-Editor angelegt; dort nur noch „Commit changes“ klicken.
⚠️ Solange das Repo öffentlich ist, ist auch die Seite öffentlich.

## Flugplan übergeben (alle Zeiten UTC)

**1. Telegram (am bequemsten)**: Befehle an deinen Bot schicken; sie werden alle ~10 min abgeholt, die Bewertung kommt dann als Nachricht.

```
/flug LOWW LOAN LOWG 12.10. 08:00 4500 105     Wegpunkte (ICAO oder 47.63,15.83), Datum, Zeit UTC, Höhe ft, [TAS kt]
GPX-Datei senden, Beschriftung: /flug 12.10. 08:00 4500 105
/liste                 geplante Flüge
/check 20261012        sofort prüfen (Name oder Anfang)
/loeschen 20261012     Flug löschen
/hilfe
```
Nur Nachrichten aus `TELEGRAM_CHAT_ID` werden angenommen.

**2. YAML-Datei** in `flights/` (auf GitHub: *Add file → Create new file*), siehe `flights/beispiel-loww-lowg.yaml`.
`departure: 2026-10-12T08:00Z` – ohne Zeitzone gilt UTC.

**3. GPX**: `.gpx` (z. B. Export aus SkyDemon, Garmin, autorouter) nach `flights/` hochladen und im YAML statt `route:` `gpx: datei.gpx` angeben. Verwendet wird die Route (`rtept`), sonst die Wegpunkte (`wpt`), sonst der Track (`trkpt`). Wegpunktnamen im ICAO-Format bekommen METAR/TAF.

## Weitere Prüfungen

- **Steig- und Sinkflug:** Das Höhenprofil startet auf Platzhöhe. Gestiegen wird mit `climb_fpm`; die Steigrate nimmt mit der Dichtehöhe (Höhe und Temperatur aus ICON) bis `ceiling_ft` ab. Zwischen Abschnitten mit verschiedener Höhe wird gestiegen bzw. gesunken, der Sinkflug endet in Platzrundenhöhe über dem Ziel. Gemeldet werden, wo die Reiseflughöhe erreicht wird (TOC) und wo der Sinkflug beginnt (TOD), außerdem eine Warnung, wenn das Gelände auf Kurs nicht mit 500 ft Abstand überstiegen wird, samt nötiger Steigrate. Wolken-, Wind-, Vereisungs- und Luftraumprüfung rechnen mit diesem Profil. Leistungsdaten im Flugplan unter `performance:` (siehe Beispiel); Telegram-Flüge nutzen die Standardwerte (500 fpm, 75 % TAS, 14000 ft).
- **Windkorrigierte Zeiten:** Groundspeed je Stützpunkt aus dem Wind auf Planhöhe (mit Vorhaltewinkel). Damit werden Ankunftszeiten und Wetterstunden berechnet.
- **Gelände im Korridor ±5 NM** (Copernicus-DEM 90 m, ~1,25-NM-Raster) als Untergrenze der möglichen Flughöhen. Wird pro Route in `state/terrain/` zwischengespeichert.
- **ICON in der Wahrscheinlichkeit:** Wahrscheinlichkeit = (1 − w) · Ensemble + w · ICON. Dabei ist w = 35 % bis 48 h vor Abflug (ICON-D2) und 20 % danach. ICON gilt als kritisch, wenn eine BKN/OVC-Schicht unter den Ceiling-Minima liegt oder der Flugweg in bzw. bis zu 500 ft an eine Schicht heranführt. So wird Hochnebel erkannt, den die Ensemble-Schätzung übersieht.
- **Grafik:** Die Ampel oben (grün/gelb/rot) zeigt die kritischen Bereiche mit Grund. Der Flugweg ist in Ampelfarben gezeichnet, die Wolken als Blöcke je Stützpunkt (Grautöne nach Bedeckung), darunter steht eine Lesehilfe.
- **Wolkenschichten aus ICON-Druckflächen** (Basis/Top). Sie erkennen Hochnebel und Inversionen und begrenzen das Höhenband zusätzlich zur Ensemble-Schätzung. „On top“ wird erwähnt, wenn die Tops unter 9000 ft liegen; dabei VFR-on-top-Regeln und den Rückweg beachten.
- **Lee-/Rotorturbulenz**, wenn der Wind auf Kammhöhe über Gebirge ≥ 25 kt beträgt. **Föhnindex** aus der Druckdifferenz Bozen − Innsbruck (±4 hPa) für Alpenstrecken.
- **Lufträume** (openAIP): R/D/P, CTR, TMA, TMZ/RMZ, TRA/TSA und ATZ, durch die die Strecke führt oder die bis 1000 ft über bzw. unter der Planhöhe liegen. Abgefragt werden die Länder der Flugplätze in der Route.
- **NOTAMs** (autorouter): für Flugplätze und FIRs, gültig im Flugzeitraum, nahe der Strecke und unterhalb Planhöhe + 2000 ft.
- **Tageslicht:** Warnung bei Landung nach dem Ende der bürgerlichen Dämmerung (oder weniger als 30 min davor) und bei Abflug vor deren Beginn.

## Planung und Nachkontrolle

- **Alternative Abflugzeiten:** Die Wahrscheinlichkeit wird auch für −3 h bis +3 h und für morgen zur gleichen Zeit berechnet. Ist eine Alternative mindestens 10 Prozentpunkte besser, gibt es einen Hinweis „💡 besser: …“.
- **Ausweichplatz:** `alternate: LOWI` im Flugplan (Telegram: `altn=LOWI`). Dazu kommen METAR/TAF sowie der Anteil kritischer Ensemble-Member zur voraussichtlichen Ankunftszeit am Ausweichplatz.
- **Plätze ohne METAR** (z. B. LOIH, EDNL): Es wird die nächstgelegene Station bis 40 NM angezeigt.
- **Flugzeugprofile:** `aircraft/<Kennzeichen>.yaml` (siehe `aircraft/BEISPIEL.yaml`) mit TAS, Leistung und optional Minima. Im Flugplan steht dann `aircraft: D-EABC`; Werte im Flugplan haben Vorrang. Telegram: `/flugzeug D-EABC 105 500 [Steig-TAS] [Gipfelhöhe]`, `/flugzeuge`, `/flug … D-EABC`.
- **Routendateien:** GPX, Garmin `.fpl` und SkyDemon `.flightplan` (`route_file:` im Flugplan, Upload auf der Webseite oder per Telegram). Start und Ziel ohne ICAO-Namen werden dem nächsten Flugplatz (bis 3 NM) zugeordnet. Hinweis: Der SkyDemon-Import ist nur mit nachgebauten Dateien getestet.
- **Nachkontrolle / Trefferquote:** 1 h nach der Ankunft werden die METARs von Start und Ziel (oder der Ersatzstation) zu den Flugzeiten mit deinen Minima verglichen und das Ergebnis per Telegram/E-Mail gemeldet. Gesammelt wird in `state/verification.json`; die Webseite zeigt Trefferquote und Brier-Score.

## Hosting: kostenlos mit GitHub Actions

Einen eigenen Server brauchst du nicht. Der Workflow `.github/workflows/check.yml` läuft **alle 10 Minuten auf GitHub** (Telegram-Befehle; alle Flüge werden stündlich geprüft) und speichert den letzten Stand in `state/`.
- Öffentliche Repos: Actions sind unbegrenzt kostenlos.
- Private Repos: 2000 Freiminuten pro Monat. Alle 10 Minuten sind das ca. 4300 Minuten pro Monat, also mehr als die Freiminuten. Für ein privates Repo deshalb im Workflow den Cron auf stündlich (`17 * * * *`) stellen.
- ⚠️ In einem **öffentlichen** Repo sind deine Flugpläne öffentlich sichtbar. Für den Dauerbetrieb solltest du das Repo auf privat stellen.
- Hinweis: Bei Repos ohne Aktivität deaktiviert GitHub geplante Workflows nach 60 Tagen. Die State-Commits zählen als Aktivität.

**Wichtig – zuverlässiger Takt:** GitHub führt geplante Workflows bei Last oft stark verzögert oder gar nicht aus (beobachtet: 2–8 statt 144 Läufe pro Tag). Deshalb besser extern auslösen, z. B. kostenlos mit [cron-job.org](https://cron-job.org):
1. Auf GitHub unter *Settings → Developer settings → Fine-grained tokens* ein Token anlegen: nur dieses Repo, Berechtigung **Contents: Read and write** (für `repository_dispatch` nötig).
2. Bei cron-job.org einen Job alle 10 Minuten anlegen:
   - `POST https://api.github.com/repos/<user>/vfrcheck/dispatches`
   - Header: `Authorization: Bearer <TOKEN>`, `Accept: application/vnd.github+json`
   - Body: `{"event_type":"tick"}`
Der GitHub-Cron bleibt als Reserve aktiv.

Alternativen: ein PHP-Webhoster scheidet meist aus, weil dort kein Python und kein Cron verfügbar ist. Möglich sind Oracle Cloud Always Free (VM), PythonAnywhere (ein täglicher Task kostenlos) oder ein Raspberry Pi zu Hause.

## Einrichtung

Einstellungen → *Secrets and variables* → *Actions* → *New repository secret*. Setze nur die Secrets, die du brauchst:

| Kanal | Secrets |
|---|---|
| **Telegram** (empfohlen) | `TELEGRAM_BOT_TOKEN` (von [@BotFather](https://t.me/BotFather)), `TELEGRAM_CHAT_ID` (dem Bot schreiben, dann `https://api.telegram.org/bot<TOKEN>/getUpdates` öffnen) |
| **E-Mail** | `SMTP_HOST` (GMX: `mail.gmx.net`), `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, `MAIL_TO`, optional `MAIL_FROM`. Bei GMX muss im Webmailer der Zugriff über POP3/IMAP/SMTP aktiviert sein. |
| **Signal** | über [CallMeBot](https://www.callmebot.com/blog/free-api-signal-send-messages/): `CALLMEBOT_PHONE`, `CALLMEBOT_APIKEY`. Kostenlos, sendet aber nur an die eigene Nummer und ohne Bild. Eine offizielle Signal-Bot-API gibt es nicht; die Alternative wäre `signal-cli` mit eigener Nummer auf einem eigenen Server. |
| **Webcams** | `WINDY_API_KEY`: kostenloser Key auf [api.windy.com/keys](https://api.windy.com/keys) (Webcams API). Links zu Webcams bei Start, Ziel, Ausweichplatz und alle ~30 NM entlang der Strecke |
| **Lufträume** | `OPENAIP_API_KEY`: kostenloses Konto auf [openaip.net](https://www.openaip.net), dann Profil → API Clients → Key anlegen |
| **NOTAMs** | nutzen den autorouter-Zugang (`AUTOROUTER_USER`/`AUTOROUTER_PASS`); der API-Zugriff muss bei autorouter per Support-Anfrage freigeschaltet werden |
| **GRAMET** | `AUTOROUTER_USER`, `AUTOROUTER_PASS` (kostenloser Account auf [autorouter.aero](https://www.autorouter.aero)) |

Danach unter *Actions* → *vfrcheck* → *Run workflow* (mit „Benachrichtigung erzwingen“) testen.

## Lokal ausführen

```bash
pip install -r requirements.txt
python -m vfrcheck --dry-run                   # alle Flüge, nichts senden
python -m vfrcheck flights/mein-flug.yaml --force-notify
```

## Grenzen und Haftung

- Die Wolkenbasis wird aus der Taupunktdifferenz am Boden geschätzt. Inversionen und Hochnebel können dabei unterschätzt werden. Die ETA wird ohne Windkorrektur berechnet.
- Sichtwerte liefert nicht jedes Ensemble-Modell. Fehlende Werte werden ignoriert.
- Die Geländeuntergrenze nutzt nur die Modellhöhe am Stützpunkt (alle 5 NM, ICON-D2-Gitter ~2 km), nicht den höchsten Grat dazwischen – MEF/Karte prüfen!
- „Über den Wolken“ (VFR on top) wird nicht vorgeschlagen.
- GitHub-Cron kann sich verspäten; die Briefing-Mail kommt daher ca. 1–3 h vor Abflug.
- Luftraum, NOTAMs und Gebirgspässe (Talnebel!) werden **nicht** geprüft.
- **Das Tool ersetzt kein offizielles Briefing** (z. B. Austro Control / DWD / flugwetter.de). Die Entscheidung trifft der PIC.

## Ideen

GPX- und SkyDemon-Import, Windkorrektur der ETA, Gelände entlang der Strecke (MEF), Höhenwind und Vereisung aus Druckniveaus, Webseite mit Verlauf.
