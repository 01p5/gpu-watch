# gpu-dashboard image.
#
# Mirror of slurm-mgr's Dockerfile. Two stages:
#   1. frontend-build (node:20-alpine) — vite builds the SPA, with
#      VITE_BASE_PATH=/gpu/ baked in by default so the bundle works
#      under Olympus's reverse proxy. Override for standalone builds.
#   2. python:3.12-slim — installs gpuwatchlib + gpu-mcp + gpu-dashboard,
#      copies the SPA into packages/gpu-dashboard/static/dist, exposes :8780.
#
# Build (standalone):
#   docker build -t gpu-dashboard:dev --build-arg VITE_BASE_PATH=/ .
#
# Build (under Olympus reverse proxy at /gpu/*):
#   docker build -t gpu-dashboard:dev .

ARG VITE_BASE_PATH=/gpu/

# ---------- stage 1: frontend ----------
FROM node:20-alpine AS frontend-build
ARG VITE_BASE_PATH
ENV VITE_BASE_PATH=${VITE_BASE_PATH}
WORKDIR /build
COPY packages/gpu-dashboard/frontend/package.json packages/gpu-dashboard/frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci --silent; else npm install --silent; fi
COPY packages/gpu-dashboard/frontend/ ./
RUN npm run build
RUN mkdir -p /spa && cp -r /static/dist/. /spa/

# ---------- stage 2: backend ----------
FROM python:3.12-slim
WORKDIR /opt/gpu-watch

RUN apt-get update \
 && apt-get install -y --no-install-recommends openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY packages packages

RUN pip install --no-cache-dir --no-deps -e ./packages/gpuwatchlib \
 && pip install --no-cache-dir --no-deps -e ./packages/gpu-mcp \
 && pip install --no-cache-dir --no-deps -e ./packages/gpu-dashboard \
 && pip install --no-cache-dir 'paramiko>=3.4,<4'

COPY --from=frontend-build /spa /opt/gpu-watch/packages/gpu-dashboard/static/dist

ENV PYTHONUNBUFFERED=1
EXPOSE 8780
CMD ["gpu-dashboard", "--host=0.0.0.0", "--port=8780"]
