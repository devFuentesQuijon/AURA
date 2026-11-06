"""
Guardrails del copiloto.

Dos capas:
  1. Programática (determinista, pre y post LLM):
     - límite de longitud y rate limit por IP
     - bloqueo de contenido dañino / señales de crisis
     - derivación de temas genéricos no relacionados con el sitio
     - sanitización de URLs en la respuesta (solo dominio del sitio)
  2. De prompt (agent.py): identidad, alcance y estilo.

La identidad (nombre del sitio, dominio, vocabulario) viene de app.config.
Política de derivación: los temas sensibles (crisis) se responden con
derivación a canales oficiales, no con silencio.
"""

import re
from collections import defaultdict, deque
import time

from fastapi import HTTPException

from . import config

MAX_MESSAGE_CHARS = 2000
MAX_HISTORY_TURNS = 6  # tandas usuario+asistente que se conservan

# --- Temas bloqueados en seco (seguridad / ilegal) ---
HARD_BLOCK_PATTERNS = [
    r"\b(porno|pornografia|contenido erotico)\b",
    r"\b(como (fabricar|conseguir|obtener) (arma|explosivo|droga|bomb[aí]))\b",
    r"\b(hackear|robar (credenciales|identidad)|phishing)\b",
]

# --- Temas genéricos fuera de alcance -> derivación amable ---
OFF_TOPIC_PATTERNS = [
    r"\b(politic[ao]s? (chilen[oa]s?)|elecciones|candidat[oa]s?)\b",
    r"\b(futbol|club (de )?futbol|partido de futbol)\b",
    r"\b(horoscopo|signo zodiacal)\b",
    r"\b(criptomonedas?|inversiones? en bolsa|forex)\b",
    r"\b(noticias? de (hoy|actualidad|ultima hora))\b",
]

# Saludo puro (mensaje completo) -> bienvenida sin gastar LLM
GREETING_RE = re.compile(r"^(hola|buen(os|as) (d[ií]as|tardes|noches)|qué tal|que tal|hey|hi|buenas|saludos)[\s!¡.,¿?]*$")

CRISIS_WORDS = (
    "suicid", "quitarme la vida", "quitar la vida", "quitarme de",
    "no quiero vivir", "no quiero seguir viv", "no le encuentro sentido a la vida",
    "lastimarme", "autolesion", "matarme",
)

CRISIS_DERIVACION = (
    "Entiendo que puedes estar pasando por un momento difícil. No estoy capacitado "
    "para ayudarte con esto, pero es importante que hables con alguien ahora:\n"
    "• Línea de Salud Mental Responde (Minsal): 600 360 7777\n"
    "• Salud Responde: 600 360 7777 (opción 1)\n"
    "Si es una emergencia, acude al servicio de urgencia más cercano. "
    f"Si tu consulta es sobre {config.SITE_NAME}, con gusto te ayudo."
)

DERIVACION_ESTANDAR = (
    "Lo siento, ese tema está fuera de mi alcance. "
    f"Soy {config.ASSISTANT_NAME}, el copiloto de {config.SITE_NAME}, y solo puedo "
    f"ayudarte con la información publicada en {config.SITE_DOMAIN}. "
    f"Para otras consultas puedes escribir a través de {config.CONTACT_URL}."
)

BIENVENIDA = config.bienvenida()

# Rate limit simple en memoria (por IP): N mensajes por ventana
RATE_LIMIT_MAX = 20
RATE_LIMIT_WINDOW = 60  # segundos


class RateLimiter:
    def __init__(self):
        self.hits = defaultdict(deque)

    def check(self, key: str):
        now = time.time()
        q = self.hits[key]
        while q and now - q[0] > RATE_LIMIT_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT_MAX:
            raise HTTPException(status_code=429, detail="Has enviado muchos mensajes en poco tiempo. Espera un momento e inténtalo de nuevo.")
        q.append(now)


rate_limiter = RateLimiter()


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


def _sin_tildes(s: str) -> str:
    return (s.replace("á", "a").replace("é", "e").replace("í", "i")
             .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))


def classify_input(message: str) -> str:
    """Devuelve 'ok' | 'block' | 'crisis' | 'offtopic' | 'greeting'."""
    text = _norm(message)
    sin = _sin_tildes(text)
    if GREETING_RE.match(sin):
        return "greeting"
    for pat in HARD_BLOCK_PATTERNS:
        if re.search(pat, sin):
            return "block"
    if any(w in sin for w in CRISIS_WORDS):
        return "crisis"
    # offtopic solo si el mensaje no toca el vocabulario del sitio
    if config.SITE_KEYWORDS and not any(k in sin for k in config.SITE_KEYWORDS):
        for pat in OFF_TOPIC_PATTERNS:
            if re.search(pat, sin):
                return "offtopic"
    elif not config.SITE_KEYWORDS:
        for pat in OFF_TOPIC_PATTERNS:
            if re.search(pat, sin):
                return "offtopic"
    return "ok"


def sanitize_output(text: str) -> str:
    """Elimina/reemplaza URLs hacia dominios no permitidos."""
    def repl(m):
        url = m.group(0)
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        host_base = host.rsplit("@", 1)[-1].split(":", 1)[0]  # sin userinfo ni puerto
        permitido = any(host == h or host_base == h or host_base.endswith("." + h)
                        for h in config.ALLOWED_URL_HOSTS)
        return url if permitido else "[enlace no disponible]"
    return re.sub(r"https?://[^\s\)\]\"']+", repl, text)


def validate_reply(reply: str) -> bool:
    """Chequeos mínimos de la respuesta generada."""
    if not reply or len(reply) > 6000:
        return False
    return True
