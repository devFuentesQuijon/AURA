#!/usr/bin/env python3
"""
Scraper genérico de sitios web para CHAT-AURA.

Navega el sitio como un usuario real (Playwright + Chrome del sistema,
modo headed) descubriendo enlaces internos desde la página de inicio
(BFS acotado por profundidad y cantidad de páginas), y extrae el
contenido por secciones (encabezados h1-h4 agrupan el texto de nodos
hoja; se omite nav/header/footer/aside).

Salida:
  data/scrape.json — {meta, pages: [{url, path, title, description, h1,
                     sections, links}]}
  data/site.json   — identidad del sitio (nombre, dominio, descripción,
                     página de contacto, índice de páginas). build_kb.py
                     le agrega keywords y topics.

Política de cortesía: pausa aleatoria entre páginas, User-Agent de
navegador real, sin query-strings (evita trampas de crawler), solo el
mismo dominio registrable (ej: www.ejemplo.cl y tienda.ejemplo.cl).

Uso:
  python scraper/scrape_site.py --url https://ejemplo.cl \
      --max-pages 60 --max-depth 3
"""

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent.parent
OUT_SCRAPE = BASE / "data" / "scrape.json"
OUT_SITE = BASE / "data" / "site.json"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

# Recursos y rutas que no aportan contenido para el copiloto
SKIP_EXT = re.compile(
    r"\.(pdf|zip|jpe?g|png|gif|webp|svg|mp[34]|webm|avi|mov|xml|json|txt|"
    r"csv|xls[x]?|doc[x]?|ppt[x]?|epub|ico|css|js)(\?|#|$)", re.I)
# Rutas típicas de login: si el crawler aterriza aquí, la sesión no está
# activa (o la página exige autenticación) -> se marca auth_required
LOGIN_PATH_RE = re.compile(r"/(auth|login|signin|session)(/|$)", re.I)

# Segmentos típicos de descarga/impresión: no aportan contenido al copiloto
# y suelen fallar la extracción (binarios)
SKIP_PATH = re.compile(
    r"/(wp-admin|wp-login|cart|checkout|my-account|search|feed|comments|"
    r"trackback|api|graphql|\.well-known|download|export|print|file)(/|$)", re.I)


def registrable_domain(host: str) -> str:
    """Dominio registrable aproximado (últimas 2 etiquetas).

    Cubre .cl/.com/.net/.org; suficiente para evitar salir del sitio.
    """
    host = (host or "").lower().split(":")[0]
    partes = host.split(".")
    return ".".join(partes[-2:]) if len(partes) >= 2 else host


def normalizar_url(base: str, href: str) -> Optional[str]:
    """Absolutiza, quita hash/query y descarta recursos y rutas internas."""
    if not href or href.startswith(("mailto:", "tel:", "javascript:", "data:")):
        return None
    u = urlparse(urljoin(base, href))
    if u.scheme not in ("http", "https"):
        return None
    host = u.netloc.lower()
    path = re.sub(r"/{2,}", "/", u.path or "/")
    if SKIP_EXT.search(path) or SKIP_PATH.search(path):
        return None
    if path != "/":
        path = path.rstrip("/")
    return f"{u.scheme}://{host}{path}"


# Extracción de contenido por nodos hoja: textContent (no innerText) para
# capturar acordeones/pestañas colapsadas; se omite nav/header/footer/aside.
# Recibe cfg con el prefijo del host para clasificar enlaces internos.
JS_EXTRACT = """(cfg) => {
  const clean = (s) => (s || '').replace(/[ \\t\\r\\f\\v]+/g, ' ')
    .replace(/ *\\n */g, '\\n').replace(/\\n{3,}/g, '\\n\\n').trim();
  const root = document.querySelector('main') || document.body;
  const inChrome = (n) => n.closest && n.closest('nav,header,footer,aside,[role="navigation"]');
  const leaves = [];
  for (const n of root.querySelectorAll('h1,h2,h3,h4,p,li,td,th,div')) {
    if (n.querySelector('h1,h2,h3,h4,p,li,td,th,div')) continue;  // solo hoja
    if (inChrome(n)) continue;
    const txt = clean(n.textContent);
    if (txt && txt.length > 1) leaves.push({tag: n.tagName, text: txt});
  }
  const out = [];
  let current = {title: '__intro__', text: []};
  for (const l of leaves) {
    if (/^H[1-4]$/.test(l.tag)) {
      if (current.text.length) out.push({title: current.title, text: current.text.join('\\n')});
      current = {title: l.text, text: []};
    } else {
      current.text.push(l.text);
    }
  }
  if (current.text.length) out.push({title: current.title, text: current.text.join('\\n')});
  const sections = out.filter(s => s.text.length > 30);

  const meta = (sel, attr) => {
    const m = document.querySelector(sel);
    return m ? (attr ? m.getAttribute(attr) : m.content) : '';
  };
  const links = [];
  for (const a of document.querySelectorAll('a[href]')) {
    try {
      const u = new URL(a.href, location.href);
      links.push({
        href: u.origin + u.pathname,
        nav: !!(a.closest && a.closest('nav,header,[role="navigation"]')),
      });
    } catch (e) {}
  }
  const h1 = root.querySelector('h1') && !inChrome(root.querySelector('h1'))
    ? clean(root.querySelector('h1').textContent) : '';
  return {
    h1,
    title: clean(document.title),
    description: clean(meta('meta[name="description"]') || meta('meta[property="og:description"]')),
    site_name: clean(meta('meta[property="og:site_name"]')),
    lang: document.documentElement.lang || '',
    sections,
    links,
  };
}"""


