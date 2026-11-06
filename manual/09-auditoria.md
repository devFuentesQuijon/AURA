# Auditoría

Trazabilidad total de quién hizo qué y cuándo. Usa este documento cuando el usuario pregunte "quién cambió X", "historial de acciones" o cómo revisar el registro de actividad.

## Qué se registra

El `AuditMiddleware` graba **cada petición HTTP** (acciones Y vistas, incluidos errores):

- usuario (desde la sesión/API key), método, path, status, duración, IP, user-agent, metadatos.
- Persistencia en tabla `audit_logs` (fire-and-forget, con sesión propia: un fallo de auditoría jamás rompe la petición).
- Cada evento también se imprime en stdout como JSON de una línea (`{"event":"audit",...}`) para el colector externo de logs (Fluent Bit → ClickHouse/OmniLog). Las API keys aparecen solo como huella sha256, nunca en claro.

Además hay **eventos de dominio** (no son HTTP): `payment_status_change` (de/a, fuente, motivo, settlement, commissionable) y `payment_status_sync` (resumen por corrida). Se emiten siempre después del commit principal.

## Página web `/audit` (requiere `audit.read`)

Filtros: rango de fechas (desde/hasta), usuario, path, método HTTP, código de status, acción. Tabla con timestamp, usuario, acción, recurso, path, status y duración.

## API (requiere `audit.read`)

- `GET /api/v1/audit` — filtros: `date_from`, `date_to`, `user_id`, `path`, `method`, `status_code`, `action`, `resource` + paginación.
- `GET /api/v1/audit/{event_id}` — detalle de un evento.

## Configuración (admin)

`AUDIT_ENABLED` (on/off global) y `AUDIT_EXCLUDE_PATHS` (paths exactos excluidos, coma-separados — pensado para healthchecks ruidosos).

## Preguntas frecuentes de soporte

- **"¿Quién cambió la etiqueta de pago de esta factura?"** → Auditoría con action `payment_status_change`; el motivo y valores from/to están en los metadatos del evento.
- **"¿Quién sincronizó pagos manualmente?"** → buscar `payment_status_sync` o el POST a `/payments/sync` (usuario en la fila).
- **"No veo mi acción en auditoría"** → puede ser ruta excluida en `AUDIT_EXCLUDE_PATHS`, o un evento de dominio (buscar por acción, no por path).
- **"¿Guardan las contraseñas o las API keys?"** → No. Solo la huella sha256 de las API keys; las contraseñas solo como hash bcrypt.
