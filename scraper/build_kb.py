#!/usr/bin/env python3
"""
Consolida data/scrape.json en la base de conocimiento del copiloto:
data/knowledge_base.json

Estructura de chunks (genérica, sin conceptos de carreras):
  {id, pagina, tipo, titulo, texto, url, metadata{path, description}}

Tipos:
  sitio    — panorama del sitio (nombre, descripción, índice de páginas).
             Responde "¿qué hay en este sitio?", "¿qué páginas tienen?"
  pagina   — resumen de cada página (título + descripción + primer extracto)
  seccion  — cada sección de contenido de cada página (textos largos se
             trozan en bloques de ~2600 chars para el retrieval)

Filtros anti-ruido:
  - secciones repetidas en muchas páginas (boilerplate: banners, avisos
    legales en footer renderizado, "síguenos en redes") se omiten
  - secciones de cookies/consentimiento cortas se omiten

Además calcula keywords y topics (vocabulario del sitio) y los fusiona en
data/site.json: el backend los usa para los guardrails de alcance y el
frontend para las sugerencias iniciales.

Uso: .venv/bin/python scraper/build_kb.py

Manuales de usuario: si existe la carpeta manual/ con archivos .md en la
raíz del repo, cada documento se troza en chunks tipo "manual" (secciones
'## ' en bloques de ~2600 chars) y se suman a la KB con pagina
"Manual: <título>". No aparecen en el índice de páginas del sitio, pero
sí en la búsqueda del agente. Vuelve a correr este script tras agregar
o editar manuales.
"""

import json
import re
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRAPE_PATH = BASE / "data" / "scrape.json"
OUT_PATH = BASE / "data" / "knowledge_base.json"
SITE_PATH = BASE / "data" / "site.json"
MANUAL_DIR = BASE / "manual"

SITIO = "__sitio__"          # marca de los chunks globales (era INSTITUCIONAL)
BLOQUE = 2600                # tamaño de trozo para secciones largas
MIN_SECCION = 40
COOKIE_RE = re.compile(r"(aceptar|cookies?|pol[ií]tica de privacidad|consentimiento)", re.I)

# Filtros anti-datos: si el sitio scrapeado es una app con datos de negocio
# (facturas, montos, clientes), esas secciones NO deben quedar en la KB
# estática — el chat es anónimo y solo los datos en vivo (con la cuenta del
# usuario) deben exponerlos. data/kb_filters.json (opcional):
#   {"drop_title_patterns": [...], "drop_text_patterns": [...]}
FILTERS_PATH = BASE / "data" / "kb_filters.json"

# términos significativos para el vocabulario del sitio (guardrails/sugerencias)
STOP = set("""
de la el los las y o u a en para por con del al un una es son ser está están
que se su sus tu tus te lo le les nos me mi más menos muy como así también
este esta estos estas ese esa eso hay puede puedes puede cómo cuál cuales
donde cuando cuanto cuánta información info página pagina sitio web ver ir
hacer hacerlo aquí allí ahora antes después año años día días vez veces
cada otros otras donde aquello qué quién cual nueva nuevo nuevos
manual manuales guía guia documento documentos
""".split())


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", s).strip().lower()


def slugify(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "x"


def limpiar_titulos(pages: list) -> None:
    """Quita el sufijo de marca de los títulos ('Precios | MiSitio' ->
    'Precios'): segmentos tras separador que se repiten en muchos
    títulos son marca, no contenido. Resultado en pg['titulo_limpio'].
    """
    seps = re.compile(r"\s+(?:\||·|—|–)\s+")
    conteo = Counter()
    for pg in pages:
        t = (pg.get("h1") or pg.get("title") or "").strip()
        for seg in seps.split(t)[1:]:
            if seg.strip():
                conteo[seg.strip()] += 1
    marcas = {s for s, n in conteo.items() if n >= max(2, 0.3 * len(pages))}
    for pg in pages:
        t = (pg.get("h1") or pg.get("title") or "").strip()
        partes = [p.strip() for p in seps.split(t) if p.strip()]
        limpio = " ".join(p for p in partes if p not in marcas)
        pg["titulo_limpio"] = limpio or (partes[0] if partes else pg["path"])


def firma_seccion(texto: str) -> str:
    """Huella de una sección para detectar boilerplate repetido."""
    return norm(texto)[:160]


def filtrar_boilerplate(pages: list) -> None:
    """Marca sections con 'skip' si se repiten en muchas páginas."""
    frec = Counter()
    for pg in pages:
        visto = set()
        for s in pg.get("sections", []):
            f = firma_seccion(s["text"])
            if f and f not in visto:
                frec[f] += 1
                visto.add(f)
    umbral = max(3, int(0.35 * len(pages)))
    for pg in pages:
        for s in pg.get("sections", []):
            f = firma_seccion(s["text"])
            repetida = frec.get(f, 0) >= umbral
            corta_cookie = len(s["text"]) < 400 and COOKIE_RE.search(s["title"] + " " + s["text"])
            if repetida or corta_cookie or len(s["text"]) < MIN_SECCION:
                s["skip"] = True


def cargar_filtros() -> tuple[list, list]:
    """(title_patterns, text_patterns) de data/kb_filters.json; [] si no hay."""
    if not FILTERS_PATH.exists():
        return [], []
    try:
        data = json.loads(FILTERS_PATH.read_text(encoding="utf-8"))
        titulos = [re.compile(p, re.I) for p in data.get("drop_title_patterns", [])]
        textos = [re.compile(p, re.I) for p in data.get("drop_text_patterns", [])]
        return titulos, textos
    except Exception as e:
        print(f"aviso: kb_filters.json ilegible ({e}); sin filtros extra")
        return [], []


def seccion_prohibida(titulo: str, texto: str,
                      f_titulos: list, f_textos: list) -> bool:
    """True si la sección trae datos de negocio que no van en la KB."""
    return (any(p.search(titulo or "") for p in f_titulos)
            or any(p.search(texto or "") for p in f_textos))


def vocabulario(pages: list, manuales: list | None = None,
                f_titulos: list | None = None) -> tuple:
    """(keywords, topics) del sitio para alcance y sugerencias."""
    terminos = Counter()
    for pg in pages:
        for campo in (pg.get("h1") or "", pg.get("title") or ""):
            for tok in norm(campo).split():
                if len(tok) > 3 and tok not in STOP and not tok.isdigit():
                    terminos[tok] += 2
        for s in pg.get("sections", []):
            if s.get("skip") or s.get("title") == "__intro__":
                continue
            if f_titulos and any(p.search(s["title"] or "") for p in f_titulos):
                continue
            for tok in norm(s["title"]).split():
                if len(tok) > 3 and tok not in STOP and not tok.isdigit():
                    terminos[tok] += 1
    for m in manuales or []:
        for tok in norm(m["pagina"]).split():
            if len(tok) > 3 and tok not in STOP and not tok.isdigit():
                terminos[tok] += 2
        for sec in m.get("secciones", []):
            for tok in norm(sec).split():
                if len(tok) > 3 and tok not in STOP and not tok.isdigit():
                    terminos[tok] += 1
    keywords = [t for t, _ in terminos.most_common(60)]

    topics, usados = [], set()
    for pg in pages:
        for s in pg.get("sections", []):
            if s.get("skip") or len(s["title"]) < 4:
                continue
            if f_titulos and any(p.search(s["title"] or "") for p in f_titulos):
                continue
            t = s["title"].strip()
            nt = norm(t)
            if nt in usados or nt in ("__intro__",) or COOKIE_RE.search(t):
                continue
            usados.add(nt)
            topics.append({"pagina": pg.get("h1") or pg["title"], "tema": t, "url": pg["url"]})
            break  # un tema por página: diversidad garantizada
        if len(topics) >= 12:
            break
    return keywords, topics


def trocear_manuales(add) -> list:
    """Chunks tipo 'manual' desde manual/*.md (manuales de usuario).

    Cada documento se divide por secciones ('## '): un chunk de
    introducción (texto previo al primer '##') y chunks por sección,
    trozados en bloques de BLOQUE. Devuelve la lista de documentos con
    sus secciones para el vocabulario.
    """
    docs = []
    if not MANUAL_DIR.exists():
        return docs
    for ruta in sorted(MANUAL_DIR.glob("*.md")):
        lineas = ruta.read_text(encoding="utf-8").splitlines()
        titulo_doc = next((re.sub(r"\s*#+\s*$", "", m.group(1)).strip()
                           for l in lineas
                           if (m := re.match(r"^#\s+(.+)", l)) and m.group(1).strip()),
                          None) or ruta.stem
        cuerpo = "\n".join(l for l in lineas
                           if not (re.match(r"^#\s+", l) and titulo_doc not in l)).strip("\n")
        pagina = f"Manual: {titulo_doc}"
        doc = {"doc": ruta.name, "pagina": pagina, "secciones": []}
        docs.append(doc)

        partes = re.split(r"(?m)^##\s+", cuerpo)
        intro = partes[0].strip()
        if intro:
            add(pagina, "manual", f"Guía: {titulo_doc}",
                f"{titulo_doc}\n{intro}", "", {"doc": ruta.name})
        for parte in partes[1:]:
            cabeza, _, resto = parte.partition("\n")
            titulo_sec = cabeza.strip().strip("#").strip() or titulo_doc
            cuerpo_sec = resto.strip()
            doc["secciones"].append(titulo_sec)
            texto = f"{titulo_sec}\n{cuerpo_sec}".strip()
            for i in range(0, len(texto), BLOQUE):
                add(pagina, "manual", titulo_sec, texto[i:i + BLOQUE], "",
                    {"doc": ruta.name})
    return docs


def main():
    scrape = json.loads(SCRAPE_PATH.read_text(encoding="utf-8"))
    pages = [p for p in scrape["pages"] if not p.get("failed") and p.get("sections")]
    filtrar_boilerplate(pages)
    limpiar_titulos(pages)
    f_titulos, f_textos = cargar_filtros()

    chunks = []

    def add(pagina, tipo, titulo, texto, url, meta=None):
        texto = re.sub(r"[ \t]+\n", "\n", (texto or "")).strip()
        if not texto or len(texto) < 15:
            return
        chunks.append({
            "id": f"{slugify(pagina)}-{tipo}-{len(chunks):04d}",
            "pagina": pagina,
            "tipo": tipo,
            "titulo": titulo,
            "texto": texto,
            "url": url,
            "metadata": meta or {},
        })

    titulo_de = lambda pg: (pg.get("titulo_limpio") or pg.get("h1")
                            or re.sub(r"\s+", " ", pg.get("title", "")).strip() or pg["path"])

    # --- Resumen por página + secciones ---
    for pg in pages:
        titulo = titulo_de(pg)
        primera = next((s["text"] for s in pg["sections"]
                        if not s.get("skip")
                        and not seccion_prohibida(s["title"], s["text"],
                                                  f_titulos, f_textos)), "")
        resumen = [f"Página: {titulo}"]
        if pg.get("description"):
            resumen.append(f"Resumen: {pg['description']}")
        if primera:
            resumen.append(f"Contenido: {primera[:600]}")
        resumen.append(f"URL: {pg['url']}")
        add(titulo, "pagina", f"Resumen de la página {titulo}",
            "\n".join(resumen), pg["url"],
            {"path": pg["path"], "description": pg.get("description", "")})

        for s in pg["sections"]:
            if s.get("skip"):
                continue
            if seccion_prohibida(s["title"], s["text"], f_titulos, f_textos):
                continue
            texto = f"{s['title']}\n{s['text']}"
            for i in range(0, len(texto), BLOQUE):
                add(titulo, "seccion", s["title"], texto[i:i + BLOQUE], pg["url"],
                    {"path": pg["path"]})

    # --- Manuales de usuario (manual/*.md) ---
    manuales = trocear_manuales(add)

    # --- Panorama del sitio ---
    inicio = scrape["meta"]["inicio"]
    dominio = scrape["meta"]["dominio"]
    indice = "\n".join(
        f"- {titulo_de(pg)} ({pg['url']}): {pg.get('description', '')[:120]}"
        for pg in pages)
    add(SITIO, "sitio", f"¿Qué contiene el sitio {dominio}?",
        f"El sitio {dominio} tiene {len(pages)} páginas principales:\n{indice}",
        inicio)

    OUT_PATH.write_text(json.dumps(chunks, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- keywords/topics -> site.json ---
    site = json.loads(SITE_PATH.read_text(encoding="utf-8")) if SITE_PATH.exists() else {}
    keywords, topics = vocabulario(pages, manuales, f_titulos)
    site.update({"keywords": keywords, "topics": topics, "n_chunks": len(chunks),
                 "kb_built_at": datetime.now().isoformat(timespec="seconds")})
    SITE_PATH.write_text(json.dumps(site, ensure_ascii=False, indent=1), encoding="utf-8")

    tipos = {}
    for c in chunks:
        tipos[c["tipo"]] = tipos.get(c["tipo"], 0) + 1
    print(f"OK -> {OUT_PATH.name}: {len(chunks)} chunks | tipos: {tipos}")
    print(f"OK -> {SITE_PATH.name}: {len(keywords)} keywords, {len(topics)} topics")


if __name__ == "__main__":
    main()
