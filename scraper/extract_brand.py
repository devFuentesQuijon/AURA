#!/usr/bin/env python3
"""
Extrae la identidad visual de un sitio para tematizar el chat.

Del sitio objetivo toma:
  - fondo de la cabecera (cualquier elemento con clase *header*/*navbar*
    de altura razonable) -> color oscuro de la marca
  - color de botones/enlaces de acción -> color primario/acento
  - frecuencia de hex saturados en hojas de estilo (con lista de exclusión
    de colores utilitarios de frameworks/plugins) como respaldo
  - logo (cualquier <img> cuyo src/alt/class indique logo) -> assets/logo.*
  - favicon -> assets/favicon.*

Todo best-effort: si algo falla, brand.json trae valores por defecto y el
frontend usa un badge con la inicial del sitio. Se puede editar a mano
después (brand.json + assets) sin re-ejecutar nada.

Salida: frontend/assets/brand.json {palette, logo, favicon, ready}

Uso: .venv/bin/python scraper/extract_brand.py --url https://ejemplo.cl
"""

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "frontend" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

PALETA_DEFAULT = {
    "dark": "#11122C",
    "primary": "#FF6B00",
    "primary_dark": "#C34E00",
    "primary_tint": "#FFE9D9",
    "bg": "#F6F6F8",
    "border": "#E3E1EC",
    "text": "#1B1B2F",
}

# Colores utilitarios típicos de frameworks/plugins (no son marca)
HEX_NEVER_PRIMARY = {
    "7AD03A", "46B450", "83B70B",  # woocommerce success greens
    "21759B", "00749C", "00A0D2", "2EA2CC",  # wordpress blues
    "DD3333", "CC4444", "D63638",  # rojos de alerta
    "96588A", "8224E3",  # woocommerce purples
    "F1F1F1", "FAFAFA", "FCFBFE",  # grises de fondo
}

JS_BRAND = """() => {
  const hexCount = {};
  for (const sheet of document.styleSheets) {
    let rules = [];
    try { rules = [...sheet.cssRules]; } catch (e) { continue; }
    const walk = (lista) => {
      for (const r of lista) {
        const txt = r.cssText || '';
        for (const m of (txt.match(/#[0-9a-fA-F]{6}\\b/g) || [])) {
          const h = m.toUpperCase();
          hexCount[h] = (hexCount[h] || 0) + 1;
        }
        if (r.cssRules) walk([...r.cssRules]);
      }
    };
    walk(rules);
  }

  // Logo: cualquier img que se declare como tal por src/alt/clase
  const logos = [];
  for (const img of document.querySelectorAll('img')) {
    const src = img.currentSrc || img.src || '';
    if (!/logo|logotipo|brand/i.test(src + ' ' + (img.alt || '') + ' ' + (img.className || ''))) continue;
    const r = img.getBoundingClientRect();
    const nw = img.naturalWidth || 0;
    if (r.width >= 30 || nw >= 60) {
      logos.push({src, alt: img.alt || '', w: Math.max(Math.round(r.width), nw), h: Math.max(Math.round(r.height), img.naturalHeight || 0)});
    }
  }

  // Cabecera: elementos con clase header/navbar de altura razonable
  const bgs = [];
  for (const el of document.querySelectorAll('[class*="header" i], [class*="navbar" i], [class*="topbar" i], header')) {
    const bg = getComputedStyle(el).backgroundColor;
    const h = el.getBoundingClientRect().height;
    if (h >= 30 && h <= 400) bgs.push({bg, h, y: el.getBoundingClientRect().top, tag: el.tagName});
  }

  // Botones: el primero con fondo visible y coloreado (CTA = marca)
  let button = null;
  for (const b of document.querySelectorAll('a[class*="btn" i], button, [class*="button" i], [role="button"]')) {
    const bg = getComputedStyle(b).backgroundColor;
    if (bg && bg !== 'rgba(0, 0, 0, 0)') { button = bg; break; }
  }
  // Enlaces del menú principal (nav): color de acento en texto
  let navLink = null;
  for (const a of document.querySelectorAll('nav a, [class*="menu" i] a, [class*="nav" i] a')) {
    const c = getComputedStyle(a).color;
    if (c && c !== 'rgb(0, 0, 0)' && c !== 'rgba(0, 0, 0, 0)') { navLink = c; break; }
  }

  const icons = [];
  for (const l of document.querySelectorAll('link[rel*="icon"]')) if (l.href) icons.push(l.href);
  return {hexCount, logos, bgs, button, navLink, icons};
}"""


def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _hex(t) -> str:
    return "#%02X%02X%02X" % t


def _luminancia(hex_color: str) -> float:
    r, g, b = [_rgb(hex_color)[i] / 255 for i in range(3)]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _saturacion(hex_color: str) -> float:
    r, g, b = _rgb(hex_color)
    mx, mn = max(r, g, b), min(r, g, b)
    return 0.0 if mx == mn else (mx - mn) / mx


