# Webhooks e integraciones salientes

Notificaciones HTTP hacia sistemas externos cuando pasa algo en SixFacturIA. Usa este documento cuando el usuario pregunte por "avisar a otro sistema", "integración", "entregas webhook" o el relay de eventos.

## Modelo

- Registros en tabla `webhook_registrations`: URL destino, eventos suscritos, secreto HMAC, activo.
- **Eventos disponibles**: `invoice.created`, `invoice.updated`, `invoice.processed` (transición a procesada).
- Cada entrega se firma con HMAC del cuerpo: cabeceras `X-SixFacturIA-Event` (tipo de evento) y `X-SixFacturIA-Signature` (hex sha256 con el secreto). Quien recibe debe verificar la firma.
- **Reintentos**: backoff exponencial, máximo 3 intentos.
- **Toda entrega se persiste** en `webhook_deliveries`, incluidos los reintentos agotados (dead-letter auditable): éxitos y fracasos. Consulta: `GET /api/v1/webhooks/{id}/deliveries?success=&limit=`.
- Envío: tarea Celery cuando el broker (Redis) responde; si el broker está caído, fallback fire-and-forget con asyncio dentro del proceso API (mismo dispatcher, mismo contrato). Conmutador: `WEBHOOK_CELERY_ENABLED`.

## Relay de eventos entrantes

`POST /api/v1/events/invoice.created` y `POST /api/v1/events/invoice.updated` — endpoints para **re-transmitir** eventos que llegan de sistemas externos hacia los suscriptores registrados (canon de entrada unificado).

## Gestión (API, permiso `webhooks.manage`)

- `GET /api/v1/webhooks` — listar
- `POST /api/v1/webhooks` — crear (valida que los eventos sean de los permitidos)
- `GET /api/v1/webhooks/{id}`
- `PUT /api/v1/webhooks/{id}` — editar URL/eventos/secreto/activo
- `DELETE /api/v1/webhooks/{id}` — 204
- `GET /api/v1/webhooks/{id}/deliveries` — historial de entregas con filtros
- `POST /api/v1/webhooks/{id}/test` — dispara un evento de prueba a esa URL (para verificar conectividad y firma en el receptor)

## Preguntas frecuentes de soporte

- **"El sistema externo no recibe eventos"** → revisar `deliveries` del webhook: si hay reintentos agotados, el problema es del receptor (URL caída, firma inválida, timeout). Probar con `/{id}/test`.
- **"¿Cómo validan la firma?"** → HMAC-SHA256 del body crudo con el secreto, en la cabecera `X-SixFacturIA-Signature`; el nombre del evento viene en `X-SixFacturIA-Event`.
- **"¿Qué eventos existen?"** → Solo los tres de facturas; no hay eventos de pagos ni prefacturas por ahora.
- **"Cambié la URL y llegan dobles"** → editar (PUT) no duplica; si crearon un segundo registro, desactivar/borrar el viejo.
