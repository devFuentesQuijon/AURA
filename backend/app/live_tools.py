"""
Proveedor declarativo de herramientas de datos (REST).

La instancia define sus tools en un manifest YAML (por defecto
data/live_tools.yaml, junto a la KB): cada entrada declara nombre,
descripción, método, endpoint, parámetros (con map_to hacia el nombre real
del API) y un template Jinja2 que formatea la respuesta JSON para el
contexto del modelo. Así cada instancia copilotee el servicio que
copilotee sin tocar código Python.

Formato de cada entrada:

  - name: metricas_dashboard          # único, snake_case
    description: "..."                # lo ve el modelo al elegir la tool
    method: GET                       # GET (default) o POST (endpoints de búsqueda)
    endpoint: /dashboard/kpis         # '{placeholders}' se rellenan con params
    params:                           # opcional
      desde: {description: "...", map_to: date_from, required: false}
    static_params: {page: 1}          # opcional, siempre enviados
    aggregate:                        # opcional: agrupa y suma EN CÓDIGO todas
      {page_size: 500, max_pages: 10, #  las páginas de un endpoint-lista
       group_by: [month, payment_status],
       sum: [total_amount]}          # 'month' deriva de issue_date (YYYY-MM)
    template: |                       # opcional; sin template se aplana el JSON
      Total: {{ total_n }} · {{ groups }} · {{ total_sum['total_amount'] }}

Sin template, la respuesta se aplana genéricamente (dict/lista → líneas,
con tope de filas). Con aggregate, el contexto del template recibe
``groups`` (lista de dicts con los campos de group_by + ``count`` +
``sum_<campo>``), ``total_n`` y ``total_sum`` (dict por campo sumado).
Las salidas pasan por el tope de caracteres global.
"""

import logging
import os
import re
from pathlib import Path

import yaml
from jinja2.sandbox import SandboxedEnvironment

from . import live_api

logger = logging.getLogger("aura-tools")

MAX_TEMPLATE_CHARS = 4000
MAX_FILAS_PLANAS = 5
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")

_jinja = SandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)


def _buscar_manifest() -> Path | None:
    candidatos = [os.environ.get("LIVE_TOOLS_PATH", "").strip(),
                  "data/live_tools.yaml", "live_tools.yaml"]
    aqui = Path(__file__).resolve()
    for c in candidatos:
        if not c:
            continue
        for base in (aqui.parent.parent, aqui.parent.parent.parent):
            p = (base / c).resolve()
            if p.exists():
                return p
    return None


def _aplanar(data, prefijo: str = "") -> str:
    """JSON → líneas 'clave: valor' legibles (con tope de filas)."""
    lineas = []
    if isinstance(data, dict):
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lineas.append(f"{prefijo}{k}:")
                anidadas = _aplanar(v, prefijo + "  ")
                if anidadas:
                    lineas.append(anidadas)
            else:
                lineas.append(f"{prefijo}{k}: {v}")
    elif isinstance(data, list):
        for item in data[:MAX_FILAS_PLANAS]:
            lineas.append(_aplanar(item, prefijo + "- "))
        if len(data) > MAX_FILAS_PLANAS:
            lineas.append(f"{prefijo}… +{len(data) - MAX_FILAS_PLANAS} más")
    else:
        lineas.append(f"{prefijo}{data}")
    return "\n".join(lineas)


def _construir(entrada: dict) -> tuple[dict, callable] | None:
    name = str(entrada.get("name", "")).strip()
    method = str(entrada.get("method", "GET")).upper()
    endpoint = str(entrada.get("endpoint", "")).strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,60}", name):
        logger.warning("live_tools: nombre inválido, se omite: %r", name)
        return None
    if method not in ("GET", "POST"):
        logger.warning("live_tools: método %s no permitido en %s", method, name)
        return None
    if not endpoint.startswith("/"):
        logger.warning("live_tools: endpoint inválido en %s", name)
        return None

    params_def = entrada.get("params") or {}
    properties, required = {}, []
    for pname, pdef in params_def.items():
        pdef = pdef or {}
        properties[pname] = {"type": pdef.get("type", "string"),
                             "description": str(pdef.get("description", ""))}
        if pdef.get("required"):
            required.append(pname)
    spec = {
        "type": "function",
        "function": {
            "name": name,
            "description": str(entrada.get("description", name)),
            "parameters": {"type": "object", "properties": properties,
                           "required": required},
        },
    }

    src = str(entrada.get("template", ""))[:MAX_TEMPLATE_CHARS]
    template = _jinja.from_string(src) if src.strip() else None
    static_params = entrada.get("static_params") or {}
    min_params = int(entrada.get("min_params", 0) or 0)
    aggregate = entrada.get("aggregate") or None
    if aggregate and not isinstance(aggregate, dict):
        logger.warning("live_tools: aggregate inválido en %s, se ignora", name)
        aggregate = None

    def impl(args=None, _name=name, _method=method, _endpoint=endpoint,
             _params=params_def, _static=static_params, _tpl=template,
             _min=min_params, _agg=aggregate):
        if not isinstance(args, dict):
            args = {}
        args = {k: v for k, v in args.items() if v not in (None, "")}
        if _min and len(args) < _min:
            return (f"Esta búsqueda exige al menos {_min} filtro: pide al "
                    "usuario que acote (rango de fechas, texto, estado, moneda…).")
        faltantes = [p for p, d in _params.items()
                     if (d or {}).get("required") and p not in args]
        if faltantes:
            return f"Faltan parámetros obligatorios: {', '.join(faltantes)}."

        # params consumidos por placeholders del endpoint no van en query/body
        usados_path = set(_PLACEHOLDER_RE.findall(_endpoint))
        path = _endpoint
        for ph in usados_path:
            if ph in args:
                path = path.replace("{" + ph + "}", str(args[ph]))

        query, body = dict(_static), dict(_static)
        for pname, pdef in _params.items():
            if pname in usados_path or pname not in args:
                continue
            dest = (pdef or {}).get("map_to", pname)
            (query if _method == "GET" else body)[dest] = args[pname]

        if _agg:
            return _agregar(_method, path, query, body, _agg, _tpl)

        data = live_api._request(_method, path,
                                 params=query or None, json=body or None)
        if _tpl:
            ctx = dict(data) if isinstance(data, dict) else {"items": data}
            ctx.setdefault("web_base", live_api.WEB_BASE)
            try:
                salida = _tpl.render(**ctx)
                # convención de templates: los saltos críticos se emiten con
                # {{ '\n' }}; aquí se normalizan los residuos de whitespace
                salida = re.sub(r"\n{2,}", "\n", salida)
            except Exception as e:  # noqa: BLE001 - template malo no tumba el loop
                logger.warning("live_tools: template de %s falló: %s", _name, e)
                salida = _aplanar(data)
        else:
            salida = _aplanar(data)
        return live_api._cortar(salida.strip() or "(respuesta vacía)")

    return spec, impl


