FROM python:3.12-slim-bookworm AS api
WORKDIR /app
ARG PIP_INDEX_URL
COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt
COPY api ./api
ENV PYTHONUNBUFFERED=1 CI_TRACE_DATABASE=/data/ci-trace.sqlite3
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8001"]

FROM node:24-bookworm-slim AS web
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/next.config.mjs web/postcss.config.mjs web/tsconfig.json ./
COPY web/public ./public
COPY web/src ./src
RUN npm run build
USER node
CMD ["node", "node_modules/next/dist/bin/next", "start", "--hostname", "0.0.0.0"]
