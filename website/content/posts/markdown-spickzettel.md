---
title: "Markdown-Spickzettel"
date: 2026-09-17
---

Alles, was man für einen Beitrag auf dieser Seite braucht — mehr Syntax ist
selten nötig.

## Text

```markdown
*kursiv*, **fett**, `Code`
[Link](https://gohugo.io/)
```

Ergibt: *kursiv*, **fett**, `Code`, [Link](https://gohugo.io/).

## Überschriften und Listen

```markdown
## Zweite Ebene
### Dritte Ebene

- Punkt
- Noch ein Punkt
  - eingerückt

1. Erstens
2. Zweitens
```

## Zitat und Codeblock

> Ein Zitat beginnt mit einem Größer-als-Zeichen.

Codeblöcke stehen zwischen drei Backticks, die Sprache dahinter sorgt für
Hervorhebung:

````markdown
```bash
hugo server
```
````

## Frontmatter

Ganz oben in jeder Datei, zwischen zwei Zeilen mit `---`:

```yaml
---
title: "Titel des Beitrags"
date: 2026-09-17
draft: true
---
```

`draft: true` hält den Beitrag aus dem Build heraus. Zum Veröffentlichen die
Zeile löschen — im Entwicklungsserver sieht man Entwürfe mit
`hugo server --buildDrafts` trotzdem.
