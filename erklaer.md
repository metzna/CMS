# So funktioniert das CMS – für Einsteiger

Dieses Dokument erklärt das ganze System ohne Vorwissen: was die einzelnen
Teile tun, was beim Speichern passiert, warum die Anmeldung sicher ist und
wie ein neuer Kunde eingerichtet wird. Fachbegriffe stehen am Ende im
[Wörterbuch](#kleines-wörterbuch).

---

## Worum es geht

Kleine Firmen und Vereine bekommen eine Website. Ihre Texte (Blogbeiträge,
Veranstaltungen) sollen sie **selbst pflegen** können, ohne etwas von
Technik zu verstehen. Dafür bekommen sie eine einfache Weboberfläche, das
CMS. Alle Kundenseiten laufen zusammen auf **einem** gemieteten Server (VPS),
aber sauber voneinander getrennt.

---

## Das große Bild

```
                         ┌──────────────────── VPS ─────────────────────────┐
                         │                                                  │
 Besucher  ─ kunde-a.de ─┼─▶ Caddy ─────────────────▶ fertige Seite         │
                         │                              (Ordner public/)    │
                         │                                   ▲              │
 Kunde ─ cms.kunde-a.de ─┼─▶ Caddy ─▶ Anmeldung ─▶ CMS ─▶ Hugo baut sie neu │
                         │            (auth.py)   (server.py)               │
                         └──────────────────────────────────────────────────┘
```

Es gibt zwei Adressen je Kunde:

- **`kunde-a.de`** ist die öffentliche Website. Die sieht jeder.
- **`cms.kunde-a.de`** ist die Pflege-Oberfläche. Die sieht nur, wer sich
  angemeldet hat.

---

## Die Bausteine

| Baustein | Was er tut | Vergleich |
|---|---|---|
| **Hugo** | Macht aus Textdateien fertige Webseiten | Druckerei: nimmt Manuskripte, liefert fertige Seiten |
| **CMS** (`cms/server.py`) | Die Oberfläche, in der der Kunde Texte schreibt | Schreibtisch |
| **Anmeldung** (`cms/auth.py`) | Prüft Benutzername und Passwort | Kontrolle am Eingang |
| **Caddy** | Nimmt alle Anfragen aus dem Internet entgegen, sorgt für HTTPS | Türsteher, der als Einziger an der Tür steht |
| **Docker** | Packt die Programme je Kunde in abgeschlossene Kisten | Getrennte Büros im selben Gebäude |
| **Git** | Merkt sich jede Änderung, damit nichts verloren geht | Tagebuch, in dem jede Änderung steht |

### Warum Hugo und keine „normale" Website?

Hugo erzeugt eine **statische Seite**: lauter fertige HTML-Dateien, die nur
noch ausgeliefert werden. Dahinter läuft kein Programm und keine Datenbank.
Das macht die Seite sehr schnell und kaum angreifbar, denn wo nichts läuft,
kann auch nichts gehackt werden. Der Preis: Nach jeder Änderung muss Hugo die
Seite neu bauen. Das übernimmt das CMS automatisch.

---

## Was passiert, wenn der Kunde auf „Speichern" klickt

1. **Die Knöpfe werden gesperrt** und neben „Speichern" dreht sich ein Ring.
   So kann ein Doppelklick nichts doppelt anlegen.
2. Der Browser schickt den Text an das CMS.
3. Das CMS **schreibt die Datei** in den Ordner `content/`. Erst in eine
   Zwischendatei, dann wird sie umbenannt. So sieht nie jemand eine halb
   geschriebene Datei.
4. **Hugo baut die Seite neu.** Bei einer kleinen Seite dauert das unter
   einer Zehntelsekunde. Danach ist die Änderung auf `kunde-a.de` sichtbar.
5. **Git hält die Änderung fest** (ein „Commit"). Damit lässt sich später
   jede Version wiederherstellen, auch gelöschte Beiträge.
6. Optional: Git **schickt die Änderung nebenher an eine Sicherungskopie**
   außer Haus (ein „Push"). Das läuft im Hintergrund, der Kunde wartet nicht
   darauf.
7. Der Ring wird zum **Haken**, oder bei einem Problem zum **Kreuz** mit
   einer Erklärung in der Statuszeile.

### Bilder

Über den Knopf **Bild** oder durch Hineinziehen ins Textfeld. Das CMS rechnet
jedes Bild sofort in das platzsparende Format **WebP** um und verkleinert es
auf höchstens 2000 Pixel. Aus einem Handyfoto von mehreren Megabyte werden so
etwa 200 Kilobyte, ohne sichtbaren Qualitätsverlust. Das hat drei Vorteile:

- Die Seite lädt für Besucher deutlich schneller.
- Nur die kleine Fassung landet in der Versionsgeschichte, die sonst mit
  jedem Foto dauerhaft anwachsen würde.
- Beim Umrechnen wird die Datei neu aufgebaut. Was kein echtes Bild ist,
  fliegt dabei raus, und die **EXIF-Daten verschwinden** — in Handyfotos
  steht dort oft der genaue Aufnahmeort.

Die wichtigste Zusage: **Gespeichert ist immer.** Selbst wenn ein späterer
Schritt scheitert, ist der Text nicht verloren. Die Statuszeile sagt genau,
welcher Schritt nicht geklappt hat:

| Statuszeile | Bedeutung |
|---|---|
| `Gespeichert: x.md` | Alles hat geklappt |
| `Gespeichert, aber die Seite wurde nicht neu erzeugt: …` | Text ist da, aber Hugo hatte ein Problem |
| `Gespeichert, aber nicht in die Versionsgeschichte aufgenommen: …` | Text ist da und auf der Seite, aber Git hatte ein Problem |
| `Gespeichert: x.md — die Sicherung klemmt: …` | Alles da, nur die Kopie außer Haus hat nicht geklappt |

Es kann immer nur **ein Schreibvorgang gleichzeitig** laufen. Zwei Hugo-Läufe
oder zwei Git-Commits zur selben Zeit würden sich gegenseitig stören. Lesen,
also Beiträge ansehen oder die Liste laden, geht dabei jederzeit.

---

## Die Anmeldung

### Warum braucht es überhaupt ein Cookie?

Das Internet vergisst zwischen zwei Anfragen alles. Eine einzige Seite im
CMS besteht aber schon aus sechs Anfragen, und jeder Klick ist eine weitere.
Ohne Gedächtnis müsste der Kunde bei jeder davon sein Passwort eingeben.

Die Lösung ist das **Cookie**, vergleichbar mit dem **Einlassbändchen auf
einem Festival**: Einmal am Eingang das Ticket zeigen, dann bekommt man ein
Bändchen, und alle weiteren Kontrollen schauen nur noch aufs Bändchen.

Unser Bändchen ist **fälschungssicher**: Es trägt eine Unterschrift
(„Signatur"), die nur mit einem geheimen Schlüssel (`auth.key`) erzeugt
werden kann. Ein selbst gebasteltes Bändchen fällt sofort auf.

Außerdem gilt:

- Das Bändchen gilt **nur für genau eine Adresse**, etwa `cms.kunde-a.de`.
  Der Browser zeigt es nirgendwo sonst vor.
- Es läuft nach **12 Stunden** ab und verschwindet, wenn der Browser
  geschlossen wird. Danach meldet sich der Kunde neu an.
- Skripte auf Webseiten können es **nicht auslesen** („HttpOnly").
- Es wird **nur verschlüsselt** übertragen („Secure").

### Wie wird das Passwort gespeichert?

Gar nicht, zumindest nicht selbst. Gespeichert wird ein **Hash**. Ein Hash
ist wie ein **Fleischwolf**: Aus einem Passwort wird eine Zeichenkette, aber
aus der Zeichenkette lässt sich das Passwort nicht zurückgewinnen, so wie
aus Hackfleisch keine Kuh mehr wird.

```toml
passwort = "scrypt$16384$8$1$l1Q5QpvEfjXMY0OdrEmXfQ==$MjL8T4Itg…"
```

Beim Anmelden wird das eingegebene Passwort durch denselben Fleischwolf
gedreht und das Ergebnis verglichen. Wer die Datei stiehlt, kann sich damit
**nicht** anmelden. Er müsste Passwörter raten und jedes einzeln durch den
Fleischwolf drehen, und das Verfahren (scrypt) ist absichtlich langsam
gebaut, damit das Raten teuer wird.

Dazu kommt ein **Salz**: eine Zufallszahl, die mit ins Passwort gemischt
wird. Dasselbe Passwort ergibt dadurch jedes Mal einen anderen Hash, und
vorberechnete Listen nützen einem Angreifer nichts.

### Brauchen wir eine Datenbank?

Nein. In einer Datenbank stünde genau derselbe Hash, die Sicherheit kommt
vom Hash, nicht vom Ort. Bei einem Zugang je Seite ist eine Datei sogar
besser: kein zusätzliches Programm, weniger Angriffsfläche, und die
Sicherung ist eine Kopie der Datei. Eine Datenbank lohnt sich erst, wenn
Kunden ihr Passwort selbst ändern oder mehrere Benutzer mit Rollen haben
sollen.

### Was sonst noch schützt

- **Benutzername und Passwort werden immer beide geprüft**, und die
  Fehlermeldung ist immer dieselbe („Benutzer oder Passwort stimmt nicht").
  Ein Angreifer erfährt also nicht, ob wenigstens der Name stimmte.
- **Bremse gegen Durchprobieren:** Nach 10 Fehlversuchen innerhalb von
  5 Minuten sperrt der Dienst diese Internetadresse für eine Weile.
- **Der Benutzername muss exakt stimmen.** `Meier` ist nicht `meier`. Am
  besten immer kleingeschrieben vergeben.

### Alle abmelden, sofort

Wird ein Laptop gestohlen, ist das Bändchen darauf noch bis zu 12 Stunden
gültig. Notausgang: die Datei `auth.key` des Kunden löschen und den
Anmeldedienst neu starten. Dann sind alle Bändchen dieser Seite ungültig.

---

## Warum das mit Caddy sicher ist

### Die einzige Tür

Das CMS und die Anmeldung lauschen nur auf der Adresse **`127.0.0.1`**. Das
heißt „dieser Rechner selbst". Aus dem Internet kommt man dort nicht heran.
Nach außen offen ist **nur Caddy**. Jede Anfrage muss also durch Caddy.

### Zwei Schubladen

Caddy sortiert jede Anfrage an `cms.kunde-a.de`:

1. **`/login` und `/abmelden`** gehen direkt an die Anmeldung. Die müssen
   offen sein, sonst käme niemand zum Formular. Die Anmeldung liefert aber
   nie Inhalte des CMS aus.
2. **Alles andere** hält Caddy erst an und fragt die Anmeldung: „Darf der
   rein?" (das heißt `forward_auth`). Nur bei „ja" geht die Anfrage weiter
   ans CMS. Bei „nein" wird der Besucher zum Login geschickt, und **das CMS
   bekommt die Anfrage nie zu sehen.**

Das CMS selbst prüft deshalb nichts. Alles, was bei ihm ankommt, wurde
bereits geprüft.

### Was ein Angreifer versuchen kann

| Versuch | Warum er scheitert |
|---|---|
| Direkt zum CMS, an Caddy vorbei | Das CMS ist aus dem Internet nicht erreichbar |
| Ohne Anmeldung eine CMS-Seite aufrufen | Caddy fragt nach, Antwort „nein", Umleitung zum Login |
| Ein Bändchen fälschen | Die Unterschrift passt nicht |
| Das Bändchen von Kunde A bei Kunde B benutzen | Der Browser zeigt es dort gar nicht vor, und im Bändchen steht die Adresse von A |
| Passwort unterwegs mitlesen | Alles läuft verschlüsselt über HTTPS |
| Von einer fremden Webseite aus heimlich Beiträge anlegen | Das CMS nimmt nur Anfragen an, die fremde Webseiten nicht schicken dürfen |

### Wenn der Wächter ausfällt

Stürzt die Anmeldung ab, bekommt Caddy kein „ja" und lässt **niemanden**
durch. Ein kaputter Wächter heißt „Tür zu", nicht „Tür offen". Die
öffentliche Website läuft dabei weiter, denn die liefert Caddy direkt aus.

### Worauf es ankommt

Diese Sicherheit hängt an drei Dingen, siehe
[Stolperfallen](#die-wichtigsten-stolperfallen):
`127.0.0.1` vor den Ports, jeder Kunde als eigenes Compose-Projekt, und die
richtigen Ports im Caddy-Block.

---

## Warum jeder Kunde seine eigene Loginseite hat

Die Loginseite ist für alle Kunden **dieselbe**, gleicher Code und gleiches
Aussehen. Sie erscheint aber unter der Adresse des jeweiligen Kunden,
also `cms.kunde-a.de/login`.

Eine einzige zentrale Loginseite für alle wäre möglich, hätte aber einen
Haken: Ein Bändchen, das auf `login.meine-agentur.de` ausgegeben wird, zeigt
der Browser auf `cms.kunde-a.de` nicht vor. Man bräuchte eine aufwendige
Übergabe, und die zentrale Seite müsste alle Kunden und alle Passwörter
kennen. Wer sie knackt, hätte alle Kunden auf einmal. Mit getrennten
Anmeldungen hat jeder Kunde seinen eigenen Schlüssel und seine eigene
Passwortdatei.

Wer den Kunden trotzdem eine gemeinsame Adresse nennen will, kann eine
einfache **Wegweiser-Seite** bauen: Der Kunde tippt seine Domain ein und wird
zu seiner Loginseite geschickt. Dort liegen keine Passwörter, also gibt es
auch nichts zu stehlen.

---

## Docker in einfachen Worten

- Ein **Image** ist ein fertig gepacktes Paket: Python, Hugo, Git und unser
  CMS, alles in einer festen Version. Wir bauen es **einmal** für alle Kunden.
- Ein **Container** ist eine laufende Kopie davon. Jeder Kunde bekommt
  **zwei**: einen für das CMS, einen für die Anmeldung.
- Container sind **Wegwerfware**. Bei einem Update werden sie gelöscht und
  neu erzeugt. Deshalb liegen die Inhalte der Kunden **nicht im Container**,
  sondern in einem normalen Ordner auf dem VPS, der in den Container
  hineingereicht wird (ein „Volume"):

  ```
  /srv/kunden/kunde-a/
  ├── compose.yaml     ← beschreibt die zwei Container dieses Kunden
  ├── site/            ← die Website des Kunden (Git-Repo), wird zu /site im Container
  │   └── website/
  │       ├── hugo.toml
  │       ├── cms.toml
  │       ├── content/ ← die Texte
  │       └── public/  ← die fertige Seite, von Hugo erzeugt
  └── auth/            ← auth.toml und auth.key, wird zu /auth im Container
  ```

- Jeder Kunde ist ein eigenes **Compose-Projekt** mit eigenem internen Netz.
  Die Container von Kunde A können die von Kunde B nicht erreichen.
- Die **Hugo-Version steht im Image fest** (0.131.0, dieselbe wie auf dem
  Entwicklungsrechner). Ein Hugo-Update kann eine Kundenseite also nicht
  heimlich verändern, sondern erst, wenn das Image neu gebaut wird.

---

## Die Dateien im Projekt

| Datei | Wofür |
|---|---|
| `cms/server.py` | Das CMS: Oberfläche ausliefern, Dateien lesen und schreiben, Hugo und Git anstoßen |
| `cms/auth.py` | Die Anmeldung: Loginseite, Bändchen ausgeben und prüfen |
| `cms/static/` | Was der Browser anzeigt: `index.html`, `app.js`, `style.css` |
| `website/` | Die Test-Website. Jede Kundenseite ist genauso aufgebaut |
| `website/hugo.toml` | Einstellungen für Hugo (Titel, Menü, **Zeitzone**) |
| `website/cms.toml` | Welche Ordner der Kunde im CMS bearbeiten darf |
| `Dockerfile` | Bauanleitung für das Image |
| `betrieb/compose.yaml.beispiel` | Vorlage: die zwei Container eines Kunden |
| `betrieb/auth.toml.beispiel` | Vorlage: der Zugang eines Kunden |
| `betrieb/Caddyfile.beispiel` | Vorlage: der Caddy-Block eines Kunden |
| `cms/README.md`, `betrieb/README.md` | Die technische Dokumentation |

---

## Einen neuen Kunden einrichten

Einmalig vorab, im Projektordner auf dem VPS:

```bash
docker build -t cms:0.131.0 .
```

Dann für jeden neuen Kunden, hier `kunde-a`:

**1. Adressen einrichten.** Beim Domain-Anbieter des Kunden `kunde-a.de` und
`cms.kunde-a.de` auf die IP-Adresse des VPS zeigen lassen. Die Zertifikate
für HTTPS holt Caddy später von selbst.

**2. Ordner anlegen und Website hineinholen.** Die Website muss aufgebaut
sein wie `website/` in diesem Projekt, mit `hugo.toml`, `cms.toml` und
`content/`.

```bash
mkdir -p /srv/kunden/kunde-a/{site,auth}
git clone <repo-der-kundenseite> /srv/kunden/kunde-a/site
```

**3. Vorlagen kopieren.**

```bash
cp betrieb/compose.yaml.beispiel /srv/kunden/kunde-a/compose.yaml
cp betrieb/auth.toml.beispiel    /srv/kunden/kunde-a/auth/auth.toml
```

In `compose.yaml` anpassen: `name`, die zwei Ports (je Kunde hochzählen:
8081/9001, 8082/9002, …) und die E-Mail hinter `--git-email`.

**4. Passwort erzeugen und Zugang eintragen.**

```bash
docker run --rm -it cms:0.131.0 python3 /opt/cms/auth.py --passwort
```

Das Programm fragt das Passwort zweimal verdeckt ab und gibt eine Zeile
`passwort = "scrypt$…"` aus. In `auth/auth.toml` eintragen: `host`
(`cms.kunde-a.de`), `name` (erscheint als Überschrift der Loginseite),
`benutzer` und diese `passwort`-Zeile.

**5. Rechte setzen.**

```bash
chmod 600 /srv/kunden/kunde-a/auth/auth.toml
chown -R 1000:1000 /srv/kunden/kunde-a
```

**6. Starten.**

```bash
cd /srv/kunden/kunde-a && docker compose up -d
```

**7. Caddy Bescheid geben.** Die beiden Blöcke aus
`betrieb/Caddyfile.beispiel` übernehmen (öffentliche Seite und
CMS-Adresse), Namen und Ports anpassen, dann:

```bash
systemctl reload caddy
```

**8. Von außen prüfen.** `https://cms.kunde-a.de` muss zur Loginseite
führen, und `http://<VPS-IP>:8081` darf **nicht** antworten.

---

## Lokal ausprobieren

Auf dem eigenen Rechner, ohne Docker und ohne Anmeldung:

```bash
python3 cms/server.py            # dann http://127.0.0.1:8080 öffnen
```

Läuft nebenher schon `hugo server` zum Vorschauen, dann mit
`--kein-build` starten, damit nicht zwei Programme gleichzeitig bauen.

---

## Die wichtigsten Stolperfallen

1. **`127.0.0.1:` vor den Ports in `compose.yaml`.** Die wichtigste Zeile
   überhaupt. Ohne sie öffnet Docker den Port an der Firewall vorbei ins
   Internet, und jeder kommt ohne Anmeldung ins CMS.
2. **Die Inhalte gehören in `./site`, nicht in den Container.** Sonst sind
   sie beim nächsten Update weg.
3. **Rechte:** Im Container arbeitet alles als Benutzer mit der Nummer 1000.
   Gehört der Ordner jemand anderem, kann das CMS nicht speichern.
4. **`timeZone` in jeder `hugo.toml`**, etwa `timeZone = 'Europe/Berlin'`.
   Ohne sie verschluckt Hugo nachts zwischen 0 und 2 Uhr gespeicherte
   Beiträge. Sie gelten dann als „erst in der Zukunft erscheinend", und das
   CMS meldet trotzdem Erfolg.
5. **Starke Passwörter.** Lang und zufällig, nicht ausgedacht. Die Bremse
   hilft gegen einen Angreifer, nicht gegen viele gleichzeitig.
6. **Jeder Kunde ein eigenes Compose-Projekt.** Nie mehrere Kunden in eine
   `compose.yaml` oder ein gemeinsames Docker-Netz packen.
7. **Die Ports im Caddy-Block müssen zum richtigen Kunden gehören.** Ein
   Tippfehler verbindet Kunde A mit dem CMS von Kunde B.
8. **`auth.toml` und `auth.key` nie in ein Git-Repo.** Sie liegen deshalb in
   `auth/` neben `site/`, nicht darin.

---

## Wenn etwas schiefgeht

| Was man sieht | Was es bedeutet | Was tun |
|---|---|---|
| „…die Seite wurde nicht neu erzeugt: Error: …" | Hugo kam mit einer Datei oder Vorlage nicht klar | Meldung lesen, Details mit `docker compose logs cms` |
| „…nicht in die Versionsgeschichte aufgenommen" | Git hatte ein Problem | Rechte am Ordner prüfen, `docker compose logs cms` |
| „…die Sicherung klemmt" | Die Kopie außer Haus ging nicht durch | Remote und Deploy-Key prüfen. Der Text ist trotzdem sicher |
| Beitrag gespeichert, aber nicht auf der Seite | Meist Entwurf angehakt, Datum in der Zukunft oder `timeZone` fehlt | Häkchen „Entwurf", Datum und `hugo.toml` prüfen |
| „· unlesbar" in der Liste | Die Datei ist nicht in UTF-8 gespeichert | Datei in UTF-8 umwandeln |
| „Zu viele Versuche" beim Login | Die Bremse hat angeschlagen | 5 Minuten warten oder Anmeldedienst neu starten |
| Fehler 502 von Caddy | Ein Container läuft nicht | `docker compose ps`, dann `docker compose up -d` |
| Container startet nicht | Meist eine fehlerhafte `cms.toml` oder `auth.toml` | `docker compose logs`, die Meldung nennt Datei und Grund |

Alle Programme prüfen ihre Einstellungen **beim Start** und weigern sich zu
laufen, wenn etwas nicht stimmt. Das ist Absicht: Ein Fehler soll beim
Einrichten auffallen, nicht erst beim Kunden.

---

## Was noch fehlt oder ungetestet ist

- **Das Docker-Image wurde noch nie gebaut.** Der erste `docker build` ist
  der erste echte Test.
- **Das Zusammenspiel mit einem echten Caddy lief noch nie.** Beim ersten
  Kunden die Angriffe aus der Tabelle oben einmal von außen durchprobieren.
- **Sicherung außer Haus aus dem Container:** Dafür braucht der Container
  einen Deploy-Key. Ist vorbereitet, aber noch nicht eingerichtet.
- **Überwachung:** Wenn nachts ein Container neu startet, merkt es noch
  niemand.
- Vorgeschlagen, aber nicht gebaut: `--passwort` erzeugt selbst ein
  zufälliges Passwort, und eine Warnung beim Start, wenn `timeZone` fehlt.

---

## Kleines Wörterbuch

**127.0.0.1** – Die Adresse „dieser Rechner selbst". Was nur dort lauscht,
ist aus dem Internet nicht erreichbar.

**CMS** – Content-Management-System: die Oberfläche, in der Inhalte gepflegt
werden.

**Commit** – Ein Eintrag in der Versionsgeschichte von Git: „diese Datei
sah zu diesem Zeitpunkt so aus".

**Compose** – Werkzeug, das mehrere zusammengehörige Container aus einer
Datei (`compose.yaml`) startet.

**Container** – Eine laufende, abgeschottete Kopie eines Images.

**Cookie** – Kleine Datei, die der Browser speichert und bei jeder Anfrage
mitschickt. Hier das „Einlassbändchen" nach der Anmeldung.

**Deploy-Key** – Ein Schlüssel, der nur für ein einziges Git-Repo gilt.
Damit darf der Server Sicherungen schicken, sonst nichts.

**forward_auth** – Die Caddy-Funktion „vor dem Durchlassen erst die
Anmeldung fragen".

**EXIF** – Zusatzdaten in Fotos: Kameramodell, Zeitpunkt, oft auch der
Aufnahmeort. Beim Hochladen werden sie entfernt.

**Frontmatter** – Der Kopf einer Beitragsdatei zwischen den `---`-Zeilen, mit
Titel, Datum und Entwurf-Markierung.

**Hash** – Nicht umkehrbare Umwandlung eines Passworts, der „Fleischwolf".

**HTTPS** – Verschlüsselte Verbindung zwischen Browser und Server.

**Image** – Fertig gepacktes Softwarepaket, aus dem Container entstehen.

**Markdown** – Einfache Schreibweise für formatierten Text, etwa `**fett**`
oder `# Überschrift`.

**Port** – Eine Art Türnummer an einem Rechner. Ein Programm lauscht an
einer bestimmten Nummer, etwa 8081.

**Push** – Git schickt seine Commits an eine andere Stelle, hier als
Sicherungskopie.

**Reverse Proxy** – Ein Programm, das alle Anfragen entgegennimmt und an die
richtigen Programme dahinter weiterreicht. Hier Caddy.

**Salz** – Zufallswert, der vor dem Hashen ins Passwort gemischt wird.

**Signatur** – Unterschrift, die beweist, dass etwas nicht verändert wurde.
Nur wer den geheimen Schlüssel hat, kann sie erzeugen.

**Statische Seite** – Website aus fertigen Dateien, ohne Programm dahinter.

**UID** – Benutzernummer unter Linux. Wer welche Dateien lesen und schreiben
darf, hängt davon ab.

**VPS** – Virtual Private Server: ein gemieteter Server im Rechenzentrum.

**WebP** – Modernes Bildformat, deutlich kleiner als JPEG bei gleicher
Qualität. Alle heutigen Browser können es.

**Volume** – Ein Ordner auf dem Server, der in einen Container
hineingereicht wird. Was darin liegt, überlebt jedes Update.
