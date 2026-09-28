# One image, two roles. docker/entrypoint.sh runs the API, the web workspace,
# or both, so a host that gives you a single container and a single port works
# the same way as compose running the two side by side.
#
# Node and Python both have to be here: the workspace is a Next server that
# renders on request, not a bundle of static files. Debian bookworm ships
# Python 3.11, which is what the packages ask for, so one base image carries
# both runtimes rather than copying binaries between images.
#
# The base is pinned by digest as well as named by tag, so a rebuild months
# from now starts from the same bytes CI tested rather than whatever the tag
# has moved to. The digest is the multi-platform index; move it on purpose,
# with `docker buildx imagetools inspect node:22-bookworm-slim`.
ARG NODE_IMAGE=node:22-bookworm-slim@sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c

FROM ${NODE_IMAGE} AS web
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY apps/web ./
# The deck imports fixture JSON through the @fixtures monorepo alias in
# tsconfig; the build needs those files where the alias points.
COPY packages/fixtures/standardphysics_fixtures/data /app/packages/fixtures/standardphysics_fixtures/data
# next.config.ts reads SP_API_ORIGIN for its /api rewrite, and `next build`
# writes that destination into routes-manifest.json, so the browser's /api
# requests go wherever this build says, whatever the container is started
# with. Setting SP_API_ORIGIN at runtime only moves the fetches the pages make
# while rendering on the server. The default serves the one-container "all"
# role; compose running the web on its own passes SP_API_ORIGIN=http://api:8787.
ARG SP_API_ORIGIN=http://127.0.0.1:8787
ENV SP_API_ORIGIN=$SP_API_ORIGIN
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


FROM ${NODE_IMAGE} AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

# Blender bakes the textures and renders the picture beside each finding. It
# runs as a subprocess, so the binary has to be here or every texture build
# fails and every report comes out with no pictures in it.
#
# Pinned, and not from Debian: check_blender.py proves the only reliable test
# is a USDZ round-trip, and 4.0.2 fails it while still advertising *.usd. This
# is the version a developer's Mac runs, so the server behaves the same way.
#
# blender.org publishes no arm64 Linux build of it. That is what holds this
# image on x86_64, and it is the thing to check before moving to an ARM host.
#
# download.blender.org sits behind a bot challenge that answers curl with a
# 403 page, so the archive is tried there first and then on blender.org's
# listed mirrors. The checksum is Blender's own published sha256, and it is
# what makes any mirror as good as the origin: a changed file fails the build.
ARG BLENDER_SERIES=5.2
ARG BLENDER_VERSION=5.2.1
ARG BLENDER_SHA256=a31f524fa99a527d3d52b7f5aaa68c34e1a19d5a1c9473f79c5cc610fd5b10e9
ARG BLENDER_SOURCES="https://download.blender.org/release https://mirrors.ocf.berkeley.edu/blender/release https://ftp.nluug.nl/pub/graphics/blender/release"
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      curl ca-certificates xz-utils \
      libx11-6 libxi6 libxxf86vm1 libxfixes3 libxrender1 libxkbcommon0 \
      libsm6 libice6 libxcb1 libglu1-mesa libegl1 libgomp1 \
 && for source in $BLENDER_SOURCES; do \
      curl -fsSL --retry 3 -o /tmp/blender.tar.xz \
        "$source/Blender${BLENDER_SERIES}/blender-${BLENDER_VERSION}-linux-x64.tar.xz" && break; \
    done \
 && echo "${BLENDER_SHA256}  /tmp/blender.tar.xz" | sha256sum -c - \
 && tar -xJf /tmp/blender.tar.xz -C /opt \
 && rm /tmp/blender.tar.xz \
 && mv "/opt/blender-${BLENDER_VERSION}-linux-x64" /opt/blender \
 && rm -rf /var/lib/apt/lists/*

# blender_path() reads this before it looks on PATH or in a macOS app bundle.
ENV BLENDER=/opt/blender/blender

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    SP_DATA_DIR=/data

WORKDIR /app

# Every third-party version comes from the lock, and it installs before the
# source is copied so an edit to the code reuses this layer. The lock carries
# the observability extra: without weave in the image, WANDB_PROJECT would be
# set in production and nothing would ever be traced.
COPY requirements.lock ./
RUN /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml ./
COPY packages ./packages
COPY services ./services
COPY scripts ./scripts
RUN /opt/venv/bin/pip install --no-cache-dir --no-deps \
      -e packages/contracts -e packages/fixtures -e packages/pipeline \
      -e "packages/agents[observability]" -e services/api \
 && /opt/venv/bin/pip check

COPY --from=web /app/apps/web/.next ./apps/web/.next
COPY --from=web /app/apps/web/node_modules ./apps/web/node_modules
COPY --from=web /app/apps/web/public ./apps/web/public
COPY --from=web /app/apps/web/package.json ./apps/web/package.json
COPY --from=web /app/apps/web/next.config.ts ./apps/web/next.config.ts
COPY apps/web/src ./apps/web/src
COPY apps/web/tsconfig.json ./apps/web/tsconfig.json

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

# The commit this image holds, which /health/details reports. It is declared
# this late because every layer after an ARG's first use rebuilds when its
# value changes, and each deploy changes it. deploy/digitalocean passes it
# from `git rev-parse HEAD`.
ARG GIT_SHA=unknown
ENV SP_GIT_SHA=$GIT_SHA

# The scans and their uploaded artifacts live here. Mount it, or a restart
# loses every shop anyone has scanned.
RUN mkdir -p /data && useradd --system --uid 10001 physics && chown -R physics /data /app
USER physics
VOLUME ["/data"]

EXPOSE 3000 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD node -e "fetch('http://127.0.0.1:'+(process.env.SP_API_PORT||8787)+'/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["all"]
