"""
Transporte de datos en vivo: reenvía llamadas a la API REST del sitio con
el JWT del usuario que conversa (header X-User-Token → Bearer).

La API del sitio aplica los permisos del usuario (401 sin sesión válida,
403 sin el permiso granular): este módulo NO decide qué se puede ver, solo
traduce errores y ofrece topes de salida. Las herramientas que lo usan se
definen de forma declarativa por proveedores (ver live_tools.py y
mcp_client.py).
"""

import logging
import os
import threading

import requests

logger = logging.getLogger("aura-live")

BASE = os.environ.get("LIVE_API_BASE_URL", "").rstrip("/")
TIMEOUT = int(os.environ.get("LIVE_API_TIMEOUT", "15"))
# Raíz web del sitio (para enlaces de referencia en las salidas de tools);
# asume que la API cuelga de <web>/api/v1.
WEB_BASE = BASE[:-len("/api/v1")] if BASE.endswith("/api/v1") else BASE

_ENABLED = os.environ.get("LIVE_API_ENABLED", "1") == "1"

MAX_CHARS_SALIDA = 3500  # tope total de caracteres por salida de tool


class LiveError(Exception):
    """Fallo genérico consultando la API en vivo."""


class NoAutenticado(LiveError):
    """Sin token o token expirado/rechazado (401)."""


class SinPermiso(LiveError):
    """El usuario no tiene el permiso granular para ese recurso (403)."""


class NoDisponible(LiveError):
    """La instancia no tiene LIVE_API_BASE_URL configurado."""


_local = threading.local()


def set_user_token(token: str) -> None:
    _local.token = (token or "").strip()


def get_user_token() -> str:
    return getattr(_local, "token", "")


def habilitada() -> bool:
    return _ENABLED and bool(BASE)


def _request(method: str, path: str, params: dict | None = None,
             json: dict | None = None) -> dict | list:
    if not BASE:
        raise NoDisponible("Datos en vivo no configurados en esta instancia")
    token = get_user_token()
    if not token:
        raise NoAutenticado("No hay una cuenta conectada en este chat")
    try:
        r = requests.request(method, f"{BASE}{path}", params=params, json=json,
                             headers={"Authorization": f"Bearer {token}"},
                             timeout=TIMEOUT)
    except requests.RequestException as e:
        logger.warning("live_api %s %s falló: %s", method, path, type(e).__name__)
        raise LiveError("No pude consultar los datos en vivo en este momento")
    if r.status_code == 401:
        raise NoAutenticado("Tu sesión expiró o no es válida; vuelve a conectar tu cuenta")
    if r.status_code == 403:
        detalle = ""
        try:
            detalle = str(r.json().get("detail", ""))
        except Exception:
            pass
        raise SinPermiso(detalle or "Tu usuario no tiene permiso para ver esos datos")
    if r.status_code >= 400:
        logger.warning("live_api %s %s -> HTTP %s", method, path, r.status_code)
        raise LiveError(f"La API respondió con un error ({r.status_code})")
    return r.json()


def _cortar(texto: str) -> str:
    if len(texto) > MAX_CHARS_SALIDA:
        return texto[:MAX_CHARS_SALIDA] + "\n…[truncado]"
    return texto
