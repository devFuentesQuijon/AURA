# Facturas (ventas y compras)

Corazón de la plataforma. Usa este documento cuando el usuario pregunte cómo cargar, buscar, ver o corregir facturas, o qué significan los estados.

## Conceptos base

- **Tipo**: `sale` (emitida por una empresa Sixmanager) o `purchase` (recibida de un proveedor). Se detecta automáticamente al parsear comparando el emisor del documento contra las entidades propias configuradas (`COMPANY_IDENTIFIERS`).
- **Tipo de documento**: factura, nota de crédito (`nota_credito`) o nota de débito (`nota_debito`), con código DTE cuando el documento lo trae (33/34/61/56 Chile — SII; 01/07/08 Perú — SUNAT).
- **Emisor vs receptor**: en ventas, el receptor es el cliente y el emisor es la entidad propia (campo `supplier` guarda el emisor del documento); en compras, el emisor es el proveedor.

## Estados de procesamiento (`status`)

| Estado | Significado |
|---|---|
| `processed` | El parser extrajo número y total. Es el estado sano. |
| `pending` | El archivo no dio número + total (escaneo ilegible, PDF sin texto, imagen sin OCR). El motivo queda en `status_note` (visible en el detalle y en el tooltip del badge del listado). |
| `error` | Fallo de procesamiento. También con `status_note`. |

Cualquier factura nueva **de venta** nace además con etiqueta de pago `pendiente` (ver documento de etiquetas de pago). Las de compra no llevan etiqueta de pago.

## Listado `/invoices` (requiere `invoices.read`)

Tabla con columna "Pago" (badges de etiqueta), búsqueda de texto libre (cliente, número, emisor…) con debounce, y panel de filtros:

- Tipo (venta/compra), estado de procesamiento, etiqueta de pago (con sub-filtros por camino de saldo: `Saldado · Pago`, `Saldado · Pago + NC`, `Saldado · NC total`), moneda, tipo de documento/timbre (con conteo por opción), emisor y receptor (multi-selección).
- La tabla se actualiza por HTMX sin recargar la página; la URL se actualiza (se puede compartir).
- Permisos: crear/subir/editar/reprocesar exigen `invoices.write`.

## Alta manual (web)

`/invoices/new` → formulario sin archivo (cliente, número, fechas, moneda, importes, tipo). Útil para registrar una factura de la que no hay PDF.

## Subida de archivo (web y API)

`/invoices/upload` acepta **PDF, imagen (JPG/PNG), XML y JSON**:

- PDF con texto: extracción con pypdf + heurísticas (números tipo `E001-85` / `N°:`, separadores de miles, etiquetas `Sub Total Ventas`/`NETO`/`IGV`/`I.V.A`/`Fecha de Emisión`, RUC/RUT/NIT/NIF/CIF del emisor y receptor…).
- Imagen: OCR con tesseract **si está instalado**; si no, la factura queda `pending` con nota clara.
- XML: UBL 2.1/SUNAT, DTE/SII (Chile) y Facturae 3.x (España), a prueba de XXE; extrae emisor/receptor y referencias (bloque `Referencia` de NCs → tabla `invoice_references`).
- JSON: payloads estructurados (registro vía API/integraciones).
- `customer_id` es **opcional**: si no se indica, la contraparte se busca o crea automáticamente desde los datos parseados (receptor en ventas, emisor en compras). Si no hay datos suficientes ni `customer_id` → error 422.
- Los archivos se guardan en el almacenamiento configurado (Silo/S3 en QA; carpeta local `uploads/` en desarrollo).
- Campos extra opcionales: `external_id`, `source_system`, `storage_key` (ruta destino), `force_type` (forzar venta/compra).

## Detalle `/invoices/{id}`

- Cabecera: número, tipo de documento (código DTE + etiqueta), emisor, cliente, fechas, moneda, neto/impuesto/total.
- **Archivo y procesado**: nombre de archivo, tamaño, fecha de parseo, `status_note` cuando aplica, botón **"Ver archivo"** (sirve el PDF/XML en línea desde el storage) y botón **"Reprocesar archivo"** (vuelve a correr los parsers sobre el archivo guardado; útil tras mejoras del parser sin resubir nada).
- **Estado de pago** (ventas): tarjeta con etiqueta, camino de saldo, saldo, NCs asociadas y si comisiona (detalle en el documento de etiquetas de pago).
- Formulario de cambio de etiqueta de pago (solo `payments.manage`), con **vista previa HTMX** que explica qué pasará antes de confirmar.
- Referencias entre documentos (NC → factura que aplica, cuando se resolvieron).

## Reprocesar (`reparse`)

`POST /invoices/{id}/reparse` (web y API): relee el archivo almacenado y repite el parseo. No sube nada nuevo. Tras un fix del parser sirve para recuperar facturas históricas `pending`/`error` masivamente.

## Ver archivo

`GET /invoices/{id}/file` (web) y `GET /api/v1/invoices/{id}/file` (API) — streaming inline del documento original desde el storage activo.

## Eliminación y edición

- Edición manual de datos (`PUT`) y borrado (`DELETE`, 204) disponibles en API con `invoices.write`.
- Cada alta/actualización dispara webhooks `invoice.created` / `invoice.updated` (y `invoice.processed` al pasar a procesada) si hay suscripciones.

## Preguntas frecuentes de soporte

- **"Subí un PDF y quedó pendiente"** → ver `status_note` en el detalle. Causas típicas: escaneo sin texto, imagen sin tesseract, o PDF corrupto. Opciones: reprocesar, o darla de alta manual y (si aplica) enriquecimiento LLM.
- **"El cliente quedó con nombre raro / duplicado"** → la contraparte se auto-creó del parseo; ver documento de contrapartes para limpieza y renombres.
- **"¿Puedo forzar que sea venta?"** → sí, en la subida por API con `force_type=sale` (así se cargó el histórico de ventas).
- **"No veo el PDF"** → en desarrollo local los PDFs históricos (keys de bucket Silo) no renderizan porque el Minio local está apagado; en QA (instancia autoritativa) sí. Si pasa en QA, revisar `file_path` del registro.
- **"¿Cómo corrijo el total?"** → edición manual por API (`PUT /api/v1/invoices/{id}`) o dar de baja y recargar; la web prioriza reprocesar el archivo.
