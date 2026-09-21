# Image fuer eine CMS-Instanz: Python-Standardbibliothek plus Hugo.
#
# Die Hugo-Version steht hier fest. Das ist der Sinn der Sache: ein Update
# von Hugo kann die Seite eines Kunden nicht mehr still veraendern, sondern
# erst dann, wenn du dieses Image neu baust.
#
# Bauen:  docker build -t cms:0.131.0 .
#         docker build --build-arg HUGO_VERSION=0.140.0 --build-arg HUGO_SHA256=… .

ARG HUGO_VERSION=0.131.0
ARG HUGO_ARCH=linux-amd64
# Aus hugo_${HUGO_VERSION}_checksums.txt im Release. Ohne Pruefsumme kein Build.
ARG HUGO_SHA256=88ba12c62772469695bff0ee0a724bf12922995243ed866076b165ad409de901

# --- Hugo holen, in einer Stufe, die spaeter wegfaellt --------------------
FROM debian:bookworm-slim AS hugo
ARG HUGO_VERSION
ARG HUGO_ARCH
ARG HUGO_SHA256
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    datei="hugo_extended_${HUGO_VERSION}_${HUGO_ARCH}.tar.gz"; \
    curl -fsSL -o /tmp/hugo.tar.gz \
        "https://github.com/gohugoio/hugo/releases/download/v${HUGO_VERSION}/${datei}"; \
    echo "${HUGO_SHA256}  /tmp/hugo.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/hugo.tar.gz -C /usr/local/bin hugo; \
    /usr/local/bin/hugo version

# --- Das eigentliche Image ------------------------------------------------
FROM python:3.13-slim

# Hugo extended ist gegen libstdc++ gelinkt; das slim-Image bringt es nicht mit.
# git fuer --git, openssh-client fuer --push ueber einen Deploy-Key.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libstdc++6 git openssh-client; \
    rm -rf /var/lib/apt/lists/*

COPY --from=hugo /usr/local/bin/hugo /usr/local/bin/hugo

# UID muss dem Eigentuemer des gemounteten Ordners auf dem Host entsprechen,
# sonst kann das CMS nicht schreiben. Notfalls beim Bauen anpassen.
ARG CMS_UID=1000
RUN useradd --uid ${CMS_UID} --create-home --shell /usr/sbin/nologin cms

COPY cms/server.py cms/auth.py /opt/cms/
COPY cms/static/ /opt/cms/static/

USER cms
WORKDIR /site

# Innen auf allen Adressen lauschen, damit Docker herankommt. Nach aussen
# gibt compose die Ports nur auf 127.0.0.1 frei -- siehe compose.yaml.
EXPOSE 8080
CMD ["python3", "-u", "/opt/cms/server.py", \
     "--host", "0.0.0.0", "--port", "8080", \
     "--content", "/site/website/content"]
