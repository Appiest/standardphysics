# One image, two roles. docker/entrypoint.sh runs the API, the web workspace,
# or both, so a host that gives you a single container and a single port works
# the same way as compose running the two side by side.
#
# Node and Python both have to be here: the workspace is a Next server that
# renders on request, not a bundle of static files. Debian bookworm ships
# Python 3.11, which is what the packages ask for, so one base image carries
# both runtimes rather than copying binaries between images.

FROM node:22-bookworm-slim AS web
WORKDIR /app/apps/web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci --no-audit --no-fund --loglevel=error
COPY apps/web ./
# Only the rewrite destination is baked in, and the entrypoint overrides it.
# The deck imports fixture JSON through the @fixtures monorepo alias in
# tsconfig; the build needs those files where the alias points.
COPY packages/fixtures/standardphysics_fixtures/data /app/packages/fixtures/standardphysics_fixtures/data
# Next bakes rewrites() into the build, so the web's API origin is chosen here.
# The default serves the one-container "all" role; compose running the web
# alone must pass --build-arg SP_API_ORIGIN=http://api:8787 to its build.
ARG SP_API_ORIGIN=http://127.0.0.1:8787
ENV SP_API_ORIGIN=$SP_API_ORIGIN
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build


FROM node:22-bookworm-slim AS runtime

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    NEXT_TELEMETRY_DISABLED=1 \
    SP_DATA_DIR=/data

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY packages ./packages
COPY services ./services
COPY scripts ./scripts
RUN /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir \
      -e . -e packages/contracts -e packages/fixtures -e packages/pipeline \
      -e packages/agents -e services/api

COPY --from=web /app/apps/web/.next ./apps/web/.next
COPY --from=web /app/apps/web/node_modules ./apps/web/node_modules
COPY --from=web /app/apps/web/public ./apps/web/public
COPY --from=web /app/apps/web/package.json ./apps/web/package.json
COPY --from=web /app/apps/web/next.config.ts ./apps/web/next.config.ts
COPY apps/web/src ./apps/web/src
COPY apps/web/tsconfig.json ./apps/web/tsconfig.json

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

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
