"""
Agente copiloto del sitio.

Motor principal: PI (github.com/earendil-works/pi) — agente ligero Node/npm
dentro del contenedor backend, invocado en modo print one-shot (`pi -p`) con
herramientas restringidas a LECTURA (read, grep, glob) sobre la base de
conocimiento del sitio; pi planifica sus pasos y responde solo con lo que
encuentra en los datos scrapeados.

Fallback: si pi falla (timeout, arranque, API), se usa el loop propio de
tool-calling (listar_paginas, buscar_info, obtener_pagina) que consulta la
misma base.

Los guardrails de entrada/salida se aplican fuera (main.py), sea cual sea
el motor que responda. El system prompt se construye dinámicamente desde
app.config: cada instancia es el copiloto de UN sitio.
"""

import json
import logging
import os
import re
import subprocess
import time

import requests

from . import config, live_api, live_tools, mcp_client, rag
from .llm import API_BASE, API_KEY, MODEL, TIMEOUT

logger = logging.getLogger("aura-agent")

MAX_ROUNDS = 6
MAX_TOOL_CHARS = 3500

# --- Motor pi ---
PI_BIN = os.environ.get("PI_BIN", "pi")
PI_MODEL = os.environ.get("PI_MODEL", "glm-5.3-flash")
PI_PROVIDER = os.environ.get("PI_PROVIDER", "zai")
PI_TIMEOUT = int(os.environ.get("PI_TIMEOUT", "120"))
PI_ENABLED = os.environ.get("PI_ENABLED", "1") == "1"
KB_DIR = "/srv"  # cwd del subproceso: data/ queda accesible para sus herramientas


def _system_prompt() -> str:
    desc = f"DESCRIPCIÓN DEL SITIO:\n{config.SITE_DESCRIPTION}\n\n" if config.SITE_DESCRIPTION else ""
    return f"""Eres "{config.ASSISTANT_NAME}", el copiloto virtual de {config.SITE_NAME} ({config.SITE_HOME}), atendiendo el chat del sitio para sus usuarios.

{desc}TU CONOCIMIENTO (archivos locales extraídos de {config.SITE_DOMAIN}, en este directorio):
- data/knowledge_base.json — información estructurada del sitio: registros con pagina (título de la página a la que pertenece), tipo (sitio/pagina/seccion), titulo, texto y url. Para buscar usa grep sobre el JSON (ej: grep -i "tema" data/knowledge_base.json) y lee los registros que correspondan con read. NUNCA inventes datos: todo debe salir de estos archivos.

ALCANCE (OBLIGATORIO):
1. Solo respondes sobre {config.SITE_NAME} y los contenidos de {config.SITE_DOMAIN}. Los temas principales del sitio:
{config.temas_para_prompt()}
2. No des consejos legales, médicos, psicológicos ni financieros personalizados.
3. NUNCA pidas datos personales al usuario: ni nombre, apellido, email, teléfono, RUT, dirección, claves ni datos de tarjetas. Si el usuario te los entrega espontáneamente, no los uses ni los repitas; deriva trámites a {config.CONTACT_URL}. Este chat es público y anónimo.
4. Si la información no está en los archivos, dilo con honestidad y deriva a {config.CONTACT_URL}.

ESTILO:
- Español, tuteo, cálido, máximo ~180 palabras (salvo comparaciones).
- Háblale SIEMPRE en PRIMERA PERSONA: "yo te recomiendo", "encontré esta información", "puedo ayudarte". Nunca te refieras a ti en tercera persona ("{config.ASSISTANT_NAME} recomienda", "el asistente sugiere") ni uses un "nosotros" institucional: prohíbete decir "tenemos", "nuestra empresa", "nuestra base", "te contamos" — en su lugar: "{config.SITE_NAME} ofrece", "encontré en la página de...", "te cuento". Nunca te presentes como empleado: eres {config.ASSISTANT_NAME}, un agente que orienta con la información publicada en {config.SITE_DOMAIN}.
- Viñetas claras; URLs completas visibles; sin markdown de encabezados ni enlaces [texto](url).
- Cierra con el siguiente paso (página relevante o {config.CONTACT_URL}).
- Entrega SOLO el mensaje final para el usuario, sin narrar tus pasos.
"""


