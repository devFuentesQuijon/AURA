# ---------------------------------------------------------------------------
# Etapa 1: pi agent (github.com/earendil-works/pi) sobre Node 22
# ---------------------------------------------------------------------------
FROM node:22-slim AS pi
RUN npm install -g @earendil-works/pi-coding-agent \
    && rm -rf /root/.npm

# ---------------------------------------------------------------------------
# Etapa 2: imagen final = FastAPI + Node + pi
# ---------------------------------------------------------------------------
FROM python:3.12-slim

# Node runtime + pi desde la etapa 1. El bin npm es un symlink relativo,
# así que se crea un wrapper que apunta al bundle real dentro de node_modules.
COPY --from=pi /usr/local/bin/node /usr/local/bin/node
COPY --from=pi /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN printf '#!/bin/sh\nexec /usr/local/bin/node /usr/local/lib/node_modules/@earendil-works/pi-coding-agent/dist/bundle/cli.js "$@"\n' \
      > /usr/local/bin/pi && chmod +x /usr/local/bin/pi
ENV PATH="/usr/local/bin:${PATH}"

# ripgrep para la herramienta grep de pi
RUN apt-get update && apt-get install -y --no-install-recommends ripgrep ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# La config del proveedor LLM para pi la genera entrypoint.sh en el
# arranque (cualquier endpoint compatible OpenAI, p.ej. un LLM local).

# --- API FastAPI del chat ---
# Contexto de build: RAÍZ del repo (ver docker-compose.yml). Se copian
# app + datos scrapeados + marca del sitio.
WORKDIR /srv

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY backend/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
COPY data ./data
COPY frontend/assets ./brand_assets

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=8).read()"

ENTRYPOINT ["/entrypoint.sh"]
