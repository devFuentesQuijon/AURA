"""
RAG ligero sobre la base de conocimiento del sitio.

Recuperación: BM25 (rank_bm25) sobre chunks con normalización de
español (sin tildes, minúsculas) + boost por coincidencia del título de
la página en la consulta + boost del chunk panorámico para preguntas de
"qué hay / cuántas páginas". Determinista, sin embeddings externos.

La KB es data/knowledge_base.json, generada por scraper/build_kb.py:
  {id, pagina, tipo: sitio|pagina|seccion, titulo, texto, url, metadata}

Sinónimos opcionales por instancia: data/synonyms.json {"tema": "expansion"}.
"""

import json
import re
import unicodedata
from pathlib import Path

from rank_bm25 import BM25Okapi

def _buscar_en_candidatos(rel: str):
    """Resuelve rel buscando en las dos raíces posibles:
    - backend/data (layout Docker: /srv/data, o backend/data en local)
    - data en la raíz del repo (resultado del scrapeo local)
    """
    aqui = Path(__file__).resolve()
    for base in (aqui.parent.parent, aqui.parent.parent.parent):
        p = base / rel
        if p.exists():
            return p
    return aqui.parent.parent / rel


KB_PATH = _buscar_en_candidatos("data/knowledge_base.json")
SYNONYMS_PATH = _buscar_en_candidatos("data/synonyms.json")

# Marca de los chunks globales del sitio
SITIO = "__sitio__"

STOP = set("""
de la el los las y o u a en para por con del al un una es son ser está están
qué que cual cuales cómo cuánto cuando donde me mi te se lo le les
hay muy más menos sobre este esta estos estas ese esa eso
quiero saber información info tema temas dime
puedo podría gustaría me gustaría quisiera tienen tiene hay
página página sitio web url link ir ver
""".split())


def _cargar_synonyms() -> dict:
    if SYNONYMS_PATH.exists():
        try:
            return {k.lower(): v for k, v in json.loads(SYNONYMS_PATH.read_text(encoding="utf-8")).items()}
        except Exception:
            pass
    return {}


SYNONYMS = _cargar_synonyms()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9ñ ]+", " ", s.lower())


def _tokens(s: str):
    return [t for t in _norm(s).split() if t not in STOP and len(t) > 1]


def _expand(tokens):
    out = list(tokens)
    for t in tokens:
        if t in SYNONYMS:
            out.extend(SYNONYMS[t].lower().split())
    return out


class Retriever:
    def __init__(self):
        self.chunks = json.loads(KB_PATH.read_text(encoding="utf-8"))
        self.corpus = [_tokens(f"{c['pagina']} {c['titulo']} {c['texto']}") for c in self.chunks]
        self.bm25 = BM25Okapi(self.corpus)
        # títulos de página normalizados para el boost
        self.page_names = []
        for c in self.chunks:
            if c["pagina"] != SITIO and c["pagina"] not in [n for n, _ in self.page_names]:
                self.page_names.append((c["pagina"], _norm(c["pagina"])))

    def search(self, query: str, top_k: int = 6, min_score: float = 0.5):
        """Devuelve lista de chunks relevantes (score 0 si no hay nada)."""
        if not query.strip():
            return []
        q = _expand(_tokens(query))
        if not q:
            return []
        scores = self.bm25.get_scores(q)
        qn = _norm(query)
        boosts = []
        for name, nn in self.page_names:
            if not nn:
                continue
            toks = [t for t in nn.split() if t not in ("de", "la", "el", "en", "y", "con")]
            if not toks:
                continue
            if nn in qn or (len(toks) >= 2 and " ".join(toks[:2]) in qn):
                boosts.append(name)
        qset = set(q)
        listado_intent = qset & {"cuantas", "cuantos", "paginas", "secciones", "listado",
                                 "oferta", "total", "contiene", "hay"}
        scored = []
        for i, s in enumerate(scores):
            chunk = self.chunks[i]
            if boosts and any(_norm(b) in _norm(chunk["pagina"]) for b in boosts):
                s += 6.0
            if chunk["pagina"] == SITIO and listado_intent:
                s += 8.0
            scored.append((s, i))
        results = []
        for s, i in sorted(scored, reverse=True)[:top_k]:
            if s >= min_score:
                results.append({**self.chunks[i], "score": round(float(s), 2)})
        return results

    def list_pages(self):
        """Índice único de páginas para el frontend (sidebar)."""
        out = {}
        for c in self.chunks:
            if c["pagina"] == SITIO or c["tipo"] == "manual":
                continue
            out.setdefault(c["url"], {
                "titulo": c["pagina"],
                "url": c["url"],
                "path": c["metadata"].get("path", ""),
                "description": c["metadata"].get("description", ""),
            })
        return sorted(out.values(), key=lambda x: (x["path"] != "/", x["path"]))

    def get_page(self, nombre: str):
        """Resumen de una página por título o path (aprox, sin tildes)."""
        objetivo = _norm(nombre)
        if not objetivo:
            return None
        otoks = set(objetivo.split())
        mejor, mejor_sim = None, 0.0
        for c in self.chunks:
            if c["tipo"] != "pagina":
                continue
            candidatos = (_norm(c["pagina"]), _norm(c["metadata"].get("path", "")))
            for cn in candidatos:
                if not cn:
                    continue
                ctoks = set(cn.split())
                # 'carreras online' dentro de 'carreras online estudia enac'
                if otoks and otoks <= ctoks:
                    sim = 0.95
                elif len(objetivo) > 3:
                    sim = _similitud(objetivo, cn)
                else:
                    sim = 0.0
                if sim >= 0.95:
                    return c
                if sim > mejor_sim:
                    mejor, mejor_sim = c, sim
        return mejor if mejor_sim >= 0.55 else None

    def page_sections(self, nombre: str, top_k: int = 5):
        """Secciones de la página más parecida a 'nombre'."""
        pagina = self.get_page(nombre)
        if not pagina:
            return []
        secs = [c for c in self.chunks if c["url"] == pagina["url"] and c["tipo"] == "seccion"]
        return secs[:top_k]


def _similitud(a: str, b: str) -> float:
    """Jaccard simple de tokens; 1.0 = iguales."""
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


_retriever = None


def get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever
