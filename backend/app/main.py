"""
API del copiloto CHAT-AURA (una instancia = un sitio web).

Endpoints:
  GET  /api/health   — estado del servicio, modelo LLM y tamaño de la KB
  GET  /api/config   — identidad de la instancia (sitio, asistente, marca,
                       sugerencias) para el frontend
  GET  /api/paginas  — índice de páginas del sitio (sidebar del chat)
  POST /api/chat     — conversación con el agente (guardrails + tools)

Pipeline de /api/chat:
  1. rate limit + límite de longitud
  2. clasificación de entrada (block / crisis / offtopic / greeting)
  3. agente: pi con herramientas de lectura sobre la KB, o loop propio de
     tool-calling (listar_paginas, buscar_info, obtener_pagina)
  4. sanitización de URLs de salida (solo dominio del sitio)
"""

import logging
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import agent, config, guardrails, live_api, rag

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("aura-chat")

app = FastAPI(title="CHAT-AURA Copiloto", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # el frontend se sirve por nginx del mismo host
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    session_id: str = ""


class ChatResponse(BaseModel):
    reply: str
    fuentes: list = []
    blocked: bool = False
    paginas_sugeridas: list = []


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "model": agent.MODEL,
        "motor": "pi-agent" if agent.PI_ENABLED else "toolloop",
        "pi_model": agent.PI_MODEL,
        "llm_configured": bool(agent.API_KEY),
        "kb_chunks": len(rag.get_retriever().chunks),
        "site": config.SITE_NAME,
        "domain": config.SITE_DOMAIN,
    }


@app.get("/api/config")
def api_config():
    """Identidad de la instancia: el frontend tematiza todo con esto."""
    return {
        "assistant": config.ASSISTANT_NAME,
        "site": {
            "name": config.SITE_NAME,
            "url": config.SITE_HOME,
            "domain": config.SITE_DOMAIN,
            "description": config.SITE_DESCRIPTION,
            "contact_url": config.CONTACT_URL,
            "n_pages": config.SITE.get("n_pages", 0),
        },
        "brand": config.brand(),
        "suggestions": config.sugerencias_iniciales(),
        "greeting": guardrails.BIENVENIDA,
        "live_api": live_api.habilitada(),
    }


@app.get("/api/paginas")
def paginas():
    return rag.get_retriever().list_pages()


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "anon")


@app.post("/api/chat", response_model=ChatResponse)
def chat(body: ChatRequest, request: Request):
    guardrails.rate_limiter.check(_client_ip(request))

    msg = body.message.strip()
    if not msg:
        raise HTTPException(status_code=400, detail="Mensaje vacío")
    if len(msg) > guardrails.MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail=f"Mensaje demasiado largo (máx {guardrails.MAX_MESSAGE_CHARS} caracteres)")

    # --- Capa 1: clasificación de entrada ---
    cls = guardrails.classify_input(msg)
    if cls == "block":
        logger.info("input bloqueada")
        raise HTTPException(status_code=400, detail="No puedo responder ese tipo de consultas.")
    if cls == "crisis":
        return ChatResponse(reply=guardrails.CRISIS_DERIVACION, blocked=True)
    if cls == "offtopic":
        return ChatResponse(reply=guardrails.DERIVACION_ESTANDAR, blocked=True)
    if cls == "greeting":
        return ChatResponse(reply=guardrails.BIENVENIDA)

    # --- Agente (tool-calling) ---
    hist = body.history[-(guardrails.MAX_HISTORY_TURNS * 2):]
    try:
        live_api.set_user_token(request.headers.get("X-User-Token", ""))
        resultado = agent.run_agent(msg, hist)
    except Exception as e:
        logger.exception("fallo del agente")
        raise HTTPException(status_code=502, detail=f"Error del agente: {type(e).__name__}")
    finally:
        live_api.set_user_token("")

    # --- Sanitización de salida ---
    reply = guardrails.sanitize_output(resultado.reply)
    if not guardrails.validate_reply(reply):
        raise HTTPException(status_code=502, detail="No pude generar una respuesta en este momento. Inténtalo de nuevo en unos segundos.")

    fuentes = [
        {"pagina": f.get("pagina", config.SITE_NAME), "titulo": f.get("titulo", ""),
         "url": f.get("url") or config.SITE_HOME}
        for f in resultado.fuentes[:3]
    ]
    sugeridas = list({f["pagina"] for f in fuentes})[:3]
    return ChatResponse(reply=reply, fuentes=fuentes, paginas_sugeridas=sugeridas)