def _agregar(method: str, path: str, query: dict, body: dict,
             agg: dict, tpl) -> str:
    """Recorre las páginas de un endpoint-lista y agrupa/suma EN CÓDIGO.

    Devuelve el template renderado con ``groups`` (cada grupo trae los
    campos de group_by + ``count`` + ``sum_<campo>``), ``total_n`` y
    ``total_sum``. La aritmética la hace el backend, no el modelo: así los
    totales son exactos y el modelo no extrapola desde una página muestra.
    """
    page_size = min(int(agg.get("page_size", 500) or 500), 500)
    max_pages = max(int(agg.get("max_pages", 10) or 10), 1)
    group_fields = [str(f) for f in (agg.get("group_by") or [])]
    sum_fields = [str(f) for f in (agg.get("sum") or [])]

    todos: list = []
    for pagina in range(1, max_pages + 1):
        payload = dict(query if method == "GET" else body)
        payload["page"] = pagina
        payload["page_size"] = page_size
        data_p = live_api._request(
            method, path,
            params=payload if method == "GET" else None,
            json=None if method == "GET" else payload)
        if not isinstance(data_p, list):
            break
        todos.extend(data_p)
        if len(data_p) < page_size:
            break

    grupos: dict = {}
    for it in todos:
        if not isinstance(it, dict):
            continue
        clave = []
        for f in group_fields:
            if f == "month":
                v = str((it.get("issue_date") or "")[:7]) or "s/f"
            else:
                v = it.get(f)
                v = "sin dato" if v in (None, "") else str(v)
            clave.append(v)
        g = grupos.setdefault(tuple(clave), dict(zip(group_fields, clave)))
        g["count"] = g.get("count", 0) + 1
        for s in sum_fields:
            val = it.get(s)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                g[f"sum_{s}"] = g.get(f"sum_{s}", 0.0) + float(val)

    lista = sorted(grupos.values(),
                   key=lambda g: [str(g.get(f, "")) for f in group_fields])
    total_sum = {s: round(sum(g.get(f"sum_{s}", 0.0) for g in lista), 2)
                 for s in sum_fields}
    ctx = {"groups": lista, "total_n": len(todos), "total_sum": total_sum,
           "truncated": len(todos) >= page_size * max_pages,
           "web_base": live_api.WEB_BASE}
    if tpl:
        try:
            salida = tpl.render(**ctx)
            salida = re.sub(r"\n{2,}", "\n", salida)
        except Exception as e:  # noqa: BLE001 - template malo no tumba el loop
            logger.warning("live_tools: template agregado falló: %s", e)
            salida = _aplanar(ctx)
    else:
        salida = _aplanar(ctx)
    return live_api._cortar(salida.strip() or "(respuesta vacía)")


def cargar() -> list[tuple[dict, callable]]:
    """(spec, impl) por cada tool declarada; [] si no hay manifest o API."""
    if not live_api.habilitada():
        return []
    ruta = _buscar_manifest()
    if not ruta:
        logger.info("live_tools: sin manifest (data/live_tools.yaml)")
        return []
    try:
        manifiesto = yaml.safe_load(ruta.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("live_tools: manifest ilegible (%s): %s", ruta, e)
        return []
    if not isinstance(manifiesto, list):
        logger.warning("live_tools: el manifest debe ser una lista de tools")
        return []
    out = []
    for entrada in manifiesto:
        try:
            tool = _construir(entrada or {})
        except Exception as e:  # noqa: BLE001
            logger.warning("live_tools: entrada inválida omitida: %s", e)
            continue
        if tool:
            out.append(tool)
    logger.info("live_tools: %d tools desde %s", len(out), ruta)
    return out
