"""
Configuración de la instancia CHAT-AURA.

Cada despliegue es el copiloto de UN sitio web. La identidad se resuelve en
este orden:
  1. Variables de entorno: SITE_URL (obligatorio para scrapear),
     ASSISTANT_NAME (default "Aura").
  2. data/site.json — generado por scraper/scrape_site.py + build_kb.py
     (nombre real del sitio, descripción, dominio, página de contacto,
     vocabulario y temas para el alcance).

Si data/site.json no existe (aún no se scrapea), se deriva una identidad
mínima desde SITE_URL para que la API pueda arrancar.
"""

import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

def _buscar(rel: str):
    """Resuelve rel en la raíz del repo o en la del paquete (Docker /srv)."""
    aqui = Path(__file__).resolve()
    for base in (aqui.parent.parent.parent, aqui.parent.parent):
        p = base / rel
        if p.exists():
            return p
    return aqui.parent.parent.parent / rel


SITE_JSON_PATH = _buscar("data/site.json")
BRAND_JSON_PATH = _buscar("frontend/assets/brand.json")

ASSISTANT_NAME = (os.environ.get("ASSISTANT_NAME", "Aura").strip() or "Aura")
SITE_URL = os.environ.get("SITE_URL", "").strip().rstrip("/")

_lang_hint = os.environ.get("SITE_LANG", "es-CL")


def _dominio_de(url: str) -> str:
    host = (urlparse(url).netloc or url or "sitio").lower().split(":")[0]
    partes = host.split(".")
    return ".".join(partes[-2:]) if len(partes) >= 2 else host


def _cargar() -> dict:
    if SITE_JSON_PATH.exists():
        try:
            cfg = json.loads(SITE_JSON_PATH.read_text(encoding="utf-8"))
            if cfg.get("url"):
                return cfg
        except Exception:
            pass
    url = SITE_URL or "https://sitio.sin-configurar"
    dominio = _dominio_de(url)
    return {
        "url": url,
        "domain": dominio,
        "name": dominio,
        "description": "",
        "contact_url": url,
        "keywords": [],
        "topics": [],
        "n_pages": 0,
    }


SITE = _cargar()

SITE_NAME = (os.environ.get("SITE_NAME") or SITE.get("name") or SITE["domain"]).strip()
SITE_DOMAIN = (SITE.get("domain") or _dominio_de(SITE_URL or SITE["url"])).lower()
SITE_HOME = (SITE.get("url") or SITE_URL).rstrip("/")
CONTACT_URL = (SITE.get("contact_url") or SITE_HOME).rstrip("/")
SITE_DESCRIPTION = (SITE.get("description") or "").strip()
SITE_KEYWORDS = [k.lower() for k in SITE.get("keywords", []) if isinstance(k, str)]
TOPICS = SITE.get("topics", []) if isinstance(SITE.get("topics", []), list) else []

# Dominios cuyas URLs se aceptan en las respuestas (sanitize_output).
# El chequeo es host == h o host.endswith("." + h): con el dominio
# registrable alcanza para cubrir www. y subdominios del sitio.
EXTRA_HOSTS = tuple(h.strip().lower() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip())
ALLOWED_URL_HOSTS = (SITE_DOMAIN,) + EXTRA_HOSTS


def brand() -> dict:
    """brand.json del frontend (paleta/logo/favicon); {} si aún no se genera."""
    rutas = [os.environ.get("BRAND_JSON", "").strip(),
             "frontend/assets/brand.json", "brand_assets/brand.json"]
    for r in rutas:
        if not r:
            continue
        p = Path(r) if Path(r).is_absolute() else _buscar(r)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def temas_para_prompt(n: int = 10) -> str:
    """Lista legible de los temas principales del sitio (alcance del agente)."""
    temas = []
    for t in TOPICS[:n]:
        if isinstance(t, dict) and t.get("tema"):
            temas.append(f"{t['tema']} ({t.get('pagina', '')})".strip())
    return "\n".join(f"- {t}" for t in temas[:n]) or "- Contenidos generales del sitio"


def sugerencias_iniciales(n: int = 3) -> list:
    """Sugerencias para el arranque del chat (frontend)."""
    out = []
    for t in TOPICS:
        if isinstance(t, dict) and t.get("tema"):
            out.append(f"¿Qué puedes contarme sobre {t['tema'].lower()}?")
        if len(out) >= n:
            break
    if not out:
        out = [f"¿Qué información hay en {SITE_NAME}?",
               f"¿Cómo contacto a {SITE_NAME}?"]
    return out


def bienvenida() -> str:
    partes = [f"¡Hola! 👋 Soy {ASSISTANT_NAME}, el copiloto virtual de {SITE_NAME}."]
    if SITE_DESCRIPTION:
        partes.append(SITE_DESCRIPTION)
    partes.append(
        f"Puedo responder tus preguntas con la información publicada en {SITE_DOMAIN}: "
        f"servicios, productos, precios, horarios, trámites, contacto y más. "
        f"¿Qué necesitas saber?"
    )
    return "\n\n".join(partes)
