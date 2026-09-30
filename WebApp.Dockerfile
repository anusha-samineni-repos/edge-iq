# Edge IQ operator console.
#
# Two stages: build the bundle with Node, serve it with nginx. The runtime
# image carries no toolchain and no source - just static assets behind a
# webserver, which keeps the attack surface small for a workload that sits
# adjacent to OT systems.

# ---------------------------------------------------------------- build ----
FROM node:20-alpine AS build

WORKDIR /build

# Copy manifests first so the dependency layer is cached independently of
# source edits - a change to App.jsx shouldn't trigger a reinstall.
COPY src/App/package.json ./
COPY src/App/package-lock.json* ./

# npm ci requires a lockfile. Fall back to install so a fresh clone (before a
# lockfile is committed) still builds.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY src/App/ ./

RUN npm run build

# -------------------------------------------------------------- runtime ----
FROM nginx:1.27-alpine

# The SPA owns its routes: any unmatched path serves index.html so a deep link
# or a browser refresh doesn't 404. /api is proxied to the API container so the
# browser only ever talks to one origin and CORS never enters the picture.
RUN printf '%s\n' \
    'server {' \
    '  listen 3000;' \
    '  server_name _;' \
    '  root /usr/share/nginx/html;' \
    '  index index.html;' \
    '  gzip on;' \
    '  gzip_types text/css application/javascript application/json image/svg+xml;' \
    '  gzip_min_length 1024;' \
    '' \
    '  location /assets/ {' \
    '    expires 1y;' \
    '    add_header Cache-Control "public, immutable";' \
    '  }' \
    '' \
    '  location = /index.html {' \
    '    add_header Cache-Control "no-cache";' \
    '  }' \
    '' \
    '  location /api/ {' \
    '    proxy_pass         $API_BASE_URL;' \
    '    proxy_http_version 1.1;' \
    '    proxy_set_header   Host              $host;' \
    '    proxy_set_header   X-Real-IP         $remote_addr;' \
    '    proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;' \
    '    proxy_set_header   X-Forwarded-Proto $scheme;' \
    '    proxy_set_header   Connection        "";' \
    '' \
    '    # Server-Sent Events: buffering would hold the whole turn until the' \
    '    # stream closed, defeating the point of streaming the answer.' \
    '    proxy_buffering    off;' \
    '    proxy_cache        off;' \
    '    proxy_read_timeout 300s;' \
    '  }' \
    '' \
    '  location / {' \
    '    try_files $uri $uri/ /index.html;' \
    '  }' \
    '}' \
    > /etc/nginx/templates/default.conf.template

COPY --from=build /build/dist /usr/share/nginx/html

# Consumed by the nginx template at container start. Container Apps injects the
# real API FQDN; this default keeps a local `docker run` working.
#
# NGINX_ENVSUBST_FILTER is essential: the default entrypoint runs envsubst over
# every environment variable, which would replace nginx's own $host,
# $remote_addr and $uri with empty strings and break the proxy. The filter
# restricts substitution to API_BASE_URL alone.
ENV API_BASE_URL=http://edgeiq-api:8000 \
    NGINX_ENVSUBST_FILTER=API_BASE_URL \
    PORT=3000

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost:3000/ || exit 1
