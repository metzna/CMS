# CMS

Kleine Weboberfläche zum Pflegen der Hugo-Inhalte. Sie bearbeitet die
Markdown-Dateien in den Unterordnern von `../website/content/` — jede
Sektion ist auf genau einen Ordner verknüpft.

## Start

```bash
python3 cms/server.py          # http://127.0.0.1:8080
```

Nur die Python-Standardbibliothek, nichts zu installieren — ausser Hugo
selbst, das nach jedem Schreiben laeuft.
Optionen: `--port`, `--host`, `--content <ordner>`, `--config <datei>`,
`--hugo <programm>`, `--kein-build`.

Ein Prozess bedient genau eine Seite. Fuer eine zweite Seite einen zweiten
Prozess starten:

```bash
python3 cms/server.py --port 8081 --content /srv/kunde-b/website/content
```

## Erzeugen der Seite

Mehrere Anfragen werden nebenlaeufig bedient. Schreiben, Bauen und
Committen laufen dabei unter einer gemeinsamen Sperre: zwei Hugo-Laeufe
raeumten einander sonst `public/` weg, zwei `git commit` stritten um
`index.lock`, und zwei gleichzeitig angelegte Beitraege koennten denselben
Dateinamen bekommen. Lesende Anfragen laufen frei weiter, auch waehrend
gebaut wird. Geschrieben wird ueber eine Zwischendatei und `os.replace`,
damit ein Leser nie eine halb geschriebene Datei erwischt.

Nach jedem Speichern und Loeschen laeuft `hugo` im Ordner ueber `content/`
und schreibt `public/` neu — die Aenderung ist also sofort auf der Seite. Fuer
diese Seite dauert das rund 60 ms, darum passiert es synchron: die Antwort
sagt schon, ob es geklappt hat, und die Oberflaeche zeigt es neben
„Speichern“ als Ring und danach als Haken.

Waehrend Speichern oder Loeschen laeuft, sind die Knoepfe gesperrt — auch
`Strg+S`, das sonst am Knopf vorbeiginge. Ohne das legte ein Doppelklick auf
einen neuen Eintrag ihn zweimal an, weil der Server bei Namensgleichheit eine
Nummer anhaengt.

Scheitert der Build, ist der Text trotzdem gespeichert. Die Statuszeile sagt
dann, dass die Seite nicht neu erzeugt wurde, und nennt Hugos Meldung; die
volle Ausgabe steht im Protokoll des Servers.

Gebaut wird mit `--cleanDestinationDir`, damit ein geloeschter Beitrag auch
aus `public/` verschwindet und nicht fuer Besucher erreichbar bleibt.

### Zeitzone: unbedingt setzen

In `hugo.toml` der Seite muss `timeZone` stehen, z. B.:

```toml
timeZone = 'Europe/Berlin'
```

Ohne das liest Hugo ein `date: 2026-09-19` als **UTC**-Mitternacht. Zwischen
00:00 und 02:00 mitteleuropaeischer Sommerzeit liegt die noch in der
Zukunft, und Hugo laesst den Beitrag als terminiert aus dem Build heraus --
ohne Fehler, mit Exit-Code 0. Das CMS meldet dann "gespeichert und erzeugt",
und der Beitrag steht trotzdem nicht auf der Seite. Nachts geschriebene
Beitraege erscheinen so erst Stunden spaeter.

## Versionsgeschichte und Sicherung

Mit `--git` wird jede Aenderung als Commit festgehalten — angelegt,
bearbeitet, geloescht, jeweils mit dem Titel in der Nachricht. Damit ist
alles umkehrbar, auch das Loeschen, das sonst keinen Papierkorb hat. Das
Repo wird ueber `git rev-parse --show-toplevel` gefunden, muss also nur
irgendwo oberhalb der Inhalte liegen. Name und E-Mail im Commit setzen
`--git-name` und `--git-email`; das Repo braucht dafuer keine eigene
Konfiguration.

`--push` schiebt zusaetzlich nach aussen, etwa in ein privates Repo als
Sicherung. Das laeuft **nebenher**, nicht im Request: ein Push ins Netz kann
Sekunden dauern oder haengen, und so lange darf ein Kunde nicht warten.
Scheitert er, steht die Meldung in der naechsten Antwort — spaetestens beim
naechsten Speichern sieht der Kunde also, dass die Sicherung klemmt. Geht er
wieder durch, verschwindet die Klage von selbst.

