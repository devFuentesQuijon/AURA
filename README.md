# CHAT-AURA · Copiloto web para cualquier sitio

[![Stack](https://img.shields.io/badge/stack-FastAPI%20%2B%20nginx%20%2B%20GLM-FF6B00)]()

**CHAT-AURA** convierte cualquier sitio web en un chat con copiloto: se le indica la URL del sitio, un scraper con navegador real (Playwright + Chrome) recorre sus páginas y organiza el contenido en una base de conocimiento, y los usuarios de ese sitio pueden preguntarle en lenguaje natural. Cada respuesta cita las páginas oficiales del sitio.

**Una instancia = un sitio.** Para copilotear otro sitio se levanta otra instancia con otro `SITE_URL`.

---

## Cómo funciona

```
 onboarding (una vez por sitio)                uso (usuarios del sitio)
 ────────────────────────────────              ─────────────────────────

 .env: SITE_URL=https://sitio.cl                    usuario
        └─> scrape_site.py  (Chrome real, BFS            │
            acotado, extrae secciones por página)  ┌─────▼──────────┐
        └─> extract_brand.py (logo, favicon,       │ frontend nginx │
            paleta -> frontend/assets)             │  (tematizado   │
        └─> build_kb.py      (data/                │   con la marca │
            knowledge_base.json)                   │   del sitio)   │
                                                   └───┬────────────┘
 docker compose up                                /api/*
                                                   └───┬────────────┘
                                                  backend FastAPI
                                                  - guardrails
                                                  - pi agent (lee la KB
                                                    con read/grep/glob)
                                                  - fallback tool-calling
                                                    (listar_paginas,
                                                     buscar_info,
                                                     obtener_pagina)
```

## Levantar una instancia

Requisitos: Python 3.12 + Google Chrome instalado (el scraper usa el Chrome del sistema), Docker + docker compose para el despliegue, y una clave de la API de Z.ai (GLM).

```bash
cp .env.example .env        # 1. editar SITE_URL y ZAI_API_KEY
./aura.sh init              # 2. venv + dependencias (una sola vez)
./aura.sh scrape            # 3. scrapea el sitio + marca + KB
./aura.sh up                # 4. build y up de los contenedores
```

Resultado: chat en `http://localhost:8290` (backend en `:8291`).

Para **re-scrapear** tras cambios en el sitio: `./aura.sh scrape-again`.

### Manuales y documentos propios (manual/)

Además de lo scrapeado del sitio, puedes sumar **manuales de usuario, FAQs o
documentación interna** en Markdown: copia los `.md` a la carpeta `manual/`
(en la raíz del repo) y reconstruye la KB:

```bash
mkdir -p manual && cp /ruta/a/mis-manuales/*.md manual/
./.venv/bin/python scraper/build_kb.py   # re-trocea sitio + manuales
docker compose up -d --build backend     # la KB se hornea en la imagen
```

Cada documento se troza por secciones (`## título`) en chunks tipo `manual`
(página `Manual: <título>`). No aparecen en el índice de páginas del sidebar
(ese sigue siendo solo del sitio), pero el agente los usa en `buscar_info`
y puede citarlos como fuente. Cuando la fuente es un manual, el enlace de la
fuente apunta al inicio del sitio.

### Datos en vivo (opcional, con la cuenta del usuario)

El agente puede consultar **APIs del sitio actuando con la identidad del
usuario que chatea**, para responder cosas como "¿por qué hay 3375 facturas
pendientes?" con cifras reales. Solo lectura, y la autorización la aplica la
propia API (el usuario solo ve lo que su token le permitiría ver directamente):

```bash
# .env
LIVE_API_ENABLED=1
LIVE_API_BASE_URL=http://localhost:5200/api/v1   # …/api/v1 del sitio
```

Las herramientas de datos **no están en el código**: cada instancia las define
declarativamente con dos proveedores complementarios (ambos alimentan el mismo
registro de tools del agente):

**1. Manifiesto REST (`data/live_tools.yaml`)** — una entrada YAML por tool:
nombre, descripción, método, endpoint, parámetros (`map_to` renombra al nombre
real del API, `{var}` en el endpoint se rellena por path) y un template Jinja2
que formatea el JSON de respuesta. Convención de templates: los saltos de línea
críticos se emiten con `{{ '\n' }}` (los tags `{% %}` se comen el newline
siguiente). Hay un ejemplo completo en `examples/live_tools.sixfacturia.yaml`.

**2. Servidores MCP (`data/mcp_servers.json`)** — para servicios que ya
exponen Model Context Protocol. El cliente conecta al arrancar, lista las
tools (prefijo `<servidor>__<tool>`) y las expone según un allowlist:

```json
[
  {"name": "github", "transport": "http", "url": "http://localhost:3001/mcp",
   "forward_auth": true, "allowed_tools": ["search_repositories"], "timeout": 20},
  {"name": "local", "transport": "stdio", "command": "python3",
   "args": ["mi_server.py"], "env": {"VAR": "1"}}
]
```

- `allowed_tools`: vacío/ausente = todas (con tope por servidor). Declara solo
  las de solo-lectura que necesites.
- `forward_auth` (http): reenvía el Bearer del usuario **por llamada**, para
  que el servidor MCP aplique los permisos del usuario.
- Un servidor caído se omite con warning; se reintenta al reiniciar.

Para copilotar **otro servicio**: copia la instancia, ajusta `SITE_URL` y
`LIVE_API_BASE_URL`, y escribe su `live_tools.yaml` / `mcp_servers.json`. Cero
código Python.

Flujo del token: el usuario obtiene su JWT desde la documentación de la API del
sitio (`/docs` → Authorize) y lo pega en el botón **Conectar cuenta** del chat;
el frontend lo envía en cada mensaje como header `X-User-Token` (solo en
memoria, nunca en localStorage) y el backend lo reenvía como `Bearer`. Sin
token, sin sesión o sin permiso, el agente lo dice con honestidad y responde
con la documentación.

Modo embebido (fase 2): si el sitio embebe el chat como iframe, puede entregar
el token automáticamente con
`postMessage({type:'sixfacturia-token', token}, '<origen-del-chat>')` desde el
origen del sitio; el frontend lo acepta y conecta sin fricción.

### Probar sin Docker (solo backend local)

```bash
./aura.sh dev     # uvicorn con recarga en :8199
# /api/health, /api/config, /api/paginas, POST /api/chat
```

## Multi-instancia (varios sitios)

El repo es la plantilla. Cada sitio copilotado es una copia/despliegue con su propio `.env`:

```bash
# instancia A (sitio A)
git clone <repo> chat-sitio-a && cd chat-sitio-a
# .env con SITE_URL=https://sitio-a.cl, ASSISTANT_NAME=Aura
./aura.sh init && ./aura.sh scrape && ./aura.sh up
# publicar en su propio puerto/túnel...

# instancia B (sitio B): mismo procedimiento, otro .env
```

Los datos scrapeados (`data/*.json`) y la marca (`frontend/assets/`) son **generados y no se versionan**: cada instancia lleva los de su sitio, empaquetados en las imágenes Docker al hacer `up`. Los contenedores se renombran vía `container_name` en cada despliegue (o con `-p` de compose si se despliegan en el mismo Docker).

## Qué se scrapea (y qué no)

- Crawler BFS desde `SITE_URL`, **mismo dominio registrable** (incluye subdominios; `--same-host` para restringir al host exacto).
- Límites por defecto: 60 páginas, profundidad 3 (`--max-pages`, `--max-depth`, `EXTRA_ARGS`).
- Sin query-strings (evita trampas de crawler), sin recursos (pdf/imágenes/…), sin rutas típicas de admin/carrito/búsqueda.
- Extracción por nodos hoja con `textContent`: captura acordeones y pestañas colapsadas; omite nav/header/footer.
- Filtro de **boilerplate**: secciones repetidas en muchas páginas (banners, avisos, redes sociales) no entran a la KB.
- Cortesía: pausa aleatoria entre páginas y User-Agent de navegador real.
- **Guardado incremental**: scrape.json se actualiza tras cada página (un scrape
  interrumpido no pierde el avance). `--headless` para no abrir ventana
  (recomendado en apps locales; en sitios públicos con anti-bot mejor headed).
- **`SKIP_PATTERN`** (regex sobre el path) para no visitar detalles dinámicos
  que ensucian la KB, p.ej. en apps con rutas por UUID:
  `SKIP_PATTERN='/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(/file)?$' ./aura.sh scrape`.
  También: `MAX_PAGES`, `MAX_DEPTH`, `HEADLESS=1` como variables de entorno.

Opcional por instancia: `data/synonyms.json` (`{"término": "expansión"}`) para enriquecer la búsqueda con vocabulario propio del rubro.

### Sitios con login (SSO/2FA)

Si el sitio exige autenticación, el scraper solo vería la pantalla de login. El flujo soportado es sesión manual persistente (funciona con Microsoft/Google SSO, CAPTCHA y 2FA, porque el login lo hace una persona una vez):

```bash
./aura.sh login     # abre Chrome: inicia sesión a mano; guarda data/auth_state.json
./aura.sh scrape    # re-scrapea navegando con esa sesión
```

- `data/auth_state.json` contiene cookies de sesión: es secreto (ignorado por git, chmod 600) y caduca según el sitio — se renueva repitiendo `./aura.sh login`.
- Durante el crawl, cualquier página que aterrice en una ruta de login queda marcada como `auth_required` y el resumen final lo advierte.
- Ojo: `localhost` y `127.0.0.1` son dominios distintos para las cookies; usa siempre la misma forma en `SITE_URL` que en el login.

## API

| Endpoint | Descripción |
|---|---|
| `GET /api/health` | Estado, modelo, motor (pi/toolloop), chunks de la KB, sitio configurado |
| `GET /api/config` | Identidad de la instancia: sitio, asistente, marca, sugerencias y saludo |
| `GET /api/paginas` | Índice de páginas del sitio (sidebar del chat) |
| `POST /api/chat` | `{message, history[], session_id}` → `{reply, fuentes[], paginas_sugeridas[], blocked}` |

## Arquitectura (heredada de CHAT-ENAC)

- **Motor del agente**: [pi](https://github.com/earendil-works/pi) en modo one-shot con herramientas de **solo lectura** (`read`, `grep`, `glob`) sobre `data/knowledge_base.json`; planifica sus pasos y responde solo con datos del sitio. **Fallback**: loop propio de tool-calling (`listar_paginas`, `buscar_info`, `obtener_pagina`) sobre el mismo retrieval BM25.
- **RAG**: BM25 determinista con normalización de español, boost por título de página y chunk panorámico para preguntas de "¿qué hay en este sitio?". Sin embeddings externos.
- **Guardrails**: rate limit por IP, límite de longitud, bloqueo de contenido dañino, derivación de crisis, offtopic genérico (solo si no toca el vocabulario del sitio), **sanitización de URLs** (solo responde/enlaza el dominio del sitio), estilo y alcance por prompt.
- **Memoria**: la conversación vive en el historial del navegador; sin datos personales, sin perfiles.

## Estructura

```
├── backend/app/        # FastAPI: main, config, agent, rag, guardrails, llm
├── scraper/            # scrape_site.py (BFS), build_kb.py, extract_brand.py
├── frontend/           # index.html (chat tematizable), nginx.conf, assets/ (generado)
├── data/               # scrape.json, knowledge_base.json, site.json (generado)
├── docker-compose.yml  # backend + frontend
└── aura.sh             # init / scrape / dev / up / down / scrape-again
```

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `SITE_URL` | — | URL del sitio a copilotear (origen del crawler) |
| `ASSISTANT_NAME` | `Aura` | Nombre del copiloto |
| `ZAI_API_KEY` | — | Clave de la API del LLM |
| `ZAI_BASE_URL` | `https://api.z.ai/api/coding/paas/v4` | Endpoint compatible OpenAI (`LLM_BASE_URL` como alias) |
| `ZAI_MODEL` | `glm-5.1` | Modelo del fallback tool-calling (`LLM_MODEL` como alias) |
| `PI_MODEL` | `glm-5.3-flash` | Modelo del agente pi (debe existir en el endpoint) |
| `ALLOWED_HOSTS` | — | Dominios extra permitidos en respuestas (csv) |

Las credenciales se leen de `.env`. Si las tienes en `.tool-box` (`BASE_URL`,
`API_KEY`, `MODEL`), genera el `.env` con `./aura.sh env` (ambos archivos
están ignorados por git).

---

**Aviso**: el copiloto responde con la información publicada en el sitio configurado al momento del scrapeo. Para datos críticos (precios, plazos, normativas) siempre validar en la fuente oficial.
