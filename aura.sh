#!/usr/bin/env bash
# CHAT-AURA — asistente de operación de una instancia.
#
# Flujo de una instancia (un sitio):
#   1. cp .env.example .env   →  editar SITE_URL y ZAI_API_KEY
#   2. ./aura.sh init         →  venv + dependencias (requiere Chrome local)
#   3. ./aura.sh scrape       →  scrapea el sitio, extrae marca y construye la KB
#   4. ./aura.sh up           →  levanta los contenedores (docker compose)
#
# Para OTRO sitio: otra copia del repo (o del despliegue) con otro .env.
set -euo pipefail
cd "$(dirname "$0")"

PY=.venv/bin/python
PIP=.venv/bin/pip

# Carga SITE_URL / ZAI_* desde .env si existe
if [ -f .env ]; then set -a; . ./.env; set +a; fi

cmd="${1:-help}"

case "$cmd" in
  init)
    echo "==> Creando venv (python3.12) e instalando dependencias"
    python3.12 -m venv .venv
    $PIP install --upgrade pip >/dev/null
    $PIP install -r backend/requirements.txt -r scraper/requirements.txt
    $PY -c "import playwright" && echo "OK: playwright instalado (usa el Chrome del sistema)"
    ;;
  login)
    # Login one-shot: abre Chrome, espera a que el usuario inicie sesión
    # (SSO/2FA incluido) y guarda el estado en data/auth_state.json.
    # Si necesitas más tiempo: ./aura.sh login-open  y luego  login-save
    [ -n "${SITE_URL:-}" ] || { echo "Falta SITE_URL en .env"; exit 1; }
    $PY scraper/auth_session.py oneshot --url "$SITE_URL"
    ;;
  login-open)
    # Abre Chrome y lo deja abierto (sin timeout) para iniciar sesión con
    # calma. La sesión se captura después con ./aura.sh login-save.
    [ -n "${SITE_URL:-}" ] || { echo "Falta SITE_URL en .env"; exit 1; }
    $PY scraper/auth_session.py open --url "$SITE_URL"
    ;;
  login-save)
    # Captura la sesión del Chrome abierto con login-open y lo cierra.
    $PY scraper/auth_session.py save
    ;;
  scrape-cdp)
    # Scrapeo dentro del Chrome ya logueado (vía CDP). Flujo para apps que
    # guardan la sesión en sessionStorage (transferencia de cookies no basta):
    #   1) ./aura.sh login-open   2) iniciar sesión  3) ./aura.sh scrape-cdp
    [ -n "${SITE_URL:-}" ] || { echo "Falta SITE_URL en .env"; exit 1; }
    args=(--url "$SITE_URL" --cdp "${AUTH_CDP_PORT:-9222}")
    [ -n "${SKIP_PATTERN:-}" ] && args+=(--skip-pattern "$SKIP_PATTERN")
    [ -n "${MAX_PAGES:-}" ] && args+=(--max-pages "$MAX_PAGES")
    [ -n "${MAX_DEPTH:-}" ] && args+=(--max-depth "$MAX_DEPTH")
    [ "${HEADLESS:-0}" = "1" ] && args+=(--headless)
    $PY scraper/scrape_site.py "${args[@]}" ${EXTRA_ARGS:-}
    $PY scraper/extract_brand.py --url "$SITE_URL" || echo "   (marca con defaults; el chat funciona igual)"
    $PY scraper/build_kb.py
    echo "Listo. Levanta la instancia con: ./aura.sh up"
    ;;
  scrape)
    [ -n "${SITE_URL:-}" ] || { echo "Falta SITE_URL en .env"; exit 1; }
    echo "==> 1/3 Scraping $SITE_URL"
    args=(--url "$SITE_URL")
    [ -n "${SKIP_PATTERN:-}" ] && args+=(--skip-pattern "$SKIP_PATTERN")
    [ -n "${MAX_PAGES:-}" ] && args+=(--max-pages "$MAX_PAGES")
    [ -n "${MAX_DEPTH:-}" ] && args+=(--max-depth "$MAX_DEPTH")
    [ "${HEADLESS:-0}" = "1" ] && args+=(--headless)
    $PY scraper/scrape_site.py "${args[@]}" ${EXTRA_ARGS:-}
    echo "==> 2/3 Extrayendo marca (logo + paleta)"
    $PY scraper/extract_brand.py --url "$SITE_URL" || echo "   (marca con defaults; el chat funciona igual)"
    echo "==> 3/3 Construyendo base de conocimiento"
    $PY scraper/build_kb.py
    echo "Listo. Levanta la instancia con: ./aura.sh up"
    ;;
  env)
    # Convierte .tool-box (BASE_URL/API_KEY/MODEL) en .env del backend
    # (SITE_URL, ZAI_*). No muestra valores por pantalla.
    [ -f .tool-box ] || { echo "No existe .tool-box"; exit 1; }
    $PY - <<'PY'
import os, re
vals = {}
for line in open(".tool-box", encoding="utf-8"):
    m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.+?)\s*$", line)
    if m:
        vals[m.group(1)] = m.group(2)
# preservar SITE_URL y demás valores ya definidos en .env previo
previo = {}
if os.path.exists(".env"):
    for line in open(".env", encoding="utf-8"):
        m = re.match(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", line.strip())
        if m:
            previo[m.group(1)] = m.group(2)
site_url = vals.get("SITE_URL") or previo.get("SITE_URL", "")
with open(".env", "w", encoding="utf-8") as f:
    f.write(f"SITE_URL={site_url}\n")
    f.write(f"ZAI_BASE_URL={vals['BASE_URL']}\n")
    f.write(f"ZAI_API_KEY={vals['API_KEY']}\n")
    f.write(f"ZAI_MODEL={vals['MODEL']}\n")
os.chmod(".env", 0o600)
print(".env generado desde .tool-box (valores no mostrados)")
PY
    ;;
  dev)
    # Backend local con recarga (para probar sin Docker; .env ya se cargó
    # arriba). El frontend queda servido por Docker o cualquier nginx estático.
    $PY -m uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port 8199 --reload
    ;;
  up)
    docker compose up -d --build
    echo "Frontend: http://localhost:8290 · Backend: http://localhost:8291/api/health"
    ;;
  down)
    docker compose down
    ;;
  scrape-again)
    # Re-scrapeo manteniendo la marca ya extraída
    $PY scraper/scrape_site.py --url "${SITE_URL:?Falta SITE_URL}" ${EXTRA_ARGS:-}
    $PY scraper/build_kb.py
    docker compose up -d --build backend
    ;;
  *)
    sed -n '2,14p' "$0"
    ;;
esac
