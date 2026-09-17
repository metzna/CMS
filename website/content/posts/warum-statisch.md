---
title: "Warum eine statische Seite?"
date: 2026-09-16
---

Ein klassisches CMS braucht eine Datenbank, einen PHP-Prozess und regelmäßige
Updates. Für eine Seite mit ein paar Texten ist das viel Apparat für wenig Inhalt.

Hugo baut aus Markdown-Dateien fertiges HTML. Was am Ende auf dem Server liegt,
sind nur Dateien:

```bash
hugo
ls public/
```

Das hat drei angenehme Folgen:

- **Schnell.** Kein Rendern pro Aufruf, der Webserver liefert nur aus.
- **Robust.** Keine Datenbank, die kaputtgehen kann, keine Login-Maske,
  die jemand aufbricht.
- **Portabel.** Der Ordner `public/` läuft auf jedem Webspace, bei GitHub Pages
  oder Netlify genauso wie auf einem eigenen nginx.

Der Preis: alles, was dynamisch sein soll — Kommentare, Suche, Formulare —
muss man extern einbinden. Für ein Blog mit ein paar Beiträgen im Monat ist das
ein guter Tausch.
