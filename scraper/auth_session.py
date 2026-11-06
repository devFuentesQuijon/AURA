#!/usr/bin/env python3
"""
Login para sitios con autenticación (CHAT-AURA).

Tres modos:

  auth_session.py            # one-shot: abre Chrome, espera login (timeout)
  auth_session.py open       # abre Chrome y LO DEJA ABIERTO (sin timeout)
  auth_session.py save       # guarda la sesión del Chrome abierto y lo cierra

El flujo open/save quita la presión de tiempo: el navegador queda abierto
mientras la persona inicia sesión (SSO/Microsoft/2FA incluido, en minutos u
horas), y al final se ejecuta `save` para capturar el estado.

El estado (cookies + localStorage) se guarda en data/auth_state.json y lo
usa scrape_site.py. El archivo es secreto (.gitignore, chmod 600).

Uso típico:
  ./aura.sh login            # one-shot interactivo
  ./aura.sh login-open       # abrir navegador (queda abierto)
  ./aura.sh login-save       # capturar sesión y cerrar
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "data" / "auth_state.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

LOGIN_RE = re.compile(r"/(auth|login|signin|session)(/|$)", re.I)
CDP_PORT = int(os.environ.get("AUTH_CDP_PORT", "9222"))


def en_login(page) -> bool:
    try:
        if LOGIN_RE.search(urlparse(page.url).path):
            return True
        return bool(page.evaluate("!!document.querySelector('input[type=password]')"))
    except Exception:
        return False


def _lanzar(p, inicio: str):
    browser = p.chromium.launch(
        channel="chrome",
        headless=False,
        args=["--disable-blink-features=AutomationControlled",
              "--window-position=2000,2000",
              f"--remote-debugging-port={CDP_PORT}"],
    )
    ctx = browser.new_context(user_agent=UA, locale="es-CL", viewport={"width": 1366, "height": 900})
    ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = ctx.new_page()
    page.goto(inicio, wait_until="domcontentloaded", timeout=60000)
    return browser, ctx, page


def _guardar(ctx, ruta: str):
    estado = ctx.storage_state()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    Path(ruta).write_text(json.dumps(estado, ensure_ascii=False), encoding="utf-8")
    os.chmod(ruta, 0o600)
    return len(estado.get("cookies", []))


def modo_oneshot(inicio: str, timeout: int, ruta: str):
    print(f"Abriendo {inicio} en Chrome…", flush=True)
    print("Inicia sesión en la ventana del navegador (SSO/2FA incluido).", flush=True)
    with sync_playwright() as p:
        browser, ctx, page = _lanzar(p, inicio)
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                if not en_login(page):
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            browser.close()
            sys.exit(f"Tiempo agotado ({timeout}s) sin detectar sesión. "
                     f"Prueba con './aura.sh login-open' + 'login-save'.")
        n = _guardar(ctx, ruta)
        browser.close()
    print(f"OK -> sesión guardada en {ruta} ({n} cookies)", flush=True)
    print("Ahora ejecuta: ./aura.sh scrape", flush=True)


def modo_open(inicio: str):
    print(f"Abriendo {inicio} en Chrome (queda abierto; puerto CDP {CDP_PORT})…", flush=True)
    print("Inicia sesión cuando quieras. Al terminar: ./aura.sh login-save", flush=True)
    with sync_playwright() as p:
        browser, ctx, page = _lanzar(p, inicio)
        try:
            page.wait_for_event("close", timeout=0)   # vive hasta cerrar la pestaña
        except Exception:
            pass
        try:
            n = _guardar(ctx, str(OUT))
            print(f"OK -> sesión guardada en {OUT} ({n} cookies)", flush=True)
        except Exception as e:
            print(f"No se pudo guardar la sesión automáticamente: {e}. "
                  f"Ejecuta './aura.sh login-save'.", flush=True)
        browser.close()


def modo_save(ruta: str):
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{CDP_PORT}")
        except Exception:
            sys.exit(f"No hay navegador en puerto CDP {CDP_PORT}. "
                     f"Ejecuta antes './aura.sh login-open'.")
        ctxs = browser.contexts
        if not ctxs:
            browser.close()
            sys.exit("El navegador no tiene contextos abiertos.")
        n = _guardar(ctxs[0], ruta)
        browser.close()
    print(f"OK -> sesión guardada en {ruta} ({n} cookies)", flush=True)
    print("Ahora ejecuta: ./aura.sh scrape", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("modo", nargs="?", default="oneshot",
                    choices=["oneshot", "open", "save"])
    ap.add_argument("--url", default="", help="URL del sitio (default: env SITE_URL)")
    ap.add_argument("--timeout", type=int, default=300, help="(oneshot) segundos máx.")
    ap.add_argument("--state", default=str(OUT), help="ruta del estado de sesión")
    args = ap.parse_args()

    if args.modo == "save":
        modo_save(args.state)
        return

    inicio = (args.url or os.environ.get("SITE_URL", "")).strip()
    if not inicio:
        sys.exit("Indica --url o define SITE_URL en .env")

    if args.modo == "open":
        modo_open(inicio)
    else:
        modo_oneshot(inicio, args.timeout, args.state)


if __name__ == "__main__":
    main()