def _rgb_str_a_hex(s: str):
    m = re.match(r"rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?", s or "")
    if not m:
        return None
    if m.group(4) is not None and float(m.group(4)) == 0.0:
        return None  # transparente
    return _hex((int(m.group(1)), int(m.group(2)), int(m.group(3))))


def _es_blanco(hex_color: str) -> bool:
    return all(c >= 235 for c in _rgb(hex_color))


def _mezclar(con_hex: str, otro_hex: str, f: float) -> str:
    a, b = _rgb(con_hex), _rgb(otro_hex)
    return _hex(tuple(round(a[i] + (b[i] - a[i]) * f) for i in range(3)))


def _oscurecer(hex_color: str, f: float) -> str:
    r, g, b = _rgb(hex_color)
    return _hex((round(r * f), round(g * f), round(b * f)))


def elegir_paleta(data: dict) -> dict:
    frec = {h.upper(): n for h, n in data.get("hexCount", {}).items() if isinstance(h, str)}
    candidatos = sorted(frec.items(), key=lambda x: -x[1])

    # --- oscuro: fondo de cabecera (topmost, oscuro, no transparente/blanco) ---
    dark = None
    for item in sorted(data.get("bgs", []), key=lambda x: (x.get("y", 0), x.get("h", 0))):
        h = _rgb_str_a_hex(item.get("bg", ""))
        if not h or _es_blanco(h):
            continue
        if _luminancia(h) < 0.30:
            dark = h
            break
    if not dark:
        # oscuros reales del css (no acentos): lum < 0.12; preferir un color
        # de marca sobre el negro puro genérico
        oscuros = [hx for hx, _ in candidatos if _luminancia(hx) < 0.12]
        no_negro = [hx for hx in oscuros if hx.lstrip("#").upper() != "000000"]
        dark = no_negro[0] if no_negro else (oscuros[0] if oscuros else None)
    if not dark:
        dark = PALETA_DEFAULT["dark"]

    # --- primario: botón -> enlace de menú -> hex frecuente no utilitario ---
    primary = None
    for src in (data.get("button"), data.get("navLink")):
        h = _rgb_str_a_hex(src or "")
        if h and not _es_blanco(h) and _saturacion(h) >= 0.25 and 0.2 < _luminancia(h) < 0.8 \
                and h.lstrip("#").upper() not in HEX_NEVER_PRIMARY:
            primary = h
            break
    if not primary:
        for hexa, _ in candidatos:
            hu = hexa.lstrip("#").upper()
            if hu in HEX_NEVER_PRIMARY or hu in ("000000", "FFFFFF"):
                continue
            if _saturacion(hexa) >= 0.35 and 0.25 < _luminancia(hexa) < 0.80:
                primary = hexa
                break
    if not primary:
        primary = PALETA_DEFAULT["primary"]

    return {
        "dark": dark,
        "primary": primary,
        "primary_dark": _oscurecer(primary, 0.72),
        "primary_tint": _mezclar("#FFFFFF", primary, 0.16),
        "bg": PALETA_DEFAULT["bg"],
        "border": _mezclar("#FFFFFF", dark, 0.12),
        "text": dark if _luminancia(dark) < 0.35 else PALETA_DEFAULT["text"],
    }


def descargar(ctx, url: str, dest: Path) -> bool:
    try:
        r = ctx.request.get(url, timeout=30000)
        if r.ok and len(r.body()) > 100:
            dest.write_bytes(r.body())
            return True
    except Exception:
        pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="", help="URL del sitio (default: env SITE_URL)")
    args = ap.parse_args()

    url = (args.url or __import__("os").environ.get("SITE_URL", "")).strip()
    if not url:
        raise SystemExit("Indica --url o define SITE_URL en .env")
    inicio = url if url.startswith("http") else "https://" + url

    logo_file, favicon_file = None, None
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                                              "Chrome/141.0.0.0 Safari/537.36"))
        page = ctx.new_page()
        page.goto(inicio, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        data = page.evaluate(JS_BRAND)

        logos = sorted(data["logos"], key=lambda im: -im["w"] * im["h"])
        if logos:
            ext = ".svg" if ".svg" in logos[0]["src"].lower() else ".png"
            dest = OUT / f"logo{ext}"
            if descargar(ctx, logos[0]["src"], dest):
                logo_file = dest.name
                print(f"logo -> {dest.name} ({dest.stat().st_size} bytes)")

        for cand in list(data["icons"]) + [inicio.rstrip("/") + "/favicon.ico"]:
            dest = OUT / ("favicon.svg" if ".svg" in cand else "favicon.png" if ".ico" not in cand else "favicon.ico")
            if descargar(ctx, cand, dest):
                favicon_file = dest.name
                print(f"favicon -> {dest.name} ({dest.stat().st_size} bytes)")
                break

        palette = elegir_paleta(data)
        browser.close()

    brand = {"palette": palette, "logo": logo_file, "favicon": favicon_file, "ready": True}
    (OUT / "brand.json").write_text(json.dumps(brand, ensure_ascii=False, indent=1), encoding="utf-8")
    print("paleta ->", json.dumps(palette))
    print(f"OK -> {OUT / 'brand.json'}")


if __name__ == "__main__":
    main()