def guardar(pages: dict, inicio: str, dominio: str):
    """Escribe scrape.json + site.json con lo recolectado hasta ahora
    (se llama tras cada página para no perder trabajo si se interrumpe)."""
    ok = [pg for pg in pages.values() if not pg.get("failed") and pg.get("sections")]
    OUT_SCRAPE.parent.mkdir(parents=True, exist_ok=True)
    OUT_SCRAPE.write_text(json.dumps({
        "meta": {"fecha": datetime.now().isoformat(timespec="seconds"),
                 "inicio": inicio, "dominio": dominio},
        "pages": list(pages.values()),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    home = pages.get(inicio) or (ok[0] if ok else {})
    nombre = (home.get("site_name")
              or limpiar_titulo(home.get("title", ""), dominio)
              or dominio)
    contacto = next((u for u in pages
                     if re.search(r"/(contacto?|admision|atencion(al|_al)_cliente|soporte|ayuda|help)(/|$)",
                                  urlparse(u).path, re.I)), None)
    previo = {}
    if OUT_SITE.exists():
        try:
            previo = json.loads(OUT_SITE.read_text(encoding="utf-8"))
        except Exception:
            previo = {}
    site = {
        "url": inicio,
        "domain": dominio,
        "name": nombre,
        "description": home.get("description", ""),
        "lang": home.get("lang", ""),
        "contact_url": contacto or inicio,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
        "n_pages": len(ok),
        "pages": [{"url": pg["url"], "path": pg["path"], "title": pg["title"],
                   "description": pg.get("description", "")} for pg in ok],
    }
    site.update({k: v for k, v in previo.items() if k in ("keywords", "topics")})
    OUT_SITE.write_text(json.dumps(site, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(ok), nombre


def visit(page, url: str, retries: int = 2) -> Optional[dict]:
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_selector("body h1, body h2, main", state="attached", timeout=15000)
            page.wait_for_timeout(900)  # settling de widgets/JS
            return page.evaluate(JS_EXTRACT, {})
        except Exception as e:
            print(f"    intento {attempt + 1} falló: {type(e).__name__}: {str(e)[:100]}", flush=True)
            if attempt >= retries:
                return None
            time.sleep(5)


def limpiar_titulo(title: str, host: str) -> str:
    """'Inicio — Ejemplo SpA' -> 'Ejemplo SpA' (heurística por separadores)."""
    t = re.sub(r"\s+", " ", (title or "")).strip()
    for sep in (" — ", " – ", " — ", " | ", " · ", " « "):
        if sep in t:
            partes = [p.strip() for p in t.split(sep) if p.strip()]
            # quedarse con la parte más larga que no sea 'inicio/home/index'
            candidatas = [p for p in partes if not re.match(r"^(inicio|home|index|bienvenid)", p, re.I)]
            if candidatas:
                return max(candidatas, key=len)
    return t or host


def main():
    ap = argparse.ArgumentParser(description="Scraper genérico CHAT-AURA")
    ap.add_argument("--url", default="", help="URL de inicio (default: env SITE_URL)")
    ap.add_argument("--max-pages", type=int, default=60)
    ap.add_argument("--max-depth", type=int, default=3)
    ap.add_argument("--same-host", action="store_true",
                    help="no seguir subdominios (solo el host exacto de inicio)")
    ap.add_argument("--auth", default=str(BASE / "data" / "auth_state.json"),
                    help="estado de sesión (cookies) guardado por auth_session.py; "
                         "si no existe, se scrapea sin sesión")
    ap.add_argument("--cdp", default="", metavar="PUERTO",
                    help="conectarse por CDP a un Chrome ya logueado (ej 9222) y "
                         "scrapear DENTRO de esa sesión viva; ignora --auth. "
                         "Necesario si la app guarda la sesión en sessionStorage")
    ap.add_argument("--headless", action="store_true",
                    help="Chrome sin ventana (apto para LLM local/apps propias; "
                         "para sitios públicos con anti-bot mejor headed)")
    ap.add_argument("--skip-pattern", default="",
                    help="regex extra sobre el path para NO visitar (ej: detalles "
                         "dinámicos '/invoices/[0-9a-f-]{36}(/file)?$')")
    ap.add_argument("--delay-min", type=float, default=1.0)
    ap.add_argument("--delay-max", type=float, default=2.5)
    args = ap.parse_args()

    inicio = (args.url or __import__("os").environ.get("SITE_URL", "")).strip()
    if not inicio:
        sys.exit("Indica --url o define SITE_URL en .env")
    inicio = normalizar_url(inicio, inicio) or inicio
    p0 = urlparse(inicio)
    dominio = registrable_domain(p0.netloc)
    host_inicio = p0.netloc.lower()

    def interno(url: str) -> bool:
        host = urlparse(url).netloc.lower()
        if args.same_host:
            return host == host_inicio
        return registrable_domain(host) == dominio

    print(f"Scraping {inicio} (dominio {dominio}, máx {args.max_pages} págs, prof {args.max_depth})",
          flush=True)

    pages: dict[str, dict] = {}   # url -> datos extraídos
    cola: list[tuple[str, int]] = [(inicio, 0)]
    pendientes_nav: list[tuple[str, int]] = []

    auth_path = Path(args.auth)
    usar_auth = auth_path.exists() and not args.cdp
    patron_skip = re.compile(args.skip_pattern) if args.skip_pattern else None
    if patron_skip:
        print(f"Excluyendo paths que coincidan con: {args.skip_pattern}", flush=True)
    if usar_auth:
        print(f"Usando sesión de {auth_path.name}", flush=True)
    if args.cdp:
        print(f"Conectando por CDP al Chrome logueado (puerto {args.cdp})", flush=True)

    with sync_playwright() as p:
        if args.cdp:
            browser = p.chromium.connect_over_cdp(f"http://localhost:{args.cdp}")
            if not browser.contexts:
                sys.exit("El navegador CDP no tiene contextos abiertos.")
            ctx = browser.contexts[0]
            # Reutilizar la pestaña con la sesión: sessionStorage es por
            # pestaña, así que no sirve abrir una pestaña nueva.
            pags = [pg for pg in ctx.pages if urlparse(pg.url).netloc == p0.netloc]
            page = pags[0] if pags else (ctx.pages[0] if ctx.pages else ctx.new_page())
        else:
            browser = p.chromium.launch(
                channel="chrome",
                headless=args.headless,
                args=["--disable-blink-features=AutomationControlled", "--window-position=2000,2000"],
            )
            ctx = browser.new_context(user_agent=UA, locale="es-CL", viewport={"width": 1366, "height": 900},
                                      storage_state=str(auth_path) if usar_auth else None)
            ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            page = ctx.new_page()

        while cola and len(pages) < args.max_pages:
            if pendientes_nav:
                url, prof = pendientes_nav.pop(0)   # los del menú van primero
            else:
                url, prof = cola.pop(0)
            if url in pages:
                continue
            print(f"[{len(pages) + 1}/{args.max_pages}] (prof {prof}) {url}", flush=True)
            data = visit(page, url)
            if not data:
                print(f"    !! fallo definitivo: {url}", flush=True)
                pages[url] = {"url": url, "path": urlparse(url).path or "/", "failed": True}
                continue
            if LOGIN_PATH_RE.search(urlparse(page.url).path):
                print(f"    !! pide autenticación (aterrizó en {page.url})", flush=True)
                pages[url] = {"url": url, "path": urlparse(url).path or "/",
                              "failed": True, "auth_required": True}
                continue

            pu = urlparse(url)
            pages[url] = {
                "url": url,
                "path": pu.path or "/",
                "title": data["title"],
                "h1": data["h1"],
                "description": data["description"],
                "sections": data["sections"],
            }
            if url == inicio:
                pages[url]["site_name"] = data["site_name"]
                pages[url]["lang"] = data["lang"]

            if prof < args.max_depth:
                for lk in data["links"]:
                    u = normalizar_url(url, lk["href"])
                    if not u or not interno(u) or u in pages:
                        continue
                    if patron_skip and patron_skip.search(urlparse(u).path):
                        continue
                    item = (u, prof + 1)
                    if lk["nav"] and item not in pendientes_nav:
                        pendientes_nav.append(item)
                    elif item not in cola:
                        cola.append(item)
            time.sleep(random.uniform(args.delay_min, args.delay_max))
            guardar(pages, inicio, dominio)   # guardado incremental

        if args.cdp:
            try:
                page.goto(inicio, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            print("Navegador del usuario dejado abierto (pestaña de vuelta en el inicio).", flush=True)
        else:
            browser.close()

    n_ok, nombre = guardar(pages, inicio, dominio)
    ok = [pg for pg in pages.values() if not pg.get("failed") and pg.get("sections")]
    n_secs = sum(len(pg["sections"]) for pg in ok)
    print(f"OK -> {OUT_SCRAPE.name} ({n_ok} páginas, {n_secs} secciones)", flush=True)
    print(f"OK -> {OUT_SITE.name} (sitio: {nombre})", flush=True)
    n_auth = sum(1 for pg in pages.values() if pg.get("auth_required"))
    if n_auth:
        print(f"\nATENCIÓN: {n_auth} página(s) pidieron autenticación. "
              f"Inicia sesión con './aura.sh login' y vuelve a scrapear.", flush=True)


if __name__ == "__main__":
    main()
