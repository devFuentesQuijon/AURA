"""
Proveedor de herramientas MCP (Model Context Protocol).

Conecta la instancia con servidores MCP declarados en data/mcp_servers.json
y expone sus tools al agente con el prefijo <servidor>__<tool>:

  [
    {"name": "github", "transport": "http", "url": "http://localhost:3001/mcp",
     "forward_auth": true, "allowed_tools": ["search_repositories"],
     "timeout": 20},
    {"name": "local", "transport": "stdio", "command": "python3",
     "args": ["mi_server.py"], "env": {"VAR": "1"}}
  ]

- allowed_tools: allowlist; ausente = todas (con tope por servidor).
- forward_auth (http): reenvía el Bearer del usuario conectado POR LLAMADA
  (el servidor MCP aplica los permisos del usuario, como la API REST).
- headers estáticos por servidor: "headers": {"X-Clave": "..."}.
- El catálogo se resuelve al arrancar; un servidor caído se omite con
  warning (se reintenta al reiniciar).
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from . import live_api

logger = logging.getLogger("aura-mcp")

MAX_TOOLS_POR_SERVIDOR = 15
_PREFIJO_MAX = 60


def _buscar_config() -> Path | None:
    candidatos = [os.environ.get("MCP_SERVERS_PATH", "").strip(),
                  "data/mcp_servers.json", "mcp_servers.json"]
    aqui = Path(__file__).resolve()
    for c in candidatos:
        if not c:
            continue
        for base in (aqui.parent.parent, aqui.parent.parent.parent):
            p = (base / c).resolve()
            if p.exists():
                return p
    return None


def _cargar_servidores() -> list[dict]:
    ruta = _buscar_config()
    if not ruta:
        return []
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("mcp: config ilegible (%s): %s", ruta, e)
        return []
    if not isinstance(data, list):
        logger.warning("mcp: el config debe ser una lista de servidores")
        return []
    return [s for s in data if isinstance(s, dict) and s.get("name")]


async def _conectar(server: dict):
    """Devuelve (read, write, ctx) según el transporte."""
    timeout = float(server.get("timeout", live_api.TIMEOUT))
    if server.get("transport") == "stdio":
        from mcp.client.stdio import StdioServerParameters, stdio_client
        env = dict(os.environ)
        env.update(server.get("env") or {})
        params = StdioServerParameters(command=server["command"],
                                       args=server.get("args") or [], env=env)
        ctx = stdio_client(params)
    else:
        import httpx2
        from mcp.client.streamable_http import streamable_http_client
        headers = dict(server.get("headers") or {})
        if server.get("forward_auth") and live_api.get_user_token():
            headers["Authorization"] = f"Bearer {live_api.get_user_token()}"
        client = httpx2.AsyncClient(headers=headers, timeout=timeout)
        ctx = streamable_http_client(server["url"], http_client=client)
    return ctx


async def _list_tools(server: dict) -> list:
    ctx = await _conectar(server)
    async with ctx as streams:
        read, write = streams[0], streams[1]
        from mcp import ClientSession
        async with ClientSession(read, write) as session:
            await session.initialize()
            res = await session.list_tools()
            return list(res.tools or [])


async def _call_tool(server: dict, tool_name: str, args: dict) -> str:
    from mcp import ClientSession
    timeout = float(server.get("timeout", live_api.TIMEOUT))
    ctx = await _conectar(server)

    async def _inner():
        async with ctx as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                res = await session.call_tool(tool_name, args)
                textos = [c.text for c in (res.content or [])
                          if getattr(c, "type", "") == "text" and c.text]
                if res.is_error:
                    raise live_api.LiveError(
                        f"El servidor MCP devolvió error: {' '.join(textos)[:300]}")
                return "\n".join(textos) or "(sin contenido)"

    return await asyncio.wait_for(_inner(), timeout=timeout + 5)


def _make_impl(server: dict, tool_name: str) -> callable:
    def impl(args=None):
        try:
            salida = asyncio.run(_call_tool(server, tool_name, args or {}))
            return live_api._cortar(salida)
        except live_api.LiveError:
            raise
        except asyncio.TimeoutError:
            raise live_api.LiveError("La herramienta MCP tardó demasiado")
        except Exception as e:  # noqa: BLE001
            logger.warning("mcp: %s/%s falló: %s", server["name"], tool_name,
                           type(e).__name__)
            raise live_api.LiveError("No pude consultar la herramienta externa")
    return impl


def herramientas() -> list[tuple[dict, callable]]:
    """(spec, impl) por cada tool MCP permitida de cada servidor."""
    out = []
    for server in _cargar_servidores():
        try:
            tools = asyncio.run(_list_tools(server))
        except Exception as e:  # noqa: BLE001 - servidor caído no tumba el boot
            logger.warning("mcp: servidor %s omitido (%s)",
                           server.get("name"), type(e).__name__)
            continue
        permitidas = server.get("allowed_tools") or []
        n = 0
        for t in tools:
            if permitidas and t.name not in permitidas:
                continue
            if n >= MAX_TOOLS_POR_SERVIDOR:
                logger.warning("mcp: servidor %s, tope de %d tools alcanzado",
                               server["name"], MAX_TOOLS_POR_SERVIDOR)
                break
            nombre = f"{server['name']}__{t.name}"[:_PREFIJO_MAX]
            schema = t.input_schema or {"type": "object", "properties": {}}
            spec = {
                "type": "function",
                "function": {
                    "name": nombre,
                    "description": (t.description or f"{server['name']}:{t.name}"),
                    "parameters": schema,
                },
            }
            out.append((spec, _make_impl(server, t.name)))
            n += 1
        logger.info("mcp: %d tools desde %s", n, server["name"])
    return out