def _run_pi(user_message: str, history):
    """Ejecuta pi -p (one-shot: texto final del asistente a stdout)."""
    hist_txt = ""
    if history:
        lineas = [f"{h.role.upper()}: {h.content[:600]}" for h in history[-6:]]
        hist_txt = "HISTORIAL RECIENTE DE LA CONVERSACIÓN:\n" + "\n".join(lineas) + "\n\n"
    prompt = hist_txt + f"MENSAJE DEL USUARIO: {user_message}"

    proc = subprocess.run(
        [PI_BIN, "-p", prompt,
         "--provider", PI_PROVIDER,
         "--model", PI_MODEL,
         "--system-prompt", _system_prompt(),
         "--tools", "read,grep,glob"],
        cwd=KB_DIR,
        capture_output=True,
        text=True,
        timeout=PI_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"pi exit {proc.returncode}: {proc.stderr[-400:]}")
    salida = proc.stdout.strip()
    if not salida:
        raise RuntimeError("pi devolvió stdout vacío")
    return salida


def _urls_de_texto(texto: str, n: int = 3):
    urls = []
    for m in re.finditer(r"https?://[^\s\)\]\"']+", texto):
        u = m.group(0).rstrip(".,;:")
        host = re.sub(r"^https?://", "", u).split("/")[0].lower()
        host_base = host.rsplit("@", 1)[-1].split(":", 1)[0]  # sin userinfo ni puerto
        permitido = any(host == h or host_base == h or host_base.endswith("." + h)
                        for h in config.ALLOWED_URL_HOSTS)
        if permitido and u not in urls:
            urls.append(u)
        if len(urls) >= n:
            break
    return urls


class AgentResult:
    def __init__(self, reply, fuentes, pasos, usados, motor="toolloop"):
        self.reply, self.fuentes, self.pasos, self.herramientas_usadas = reply, fuentes, pasos, usados
        self.motor = motor


def run_agent(user_message: str, history: list) -> AgentResult:
    """Punto de entrada: pi primero, loop propio como fallback."""
    if PI_ENABLED:
        try:
            inicio = time.time()
            salida = _run_pi(user_message, history)
            logger.info("pi respondió en %.1fs (%d chars)", time.time() - inicio, len(salida))
            fuentes = [{"pagina": config.SITE_NAME, "titulo": "", "url": u} for u in _urls_de_texto(salida)]
            return AgentResult(salida, fuentes, 1, ["pi"], "pi")
        except Exception:
            logger.exception("motor pi falló; usando fallback tool-calling")
    return _run_toolloop(user_message, history)


# ---------------------------------------------------------------------------
# Loop propio de tool-calling (fallback)
# ---------------------------------------------------------------------------

AGENT_PROMPT_EXTRA = """

## CÓMO TRABAJAS (AGENTE CON HERRAMIENTAS)
- Tienes herramientas con la información del sitio {dominio}. Antes de responder cualquier pregunta factual, CONSÚLITALAS; no respondas desde memoria ni inventes.
- Puedes llamar varias herramientas en secuencia. Planifica y ejecuta cuantos pasos necesites.
- Si las herramientas no traen la información, dilo con honestidad y deriva a {contacto}. NUNCA inventes datos, cifras, fechas ni URLs.
- Cita las URLs exactas que te devuelven las herramientas (no inventes URLs).
- Cuando la respuesta compare varias categorías o períodos con cifras (meses, estados de pago, contrapartes, top clientes), preséntala en TABLA markdown (encabezado `| A | B |` y filas `| x | y |`): la interfaz la renderiza como tabla y es mucho más fácil de comparar que una lista.
"""


def _agent_prompt() -> str:
    prompt = _system_prompt() + AGENT_PROMPT_EXTRA.format(
        dominio=config.SITE_DOMAIN, contacto=config.CONTACT_URL)
    if _HAY_DATOS_EN_VIVO:
        prompt += _LIVE_PROMPT
    return prompt


TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "listar_paginas",
            "description": f"Lista las páginas principales de {config.SITE_DOMAIN} con su descripción. Úsala para '¿qué hay en este sitio?', '¿qué páginas tienen?', '¿qué ofrecen?'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_info",
            "description": "Busca información en la base de conocimiento por concepto o pregunta (ej: 'precios', 'horarios', 'cómo postular', 'garantía', 'webhooks'). Cubre las páginas del sitio y los manuales de usuario. Devuelve las mejores coincidencias con extracto y URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "consulta": {"type": "string", "description": "Concepto o pregunta a buscar"},
                },
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "obtener_pagina",
            "description": "Contenido completo de una página del sitio por su título o ruta (ej: 'Contacto', '/precios'). Usa listar_paginas antes si no estás seguro del nombre exacto.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pagina": {"type": "string", "description": "Título o ruta de la página"},
                },
                "required": ["pagina"],
            },
        },
    },
]


