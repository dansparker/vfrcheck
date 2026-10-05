# vfrcheck

Prüft anhand eines Flugplans, wie wahrscheinlich ein Flug unter **VFR-Bedingungen** durchführbar ist, zeigt kritische Stellen entlang der Strecke und meldet starke Änderungen per **Telegram, E-Mail oder Signal**.

## So funktioniert es

1. Flugplan in `flights/*.yaml` (Wegpunkte als ICAO-Code oder lat/lon, Abflugzeit, TAS, Reiseflughöhe, persönliche Minima).
2. Die Strecke wird alle ~20 NM in Stützpunkte zerlegt, jeder bekommt eine ETA.
3. Für jeden Punkt werden **Ensemble-Vorhersagen** von [Open-Meteo](https://open-meteo.com) abgefragt (kostenlos):
   - **ICON-EPS** (DWD, ~40 Member) und **ECMWF-ENS** (~51 Member), zusammen rund 90 Szenarien
   - Sicht, tiefe Bewölkung, Wolkenbasis (geschätzt über die Taupunktdifferenz), Böen, Niederschlag, CAPE
4. **Wahrscheinlichkeit** = Anteil der Member, in denen *jeder* Punkt der Strecke die Minima erfüllt.
   Kategorien: ≥80 % GO, ≥50 % MARGINAL, darunter NO-GO.
5. **Problemstellen**: pro Punkt der Anteil kritischer Member samt Grund (z. B. „Semmering: 35 % kritisch, Ceiling < 1500 ft“).
6. **METAR/TAF** der Flugplätze (aviationweather.gov) und optional ein **GRAMET**-Querschnitt (autorouter) werden mitgeschickt.
7. Benachrichtigt wird bei der ersten Bewertung, bei einer Änderung um ≥ `delta_pct` Prozentpunkte oder bei einem Wechsel der Kategorie.

## Hosting: kostenlos mit GitHub Actions

Einen eigenen Server brauchst du nicht. Der Workflow `.github/workflows/check.yml` läuft **stündlich auf GitHub** und speichert den letzten Stand in `state/`.
- Öffentliche Repos: Actions sind unbegrenzt kostenlos.
- Private Repos: 2000 Freiminuten pro Monat. Ein Lauf dauert unter einer Minute, stündlich sind das ca. 720 Minuten pro Monat, also reicht das.
- ⚠️ In einem **öffentlichen** Repo sind deine Flugpläne öffentlich sichtbar. Für den Dauerbetrieb solltest du das Repo auf privat stellen.
- Hinweis: Bei Repos ohne Aktivität deaktiviert GitHub geplante Workflows nach 60 Tagen. Die State-Commits zählen als Aktivität.

Alternativen: ein PHP-Webhoster scheidet meist aus, weil dort kein Python und kein Cron verfügbar ist. Möglich sind Oracle Cloud Always Free (VM), PythonAnywhere (ein täglicher Task kostenlos) oder ein Raspberry Pi zu Hause.

## Einrichtung

Einstellungen → *Secrets and variables* → *Actions* → *New repository secret*. Setze nur die Secrets, die du brauchst:

| Kanal | Secrets |
|---|---|
| **Telegram** (empfohlen) | `TELEGRAM_BOT_TOKEN` (von [@BotFather](https://t.me/BotFather)), `TELEGRAM_CHAT_ID` (dem Bot schreiben, dann `https://api.telegram.org/bot<TOKEN>/getUpdates` öffnen) |
| **E-Mail** | `SMTP_HOST` (GMX: `mail.gmx.net`), `SMTP_PORT` (587), `SMTP_USER`, `SMTP_PASS`, `MAIL_TO`, optional `MAIL_FROM`. Bei GMX muss im Webmailer der Zugriff über POP3/IMAP/SMTP aktiviert sein. |
| **Signal** | über [CallMeBot](https://www.callmebot.com/blog/free-api-signal-send-messages/): `CALLMEBOT_PHONE`, `CALLMEBOT_APIKEY`. Kostenlos, sendet aber nur an die eigene Nummer und ohne Bild. Eine offizielle Signal-Bot-API gibt es nicht; die Alternative wäre `signal-cli` mit eigener Nummer auf einem eigenen Server. |
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
- Luftraum, NOTAMs und Gebirgspässe (Talnebel!) werden **nicht** geprüft.
- **Das Tool ersetzt kein offizielles Briefing** (z. B. Austro Control / DWD / flugwetter.de). Die Entscheidung trifft der PIC.

## Ideen

GPX- und SkyDemon-Import, Windkorrektur der ETA, Gelände entlang der Strecke (MEF), Höhenwind und Vereisung aus Druckniveaus, Webseite mit Verlauf.
