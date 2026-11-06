"""
Cliente LLM (GLM vía API de Z.ai, compatible OpenAI).

La identidad/alcance del copiloto van en el system prompt del agente
(agent.py), construido dinámicamente desde app.config.
"""

import os

import requests

# Soporta aliases genéricos LLM_* para cualquier endpoint compatible OpenAI
# (Z.ai, OpenRouter, vLLM, Ollama con servidor OpenAI, etc.).
API_BASE = (os.environ.get("ZAI_BASE_URL") or os.environ.get("LLM_BASE_URL")
            or "https://api.z.ai/api/coding/paas/v4")
MODEL = os.environ.get("ZAI_MODEL") or os.environ.get("LLM_MODEL") or "glm-5.1"
API_KEY = os.environ.get("ZAI_API_KEY") or os.environ.get("LLM_API_KEY") or ""
TIMEOUT = int(os.environ.get("LLM_TIMEOUT", os.environ.get("ZAI_TIMEOUT", "60")))


def chat(messages, max_tokens=900, temperature=0.4):
    """Llama al modelo. messages: [{role, content}]. Devuelve texto."""
    if not API_KEY:
        raise RuntimeError("ZAI_API_KEY no configurada")
    resp = requests.post(
        f"{API_BASE}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"].strip()