def _cortar(texto: str) -> str:
    return texto[:MAX_TOOL_CHARS] + ("\n…[truncado]" if len(texto) > MAX_TOOL_CHARS else "")


def tool_listar_paginas(_) -> str:
    ret = rag.get_retriever()
    paginas = ret.list_pages()
    lineas = [f"TOTAL: {len(paginas)} páginas principales en {config.SITE_DOMAIN}."]
    for pg in paginas:
        d = f" — {pg['description'][:110]}" if pg["description"] else ""
        lineas.append(f"- {pg['titulo']} ({pg['path']}){d}\n  URL: {pg['url']}")
    return _cortar("\n".join(lineas))


def tool_buscar_info(args) -> str:
    consulta = (args.get("consulta") or "").strip()
    if not consulta:
        return "Error: 'consulta' es obligatoria."
    ret = rag.get_retriever()
    docs = ret.search(consulta, top_k=6, min_score=1.0)
    if not docs:
        return ("Sin resultados en la base de conocimiento. Sugiere reformular o "
                f"deriva a {config.CONTACT_URL}.")
    out = []
    for d in docs:
        out.append(f"[{d['pagina']}] {d['titulo']}\nURL: {d['url']}\n{d['texto'][:700]}")
    return _cortar("\n\n".join(out))


def tool_obtener_pagina(args) -> str:
    nombre = (args.get("pagina") or "").strip()
    ret = rag.get_retriever()
    pagina = ret.get_page(nombre)
    if not pagina:
        return f"No encontré la página '{nombre}'."
    secs = ret.page_sections(nombre, top_k=6)
    partes = [f"PÁGINA: {pagina['pagina']}\nURL: {pagina['url']}\n{pagina['texto'][:1200]}"]
    for s in secs:
        partes.append(f"[{s['titulo']}]\n{s['texto'][:1100]}")
    return _cortar("\n\n".join(partes))


TOOL_IMPL = {
    "listar_paginas": tool_listar_paginas,
    "buscar_info": tool_buscar_info,
    "obtener_pagina": tool_obtener_pagina,
}


# ---------------------------------------------------------------------------
# Herramientas externas (datos en vivo): proveedores declarativos
# ---------------------------------------------------------------------------

def _tool_live(fn):
    """Envuelve una tool de datos en vivo: traduce errores a mensajes
    honestos para el modelo (nunca rompe el loop)."""
    def wrapper(args):
        try:
            return fn(args or {})
        except live_api.NoAutenticado as e:
            return (f"No hay cuenta conectada o la sesión expiró: {e}. "
                    "Informa al usuario que puede conectar su cuenta para "
                    "consultar datos en vivo; mientras tanto responde con la documentación.")
        except live_api.SinPermiso as e:
            return (f"La cuenta conectada no tiene permiso para ese dato: {e}. "
                    "Comunícalo al usuario tal cual; sugiere pedir el acceso a un administrador.")
        except live_api.LiveError as e:
            return f"Fallo consultando los datos en vivo: {e}."
        except Exception:  # noqa: BLE001 - la tool nunca debe tumbar el loop
            logger.exception("tool de datos en vivo falló")
            return "No pude consultar los datos en vivo en este momento."
    return wrapper


_LIVE_PROMPT = """

## DATOS EN VIVO (SOLO LECTURA, CON LA CUENTA DEL USUARIO)
- Tienes herramientas que consultan los datos reales del sistema con la cuenta del usuario que chatea. Para CUALQUIER pregunta sobre cifras, conteos, estados o registros actuales ("¿cuántas pendientes hay?", "¿por qué veo este número?", "muéstrame las de error"), ÚSALAS primero; jamás des cifras desde la memoria.
- Las herramientas pueden indicar que no hay cuenta conectada o que faltan permisos: comunícalo tal cual y ofrece la alternativa (conectar la cuenta, o pedir el permiso a un administrador).
- Presenta las cifras como datos en vivo consultados con su cuenta. NUNCA inventes números, clientes ni motivos.
"""


