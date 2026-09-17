# CMS

Kleine Weboberfläche zum Pflegen der Hugo-Inhalte. Sie bearbeitet die
Markdown-Dateien in den Unterordnern von `../website/content/` — jede
Sektion ist auf genau einen Ordner verknüpft.

## Start

```bash
python3 cms/server.py          # http://127.0.0.1:8080
```

Nur die Python-Standardbibliothek, nichts zu installieren.
Optionen: `--port`, `--host`, `--content <ordner>`.

Zum Gegenlesen parallel den Hugo-Server laufen lassen:

```bash
cd website && hugo server --buildDrafts
```

## Sektionen

Die Liste steht fest im Code, oben in `server.py`:

```python
SECTIONS = [
    {"id": "posts", "name": "Blog", "dir": "posts"},
    {"id": "veranstaltungen", "name": "Veranstaltungen", "dir": "veranstaltungen"},
]
```

`dir` ist der Unterordner in `website/content/`. Eine weitere Sektion
braucht zwei Schritte:

1. Zeile in `SECTIONS` ergänzen.
2. Ordner in Hugo anlegen, mit `_index.md` für die Übersichtsseite:

   ```bash
   mkdir website/content/projekte
   printf -- '---\ntitle: "Projekte"\n---\n' > website/content/projekte/_index.md
   ```

   Für einen Menüpunkt zusätzlich einen `[[menu.main]]`-Block in
   `website/hugo.toml` eintragen.

Fehlt der Ordner, erscheint die Sektion in der Auswahl als
„(Ordner fehlt)" und ist nicht anwählbar — der Server startet trotzdem.

## Bedienung

- Oben links die Sektion wählen, darunter die Einträge; Klick öffnet einen.
- **+ Neu** legt einen Eintrag in der gewählten Sektion an; der Dateiname
  entsteht aus dem Titel (`Herbstlesung im Hof` → `herbstlesung-im-hof.md`),
  bei Namensgleichheit mit angehängter Nummer.
- **Vorschau** blendet die gerenderte Ansicht neben den Editor.
- **Speichern** oder `Strg+S` schreibt die Datei.
- **Entwurf** setzt `draft: true` — solche Einträge landen nicht im Build.
- Unten links schaltet **Darstellung** zwischen System, Hell und Dunkel;
  die Wahl bleibt im Browser gespeichert.

## Grenzen

- Kein Löschen und kein Umbenennen: dafür die Datei direkt im Ordner anfassen.
- `_index.md` der Sektionen wird nicht angetastet.
- Keine Anmeldung. Der Server bindet auf `127.0.0.1` und gehört nicht
  ins offene Netz.
- Unbekannte Frontmatter-Felder bleiben beim Speichern erhalten, wandern
  aber ans Ende des Blocks.
- Die Vorschau ist eine Annäherung (rund 80 Zeilen JavaScript), nicht
  Hugos Markdown-Renderer. Verbindlich ist, was `hugo server` zeigt.
