# Prefacturas

Módulo funcional completo de pre-facturación (reemplaza al módulo legacy). Usa este documento cuando el usuario pregunte cómo facturar una nota de venta, cuotas, glosas, PDF/Excel o el ciclo de vida de una prefactura.

## Ciclo de vida (estados)

```
draft ──emitir──▶ issued ──facturar──▶ invoiced ──marcar pagada──▶ paid
  │                  │                    │
  └──anular──────────┴────anular─────────┴────anular────▶ voided (terminal)
```

| Estado | Significado |
|---|---|
| `draft` | Borrador; se puede editar y anular sin efectos |
| `issued` | Emitida (aprobada); ya no editable, se puede anular |
| `invoiced` | La factura real ya se generó (número de factura vinculado) |
| `paid` | Pagada (terminal) |
| `voided` | Anulada (terminal, **no se puede deshacer** — pide confirmación) |

Las transiciones se hacen desde el detalle con los botones "Emitir prefactura", "Marcar como facturada" (pide el número de factura), "Marcar como pagada" y "Anular". En la web cada POST→GET commitea explícitamente y muestra banner de resultado.

## Crear una prefactura (wizard web `/preinvoices/new`)

Pantalla única con búsqueda HTMX en vivo y **resumen vivo** (totales que se recalculan al cambiar ítems/TC). Bloques:

1. **Datos**: N° prefactura, emisor (define país y moneda base), orden de compra, plazo en días, razón social del cliente (selección libre entre todas las contrapartes — sin grupos) o alta rápida de una nueva (nombre + RUT/RUC), comentario opcional.
2. **Notas de venta e ítems**: buscar NVs por número (separadas por coma) → el sistema trae sus ítems; elegir ítems a facturar. El bloque **"Prefacturas de la razón social"** ofrece reusar las últimas 3 del legacy como plantilla (precarga OC, plazo, glosa y comentario-ref; **nunca muta la original**). Ítems de plantilla vienen sin vínculo a NV → quedan como panel de referencia; los ítems reales se eligen vía NVs para conservar el vínculo de cuotas. Si el puente legacy está caído: "continuar manualmente" (no bloquea).
3. **Glosa**: lo que verá el cliente — líneas con Ref., Concepto/Descripción y Total a facturar; cada línea se refleja en la vista previa.
4. **Vista previa y guardar**: botón "Vista previa" (POST HTMX) muestra el documento tal cual sale; "Guardar" crea como borrador.

Reglas que el servidor aplica automáticamente (el usuario no las calcula):

- **Cuota** = consolidadas + 1 (bloqueo SELECT FOR UPDATE para concurrencia).
- **Montos** calculados en servidor (neto, impuesto, total).
- **Tipo de cambio** por fecha con `was_modified` derivado si alguien lo alteró.
- **Semáforo RN-10**: las glosas deben cuadrar con los ítems ± tolerancia configurada (`PREINVOICE_TOTAL_TOLERANCE`); el wizard lo muestra en vivo.

## Detalle `/preinvoices/{id}`

- Glosa final.
- Ítems de NV (snapshot congelado al crear; los marcados "modificado" tuvieron valor o TC alterados respecto de la NV).
- Distribución por NV (cuánto se le factura a cada una).
- Datos: fechas de emisión/pago/anulación, factura vinculada.
- Acciones: **Descargar PDF** (weasyprint, plantilla formal), **Descargar Excel** (openpyxl, 2 hojas), Emitir / Facturar / Pagar / Anular según estado.
- Comentarios (línea de tiempo).

## Notas de venta (legacy)

Solo lectura, vía puente SIX-LegacyBridge (canal canónico): `GET /api/v1/sales-notes` (filtros tax_id/country/status) e ítems por NV. La web los consume en vivo con caché de 45 s. Sin puente configurado → 503 con mensaje claro. Las NVs importadas del legacy conservan su histórico (`source='legacy'` en prefacturas; import one-time idempotente).

## Tipos de cambio

Tablas propias + providers públicos (Chile: Mindicador USD/UF→CLP; Perú: apis.net.pe USD→PEN si hay token; Colombia: TRM USD→COP). Job diario 09:00 Santiago, con short-circuit (si ya hay tasa del día no llama a la API) y snapshot idempotente. Consulta: `GET /api/v1/exchange-rates?base=USD&quote=CLP&date=` (404 si no hay tasa guardada). No hay histórico previo a la activación del job.

## Preguntas frecuentes de soporte

- **"No encuentro la NV"** → comprobar el número exacto (se acepta lista separada por comas); si es del Perú, recordar que el módulo legacy de prefactura es solo Chile; si el puente está caído, el aviso lo dice: continuar manualmente.
- **"El total no me cuadra con la NV"** → revisar el semáforo del wizard y el snapshot del detalle (ítems "modificado" y distribución por NV).
- **"Emití y me equivoqué"** → se puede anular desde issued/invoiced (confirmación, irreversible). Si ya está `paid` o `voided`, no hay vuelta atrás: crear una nueva.
- **"¿Puedo editar una emitida?"** → No; el flujo es anular y rehacer.
- **"El PDF/Excel no descarga"** → requiere sesión válida; revisar pop-up/blocker del navegador. Excel exige `preinvoices.write` en la API.
- **"¿Cómo facturo la cuota 2 de una NV?"** → el contador de cuotas se lee en vivo del legacy; el sistema propone consolidadas+1 automáticamente.
