# API REST

Referencia para integraciones. Base URL: `/api/v1`. Usa este documento cuando el usuario pregunte "¿hay un endpoint para…?" o cómo integrarse programáticamente.

## Autenticación

- **JWT Bearer** en todas las rutas excepto `/api/v1/auth/*`, `/`, `/health` y la documentación OpenAPI.
- Obtener token: `POST /api/v1/auth/login` `{email, password}` → `TokenResponse`.
- `GET /api/v1/auth/me` — perfil del usuario autenticado.
- El registro `POST /api/v1/auth/register` está deshabilitado por defecto (`SELF_REGISTRATION_ENABLED=off`).

## Convenciones

- Toda ruta exige además el permiso granular indicado (`403` si falta).
- Respuestas con esquemas Pydantic v2; errores como `{"detail": "..."}`.
- Colecciones: `skip/limit` (límite 500) o `page/page_size`.
- OpenAPI interactivo: `/docs` (Swagger) y `/redoc` — solo en el proceso API, no anexan la web.

## Facturas — `invoices.read` / `invoices.write`

| Método y ruta | Permiso extra | Descripción |
|---|---|---|
| `POST /invoices/search` | — | Búsqueda avanzada (texto libre + todos los filtros) → `InvoiceSummary[]` |
| `GET /invoices/search/count` | — | Conteo filtrado por tipo/estado |
| `GET /invoices/by-external/{external_id}?source_system=` | — | Buscar por id externo (integraciones) |
| `GET /invoices` | — | Lista con filtros: tipo, status, payment_status, settlement_type, commissionable, currency, customer_id, fechas, min/max_total, search |
| `GET /invoices/{id}` | — | Detalle completo |
| `POST /invoices` | `invoices.write` | Alta manual (JSON) |
| `PUT /invoices/{id}` | `invoices.write` | Edición |
| `DELETE /invoices/{id}` | `invoices.write` | Borrado (204) |
| `POST /invoices/upload` | `invoices.write` | Multipart: `file` + opcionales `customer_id`, `external_id`, `source_system` |
| `GET /invoices/{id}/file` | — | Stream inline del archivo original |
| `POST /invoices/{id}/reparse` | `invoices.write` | Reprocesa el archivo guardado |
| `GET /invoices/{id}/payment-status/preview` | `payments.manage` | Vista previa del cambio de etiqueta (sin escribir) |
| `POST /invoices/{id}/payment-status` | `payments.manage` | Cambio manual; terminales exigen `reason`; override de settlement exige `payments.override` |
| `POST /invoices/extract` | `enrichment.run` | Extracción LLM de un PDF suelto SIN persistir (score + campos + motivos de rechazo + `would_apply`) |
| `POST /invoices/{id}/enrich` | `enrichment.run` | Propone enriquecimiento LLM de una compra (sin aplicar; registra el intento) |
| `POST /invoices/{id}/enrich/apply` | `enrichment.run` | Aplica la propuesta si supera confianza y validaciones |

## Pagos — `payments.manage`

| Método y ruta | Descripción |
|---|---|
| `POST /payments/sync` | Dispara la sincronización con la API de folios (devuelve resumen; auditado) |

## Prefacturas — `preinvoices.read` / `preinvoices.write`

| Método y ruta | Permiso extra | Descripción |
|---|---|---|
| `GET /preinvoices` | — | Lista (skip/limit) |
| `GET /preinvoices/{id}` | — | Detalle |
| `POST /preinvoices` | `preinvoices.write` | Crear (borrador) |
| `PUT /preinvoices/{id}` | `preinvoices.write` | Editar borrador |
| `DELETE /preinvoices/{id}` | `preinvoices.write` | Eliminar borrador |
| `POST /preinvoices/{id}/issue` | `preinvoices.write` | Emitir |
| `POST /preinvoices/{id}/link-invoice` | `preinvoices.write` | Marcar facturada (vincula número de factura) |
| `POST /preinvoices/{id}/mark-paid` | `preinvoices.write` | Marcar pagada |
| `POST /preinvoices/{id}/void` | `preinvoices.write` | Anular (irreversible) |
| `GET /preinvoices/{id}/pdf` | — | PDF |
| `GET /preinvoices/{id}/excel` | `preinvoices.write` | Excel (2 hojas) |
| `GET /preinvoices/lookups/sales-notes` | `salesnotes.read` | NVs para el wizard |
| `GET /preinvoices/lookups/sales-note-items` | `salesnotes.read` | Ítems de NVs |
| `GET /preinvoices/check-number?number=` | — | Verifica si un número de prefactura está libre |

## Notas de venta (legacy, solo lectura) — `salesnotes.read`

| Método y ruta | Descripción |
|---|---|
| `GET /sales-notes?tax_id=&country=&status=` | Lista de NVs del legacy (503 si el puente no está configurado) |
| `GET /sales-notes/{nv_id}/items` | Ítems de una NV |

## Contrapartes — `customers.read` / `customers.write`

`GET /customers` · `GET /customers/{id}` · `POST /customers` · `PUT /customers/{id}` · `DELETE /customers/{id}` (204).

## Importaciones — `imports.read` / `imports.run`

| Método y ruta | Permiso extra | Descripción |
|---|---|---|
| `POST /imports/run` | `imports.run` | Ejecuta una corrida del inbox ahora |
| `GET /imports/batches` | — | Batches con paginación/filtros |
| `GET /imports/batches/{id}` | — | Detalle del batch (archivos y estados) |
| `GET /imports` | — | `imported_files` con filtros (status, source, búsqueda) |

## Webhooks — `webhooks.manage`

`GET /webhooks` · `POST /webhooks` · `GET /webhooks/{id}` · `PUT /webhooks/{id}` · `DELETE /webhooks/{id}` · `GET /webhooks/{id}/deliveries` · `POST /webhooks/{id}/test`.

## Auditoría — `audit.read`

`GET /audit` (filtros: fechas, usuario, path, método, status, acción, recurso + paginación) · `GET /audit/{event_id}`.

## Usuarios — `users.manage`

`GET /users` · `POST /users` · `GET /users/permissions` (catálogo + presets) · `PATCH /users/{id}`.

## Tipos de cambio (público autenticado)

`GET /exchange-rates?base=USD&quote=CLP&date=` — tasa almacenada (404 si no existe; sin histórico previo a la activación del job diario).

## Eventos relay — autenticado

`POST /events/invoice.created` · `POST /events/invoice.updated` — re-transmiten a los suscriptores webhook.

## Códigos de error frecuentes

| Código | Cuándo |
|---|---|
| 401 | Token ausente/expirado |
| 403 | Sin el permiso granular |
| 404 | Recurso inexistente |
| 415 | `/invoices/extract` con archivo que no es PDF |
| 413 | Archivo sobre `UPLOAD_MAX_SIZE` |
| 422 | Validación (p. ej. factura sin datos de contraparte ni `customer_id`) |
| 502 | El LLM no pudo extraer el documento |
| 503 | Legacy/puente no configurado (sales-notes, prefacturas) |

## Preguntas frecuentes de soporte

- **"¿Cómo pruebo la API rápido?"** → `POST /api/v1/auth/login`, copiar `access_token`, pegarlo en `/docs` (Authorize) y probar desde Swagger.
- **"¿Puedo subir con curl?"** → `curl -H "Authorization: Bearer $TOKEN" -F "file=@factura.pdf" https://<host>/api/v1/invoices/upload`.
- **"Subí y quiero saber el id para otra integración"** → el `POST /upload` devuelve el `InvoiceResponse` completo (incluido `id` y `external_id`).
