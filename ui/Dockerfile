# syntax=docker/dockerfile:1
#
# XbrlRag React UI (WS-6 / B7) — multi-stage: build the Vite bundle with Node, then
# serve the static files with nginx (which also reverse-proxies /api and /metrics
# to the BFF so the browser hits a single origin). Build from the ui/ directory:
#   docker build -t arag-ui:local ui/

FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine AS runtime
# Static bundle
COPY --from=build /app/dist /usr/share/nginx/html
# Reverse-proxy config as a TEMPLATE — the nginx entrypoint runs envsubst on it
# at startup, substituting only defined env vars (XBRLRAG_API_UPSTREAM,
# XBRLRAG_PROXY_READ_TIMEOUT) and leaving nginx runtime vars ($uri, $host, …) intact.
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV XBRLRAG_API_UPSTREAM=api:8000
# Seconds nginx waits on the API before returning 504. Keep ABOVE the app's own
# RAGINFER_TIMEOUT_S so the inner timeout always fires first. Overridable from
# compose, so retuning it needs no image rebuild — but it MUST have a default here:
# envsubst leaves an undefined var as a literal ${...} and nginx then fails to start.
ENV XBRLRAG_PROXY_READ_TIMEOUT=600
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=4s --retries=5 \
    CMD wget -qO- http://localhost/ >/dev/null 2>&1 || exit 1
