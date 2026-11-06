# Importaciones

Cómo entran los documentos a SixFacturIA de forma masiva. Usa este documento cuando el usuario pregunte por el inbox, SharePoint, cargas históricas, "archivos fallidos" o el batch de importación.

## Fuentes

| Fuente | Descripción | Cuándo corre |
|---|---|---|
| `local` (inbox) | Carpeta `data/inbox/` (dev/montaje) — barre archivos, crea facturas, archiva | Beat cada `IMPORT_INTERVAL_SECONDS` + manual |
| `sharepoint-xml` | Drive legacy de producción (solo lectura) — XML de DTE | Beat programado |
| `sharepoint-pdf` | Mismo drive — adjuntos PDF de las ventas | Beat programado (desfasado +150 s del XML) |
| `historical` | Volcado one-time del árbol histórico de ventas (PDF) | Manual/Celery |
| `historical-purchase` | Volcado one-time del árbol de compras (PDF por RUT proveedor) | Manual/Celery |
| `legacy-dtc` | Backfill one-time desde el registro de compras legacy | Ya corrido (nov-2026) |
| `sii-rc` | Import one-time del Registro de Compras del SII (JSON por período) | Ya corrido (nov-2026); períodos futuros a mano |
| `legacy` | Prefacturas históricas del legacy | Ya corrido |

## Comportamiento del inbox

1. Descarga cada archivo, calcula sha256.
2. **Dedup** por `(source, file_name, sha256)` sobre registros `processed`: si ya existe → mueve a `archive/AAAA/MM/` y marca `duplicate`. Re-correr es idempotente.
3. Si no: crea la factura vía el mismo servicio que la subida manual (con auto-creación de contraparte), archiva a `archive/AAAA/MM/`.
4. Si falla (parseo 422, error inesperado): mueve a `failed/AAAA/MM/` y guarda el motivo (`error_message`).

Fuentes SharePoint:
- **XML**: único creador — match-or-create, enriquece 2026+ por RUT del emisor. Estados de archivo: `created` / `enriched`.
- **PDF**: **nunca crea facturas** — resuelve folio + emisor, sube el PDF al storage bajo `ventas/{emisor_id}/{año}/{mes}/` y lo adjunta al registro existente. Si el XML aún no llegó: archivo `skipped` (se reintenta ~24 h por ventana de solapamiento).

## Página web `/imports` (requiere `imports.read`)

- **Ejecutar ahora** (`imports.run`): botón "Importar ahora" — dispara una corrida del inbox y muestra el resumen (`N procesados · N duplicados · N fallidos`). También `POST /api/v1/imports/run`.
- **Ejecuciones (tab) `/imports/batches`**: tabla de `import_batches` con fecha, fuente, trigger (beat/manual), conteos (procesados/duplicados/fallidos/skipped) y detalle. El detalle del batch lista archivo por archivo con su estado y, si falló, el motivor exacto.
- **Archivos**: histórico de `imported_files` con filtros (estado `processed`/`duplicate`/`failed`/`skipped`, fuente). El filtro **Fallido** es el lugar correcto para revisar errores de corridas anteriores (la tarjeta del dashboard solo apunta a la última).

## Estados de archivo

| Estado | Significado |
|---|---|
| `processed` | Importado (o PDF adjuntado) con éxito |
| `duplicate` | Ya existía (mismo nombre + contenido) — archivado sin recrear |
| `failed` | Error; archivo en `failed/`, motivo registrado |
| `skipped` | PDF SharePoint cuyo XML aún no había llegado — se reintenta |
| `created` / `enriched` | XML SharePoint: factura nueva / factura existente enriquecida |

Toda corrida (incluso vacía) queda auditada (`GET /api/v1/imports/batches`).

## Cargas one-time ya ejecutadas (contexto para soporte)

- **Ventas históricas**: todo PDF parseable del árbol legacy → `sale` forzado bajo `ventas/{unidad}/{año}/{mes}/`; los no parseables quedaron con registro `failed`.
- **Compras históricas**: por RUT proveedor; los digitales parseados (`processed`), los escaneados (~77%) desde metadatos del path con estado `pending`.
- **Backfill legacy DTC + Registro SII**: compras sin PDF (estado `pending`, `raw_data.awaiting_file=true` cuando el legacy referencia un PDF aún no volcado). Hay nómina de folios pendientes de PDF.

## Preguntas frecuentes de soporte

- **"Puse un archivo en inbox y no aparece"** → esperar al beat o usar "Importar ahora"; verificar que no esté duplicado (mismo nombre+contenido) y revisar `/imports` → Archivos por si quedó `failed`.
- **"¿Por qué hay archivos en failed?"** → abrir el detalle: el motivor (`error_message`) dice la causa (p. ej. `422: no se pudo extraer número y total`).
- **"Quiero recargar todo"** → no hace falta borrar nada: el dedup hace las re-corridas idempotentes; los `failed` se reintentan solos al re-correr.
- **"El PDF de una venta no se ve"** → si es de SharePoint, el adjuntado es async respecto del XML (estado `skipped` hasta que resuelve); revisar la corrida PDF del batch.
- **"¿Puedo importar una carpeta de mi equipo?"** → en desarrollo copiando a `data/inbox/`; en QA el inbox es la integración con SharePoint. Para volcados puntuales existe el procedimiento de carga histórica (lo ejecuta el equipo de desarrollo).
