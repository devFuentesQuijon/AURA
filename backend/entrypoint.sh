#!/usr/bin/env sh
# Arranque del backend: genera la config del proveedor LLM para pi desde
# las variables de entorno (cualquier endpoint compatible OpenAI: Z.ai,
# LLM local, etc.) y luego ejecuta uvicorn.
set -e

BASE="${ZAI_BASE_URL:-${LLM_BASE_URL:-https://api.z.ai/api/coding/paas/v4}}"
MOTOR="${ZAI_MODEL:-${LLM_MODEL:-glm-5.1}}"
PI_MOTOR="${PI_MODEL:-glm-5.3-flash}"
CLAVE="${ZAI_API_KEY:-${LLM_API_KEY:-}}"

python3 - "$BASE" "$MOTOR" "$PI_MOTOR" "$CLAVE" <<'PY'
import json, sys

base, modelo, pi_modelo, clave = sys.argv[1:5]
modelos = []
for m in dict.fromkeys([pi_modelo, modelo]):  # únicos, en orden
    modelos.append({"id": m})

cfg = {"providers": {"zai": {
    "baseUrl": base,
    "api": "openai-completions",
    "apiKey": clave,
    "models": modelos,
}}}
import os
os.makedirs(os.path.expanduser("~/.pi"), exist_ok=True)
with open(os.path.expanduser("~/.pi/models.json"), "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
print(f"[entrypoint] pi provider -> {base} (modelos: {', '.join(m['id'] for m in modelos)})")
PY

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --timeout-keep-alive 120