Die drei Schritte sind getrennt zu betrachten: **gespeichert ist immer**,
auch wenn der Build scheitert; **versioniert ist meistens**, auch wenn der
Push scheitert. Die Statuszeile nennt genau die Stufe, die nicht geklappt
hat.

```bash
python3 cms/server.py --git --push --git-name "CMS" --git-email "cms@kunde-a.de"
```

## Beim Entwickeln

Wer ohnehin `hugo server` laufen laesst, schaltet den Build
mit `--kein-build` ab:

```bash
cd website && hugo server --buildDrafts
python3 cms/server.py --kein-build
```

## Konfiguration

Was bearbeitet werden darf, steht in `cms.toml` neben dem `content`-Ordner —
also im Repo der Seite, nicht beim CMS. Jede Kundenseite bringt ihre eigene
mit; mit `--config` liegt sie auch woanders.

```toml
title = "Meine Seite"     # steht im Browser-Tab der Oberfläche

[[sections]]
dir = "posts"             # Unterordner in content/ (Pflicht)
name = "Blog"             # Beschriftung in der Auswahl (Vorgabe: dir)
# id = "posts"            # kommt in der URL vor (Vorgabe: dir)
```

Ist die Datei fehlerhaft, startet der Server nicht und nennt Datei, Stelle
und Grund. Das ist Absicht: eine kaputte Konfiguration soll beim Hochfahren
auffallen, nicht beim ersten Klick.

### Weitere Sektion

1. `[[sections]]`-Block in `cms.toml` ergänzen.
2. Ordner in Hugo anlegen, mit `_index.md` für die Übersichtsseite:

   ```bash
   mkdir website/content/projekte
   printf -- '---\ntitle: "Projekte"\n---\n' > website/content/projekte/_index.md
   ```

   Für einen Menüpunkt zusätzlich einen `[[menu.main]]`-Block in
   `website/hugo.toml` eintragen.
3. Server neu starten — `cms.toml` wird nur beim Start gelesen.

Fehlt der Ordner, erscheint die Sektion in der Auswahl als
„(Ordner fehlt)" und ist nicht anwählbar — der Server startet trotzdem.

## Bedienung

- Oben links die Sektion wählen, darunter die Einträge; Klick öffnet einen.
- **+ Neu** legt einen Eintrag in der gewählten Sektion an; der Dateiname
  entsteht aus dem Titel (`Herbstlesung im Hof` → `herbstlesung-im-hof.md`),
  bei Namensgleichheit mit angehängter Nummer.
- **Vorschau** blendet die gerenderte Ansicht neben den Editor.
- **Speichern** oder `Strg+S` schreibt die Datei und erzeugt die Seite neu.
- **Löschen** entfernt den offenen Eintrag nach einer Rückfrage. Die Datei
  ist danach weg — kein Papierkorb. Bei einem noch nicht gespeicherten
  Eintrag erscheint der Knopf nicht.
- **Entwurf** setzt `draft: true` — solche Einträge landen nicht im Build.
- Unten links schaltet **Darstellung** zwischen System, Hell und Dunkel;
  die Wahl bleibt im Browser gespeichert.

## Grenzen

- Kein Umbenennen: dafür die Datei direkt im Ordner anfassen. Gelöschtes
  holt nur `git checkout` zurück, sofern es eingecheckt war.
- `_index.md` der Sektionen wird nicht angetastet.
- Keine Anmeldung im Server selbst. Er bindet auf `127.0.0.1` und gehört
  nicht ins offene Netz. Für den Betrieb mit Kundenzugängen steht die
  Anmeldung davor: siehe `betrieb/README.md` und `cms/auth.py`.
- Im Titel sind `"` und `\` nicht erlaubt; der Titel steht im Frontmatter als
  `title: "..."`, wo `"` den Wert abbricht und `\` eine Escape-Sequenz
  beginnt — beides macht die Datei fuer Hugo unlesbar. Das Eingabefeld meldet
  es sofort, der Server weist es mit 400 ab. Typografische
  Anfuehrungszeichen (`„ “`, `» «`) gehen.
- Unbekannte Frontmatter-Felder bleiben beim Speichern erhalten, wandern
  aber ans Ende des Blocks.
- Die Vorschau ist eine Annäherung (rund 80 Zeilen JavaScript), nicht
  Hugos Markdown-Renderer. Verbindlich ist, was `hugo server` zeigt.
