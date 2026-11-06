# Etiquetas de pago (ventas, folios Chile)

Sistema de seguimiento de cobro por factura de venta. Usa este documento cuando el usuario pregunte por "pagos", "saldos", "facturas cobradas", "notas de crédito aplicadas" o la sincronización con la API de folios.

## Dónde vive

Columnas propias de `invoices` (visibles en el listado, columna "Pago", y en el detalle, tarjeta "Estado de pago"):

| Campo | Qué es |
|---|---|
| `payment_status` | `pendiente` · `parcial` · `saldado` · `incobrable` |
| `payment_status_source` | Quién puso la etiqueta: `api` (sync), `manual`, `backfill` (histórico), `propagated` (derivada de NC) |
| `payment_balance` | Saldo que reporta la API de folios |
| `settlement_type` | Camino del saldo: `payment` (pago) · `mixed` (pago + NC) · `full_credit` (NC total) — solo en `saldado` |
| `settlement_refs` | NCs asociadas (folio, monto, código de referencia) + `paid_amount` derivado |
| `commissionable` | Si comisiona (base del futuro módulo de comisiones) |

**Solo aplica a ventas chilenas ≥ 2026, tipo factura (folio 33).** Compras: todo NULL. NCs y NDs no se sincronizan directamente: su etiqueta se **deriva** (ver más abajo).

## Cómo se actualiza

### 1. Sincronización automática (fuente `api`)

Celery consulta la API interna de folios (`GET /dtes/saldos?folios=…`) con lotes, **solo sobre el pool pendiente + parcial**. Programación: lunes a viernes 11:50 y 01:00 (hora Santiago) — dos corridas porque la NC suele llegar minutos después de saldar.

- `saldado` e `incobrable` son **terminales**: el sync jamás los reescribe (solo una re-verificación a 7 días puede ajustar el *camino*, no el estado).
- Al saldar se deriva el camino: `paid_amount = MontoTotal − Σ NCs con movimiento`. Sin NC → `payment` (comisiona); NC + pago → `mixed` (comisiona como pago completo); solo NC → `full_credit` (no comisiona).

### 2. Sincronización manual

Botón "Sincronizar pagos" (web) o `POST /api/v1/payments/sync` (API). Requiere `payments.manage`. Audita quién la disparó. Útil tras saber que entró un pago y no querer esperar al schedule.

### 3. Cambio manual (fuente `manual`)

En el detalle de la factura, con `payments.manage`:

- Al elegir la etiqueta en el formulario aparece una **vista previa HTMX** (sin escribir nada) que muestra: camino derivado con evidencia y método (referencia/heurística), abonos si marcas `incobrable`, efecto sobre las NCs propagadas y avisos si la factura reingresaría al pool de sync.
- **Terminales (`saldado`, `incobrable`) exigen motivo** (obligatorio).
- Marcar `incobrable` congela `paid_amount` = total − último saldo conocido (comisiona lo efectivamente cobrado).
- **Sobrescribir el `settlement_type` a mano exige además `payments.override`** (el selector solo se dibuja para quien lo tiene; API y web lo rechazan sin él). Es la válvula de escape del usuario experto.

### 4. Histórico (`backfill`)

Import one-time desde el JSON histórico de folios: `1 Emitida`→pendiente, `2 Pagada`→saldado/payment (mixed si el JSON traía NC), `3 Anulada`→saldado/full_credit. Los ids `*RG*` (folios ficticios de cuadratura) quedan fuera.

## Propagación a notas de crédito

La API de folios no trackea NCs, así que su etiqueta se **deriva del estado de las facturas que referencian** (vía `invoice_references`):

- Todas sus facturas terminales → NC `saldado` (aplicada).
- Alguna → NC `parcial`.
- Ninguna → NC `pendiente`.
- Fuente `propagated`, con auditoría `reason="factura X → estado"`.
- Respeta etiquetas manuales previas de la NC y **excluye NDs** (las NDs tienen vida propia).
- Se dispara desde el sync y desde cambios manuales.

**Caso borde conocido**: una NC importada *después* de que su factura ya cerró ciclo no dispara propagación (la factura no cambió de estado) y queda `pendiente` → se corrige con el cambio manual (con motivo, auditado). La re-verificación de 7 días cubre el caso normal (NC que llega minutos después).

## Reporte de castigo (solo lectura)

Endpoint web/API con pendientes + parciales más antiguos que el umbral configurado (`PAYMENT_STALE_DAYS`) — candidatos a castigar. No modifica nada.

## Eventos de auditoría de dominio

`payment_status_change` (from/to/source/reason/settlement/commissionable) y `payment_status_sync` (resumen por corrida). Se persisten y además se imprimen en stdout como JSON para el colector de logs.

## Configuración relevante (admin)

`PAYMENT_STATUS_API_URL` (vacío = feature off), `PAYMENT_STATUS_BATCH_SIZE`, `PAYMENT_SETTLEMENT_RECHECK_DAYS` (7), `PAYMENT_STALE_DAYS`, API key de la API de folios (la setea el admin a mano; **nunca en el repo**).

## Preguntas frecuentes de soporte

- **"La factura sigue pendiente y ya pagaron"** → botón "Sincronizar pagos"; si la API de folios no la reporta (folio distinto, año distinto, no es 33), cambio manual con `payments.manage`.
- **"Puse saldado y ahora dice Pago + NC"** → el camino se recalculó con la re-verificación de 7 días al llegar la NC; el estado sigue saldado, solo cambió el camino. Corregir solo con `payments.override`.
- **"La NC quedó pendiente pero su factura está saldada"** → caso borde de arriba: cambio manual sobre la NC.
- **"¿Por qué no comisiona esta saldada?"** → camino `full_credit` (cubrió toda la deuda con NC) — por diseño no comisiona.
- **"¿Quién cambió esta etiqueta?"** → Auditoría, acción `payment_status_change` (usuario, motivo, valores).
- **"¿Puedo marcar compras como pagadas?"** → No: las etiquetas de pago son solo de ventas CL folio 33.