def _registrar_externas() -> bool:
    """Registra las tools de los proveedores declarativos: manifest REST
    (live_tools.yaml) y servidores MCP (mcp_servers.json). Devuelve True
    si quedó alguna disponible para el agente."""
    fuentes = []
    for proveedor, cargar in (("live_tools", live_tools.cargar),
                              ("mcp", mcp_client.herramientas)):
        try:
            fuentes.extend(cargar())
        except Exception:  # noqa: BLE001 - un proveedor malo no tumba el boot
            logger.exception("proveedor %s falló", proveedor)
    for spec, impl in fuentes:
        nombre = spec["function"]["name"]
        if nombre in TOOL_IMPL:
            logger.warning("tool duplicada, se omite: %s", nombre)
            continue
        TOOLS_SPEC.append(spec)
        TOOL_IMPL[nombre] = _tool_live(impl)
    return bool(fuentes)


_HAY_DATOS_EN_VIVO = _registrar_externas()


def _run_toolloop(user_message: str, history: list) -> AgentResult:
    """Fallback: loop propio de tool-calling contra la misma base."""
    messages = [{"role": "system", "content": _agent_prompt()}]
    for h in history:
        messages.append({"role": h.role, "content": h.content[:1500]})
    messages.append({"role": "user", "content": user_message})

    fuentes = {}
    usados = []
    pasos = 0

    for ronda in range(MAX_ROUNDS):
        pasos = ronda + 1
        resp = requests.post(
            f"{API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={
                "model": MODEL,
                "messages": messages,
                "tools": TOOLS_SPEC,
                "max_tokens": 1600,
                "temperature": 0.35,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        msg = resp.json()["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            contenido = (msg.get("content") or "").strip()
            if not contenido and ronda < MAX_ROUNDS - 1:
                # Modelos razonadores agotan max_tokens pensando y devuelven
                # content vacío: se pide la síntesis final una vez más.
                messages.append({"role": "user", "content":
                    "Redacta ahora SOLO la respuesta final para el usuario, "
                    "concisa (máx. ~180 palabras), con lo que encontraste."})
                continue
            return AgentResult(contenido, list(fuentes.values()), pasos, usados, "toolloop")

        messages.append({
            "role": "assistant",
            "content": msg.get("content") or "",
            "tool_calls": tool_calls,
        })

        for tc in tool_calls:
            fn = tc["function"]["name"]
            usados.append(fn)
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            impl = TOOL_IMPL.get(fn)
            resultado = impl(args) if impl else f"Error: herramienta desconocida {fn}"
            if fn in ("buscar_info", "obtener_pagina"):
                primera_url = _extraer_url(resultado)
                if primera_url:
                    nombre = _extraer_pagina(resultado) or fn
                    fuentes.setdefault(primera_url, {"url": primera_url, "pagina": nombre, "titulo": nombre})
            logger.info("tool %s(%s) -> %d chars", fn, str(args)[:60], len(resultado))
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": resultado,
            })

    # se agotaron las rondas: forzar respuesta final sin herramientas.
    # Modelos razonadores pueden devolver content vacío si max_tokens no
    # alcanza para pensar + redactar: se reintenta una vez con más tokens.
    messages.append({"role": "user", "content": "Responde ahora con lo que tienes (sin más llamadas a herramientas)."})
    contenido = ""
    for intento in range(2):
        resp = requests.post(
            f"{API_BASE}/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json={"model": MODEL, "messages": messages,
                  "max_tokens": 900 if intento == 0 else 3000, "temperature": 0.35},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        contenido = (resp.json()["choices"][0]["message"].get("content") or "").strip()
        if contenido:
            break
        logger.info("respuesta forzada vacía (intento %d); reintentando con más tokens", intento + 1)
    return AgentResult(contenido, list(fuentes.values()), pasos, usados, "toolloop")


def _extraer_url(texto: str):
    m = re.search(r"URL: (https?://[^\s]+)", texto)
    return m.group(1).rstrip(".") if m else None


def _extraer_pagina(texto: str):
    """Nombre de la página citada en un resultado de herramienta."""
    m = re.search(r"PÁGINA: (.+)", texto) or re.search(r"^\[([^\]\n]{3,80})\]", texto, re.M)
    return m.group(1).strip() if m else None
