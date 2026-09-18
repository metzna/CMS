# Betrieb

Die Dateien hier sind Vorlagen, keine fertige Installation.

## Aufbau

Je Website ein vollstaendiges Paket: Inhalte, CMS, Anmeldung. Nichts davon
wird zwischen Kunden geteilt.

```
kunde-a.de       → Caddy liefert website/public/      (die fertige Seite)
cms.kunde-a.de   → Caddy, davor die Anmeldung
                     /login, /abmelden → auth.py    :9001
                     alles Uebrige     → server.py  :8081  (wenn angemeldet)
```

`auth.py` und `server.py` lauschen auf `127.0.0.1` und sind von aussen nicht
erreichbar -- der Proxy ist nicht zu umgehen.

Das CMS kennt die Anmeldung nicht. Wer wie anmeldet, ist allein eine Frage
von Caddy und `auth.py`; `server.py` bleibt unveraendert.

## Trennung der Websites

Jede Website hat ihren eigenen Anmeldedienst mit eigenem Sitzungsschluessel.
Ein Cookie von Seite A ist auf Seite B aus drei Gruenden wertlos:

1. **Der Browser.** Das Cookie wird ohne `Domain=` gesetzt, gilt also nur
   fuer genau eine Adresse und wird an keine andere geschickt.
2. **`auth.py`.** Im Cookie steht der Host mit drin, und der Dienst kennt nur
   den einen, zu dem er gehoert. Alles andere beantwortet er mit 404.
3. **Der Schluessel.** Jede Seite signiert mit einem eigenen `auth.key`.

## Einrichten

```bash
# 1. Ordner und Systembenutzer
useradd -r -s /usr/sbin/nologin cms-kunde-a
mkdir -p /srv/kunden/kunde-a /etc/cms/kunde-a
git clone <repo-oder-leer> /srv/kunden/kunde-a/site
chown -R cms-kunde-a: /srv/kunden/kunde-a /etc/cms/kunde-a

# 2. Ports und Inhalte eintragen
printf 'PORT=8081\nAUTH_PORT=9001\nCONTENT=/srv/kunden/kunde-a/site/website/content\n' \
    > /etc/cms/kunde-a.env

# 3. Zugang anlegen
python3 /opt/cms/auth.py --passwort          # Hash erzeugen
cp betrieb/auth.toml.beispiel /etc/cms/kunde-a/auth.toml
$EDITOR /etc/cms/kunde-a/auth.toml           # host, benutzer, passwort eintragen

# 4. Starten
systemctl enable --now cms@kunde-a cms-auth@kunde-a
systemctl reload caddy
```

Ports je Website hochzaehlen: 8081/9001, 8082/9002, und so fort.

## Ausprobieren ohne VPS

Die Beispieldatei laeuft nicht ohne Aenderung: ihr Hash ist ein Platzhalter,
und `cms.kunde-a.de` loest lokal nicht auf. Fuer einen Blick auf die
Loginseite braucht es eine kleine Konfiguration auf `localhost`:

```bash
python3 cms/auth.py --passwort          # Hash erzeugen, Zeile kopieren

cat > /tmp/auth-test.toml <<'TOML'
host     = "localhost"
name     = "Testkunde"
benutzer = "ich"
passwort = "<der Hash von oben>"
TOML

python3 -u cms/auth.py --config /tmp/auth-test.toml --unsicher
```

Dann `http://localhost:9000/login` aufrufen. Nach der Anmeldung leitet der
Dienst auf `/` weiter und antwortet dort mit 404 -- richtig so: diesen Pfad
reicht im Betrieb Caddy ans CMS weiter, der Anmeldedienst kennt ihn nicht.

`--unsicher` setzt das Cookie ohne `Secure`, damit es ueber `http://`
ueberhaupt ankommt. Nur lokal, nie im Netz. Das `-u` sorgt dafuer, dass die
Meldungen sofort erscheinen statt im Puffer zu haengen.

## Grenzen

- Ein Zugang je Website. Sollen zwei Personen beim Kunden pflegen, teilen
  sie ihn sich -- in den Protokollzeilen ist dann nicht zu unterscheiden,
  wer geschrieben hat.
- Die Bremse gegen Durchprobieren zaehlt pro Absender-IP: zehn Fehlversuche
  in fuenf Minuten sperren diese IP fuer diese Website. Die Zaehlung steht
  im Arbeitsspeicher, ein Neustart hebt jede Sperre auf.
- `auth.toml` wird nur beim Start gelesen. Neues Passwort:
  `systemctl restart cms-auth@kunde-a`. Angemeldete bleiben angemeldet, denn
  das Cookie haengt am Schluessel in `auth.key`, nicht an der Konfiguration.
- Sitzungen laufen nach zwoelf Stunden ab, das Cookie selbst verfaellt beim
  Schliessen des Browsers.
