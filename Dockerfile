# Image for one CMS instance: Python standard library plus Hugo.
#
# The Hugo version is pinned here. That is the whole point: a Hugo update
# can no longer silently change a customer's site, only once you rebuild
# this image.
#
# Build:  docker build -t cms:0.131.0 .
#         docker build --build-arg HUGO_VERSION=0.140.0 --build-arg HUGO_SHA256=… .

ARG HUGO_VERSION=0.131.0
ARG HUGO_ARCH=linux-amd64
# From hugo_${HUGO_VERSION}_checksums.txt in the release. No checksum, no build.
ARG HUGO_SHA256=88ba12c62772469695bff0ee0a724bf12922995243ed866076b165ad409de901

# --- Fetch Hugo, in a stage that is dropped later ------------------------
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

# --- The actual image ----------------------------------------------------
FROM python:3.13-slim

# Hugo extended is linked against libstdc++; the slim image doesn't ship it.
# git for --git, openssh-client for --push via a deploy key.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libstdc++6 git openssh-client; \
    rm -rf /var/lib/apt/lists/*

COPY --from=hugo /usr/local/bin/hugo /usr/local/bin/hugo

# Pillow converts uploaded images to WebP. The only package outside the
# standard library; prebuilt wheels exist, nothing gets compiled.
RUN pip install --no-cache-dir --root-user-action=ignore "Pillow==11.1.0"

# UID must match the owner of the mounted folder on the host, otherwise
# the CMS can't write. Adjust at build time if needed.
ARG CMS_UID=1000
RUN useradd --uid ${CMS_UID} --create-home --shell /usr/sbin/nologin cms

COPY cms/server.py cms/auth.py /opt/cms/
COPY cms/static/ /opt/cms/static/

USER cms
WORKDIR /site

# Listen on all addresses inside so Docker can reach it. To the outside,
# compose only exposes the ports on 127.0.0.1 -- see compose.yaml.
EXPOSE 8080
CMD ["python3", "-u", "/opt/cms/server.py", \
     "--host", "0.0.0.0", "--port", "8080", \
     "--content", "/site/website/content"]
