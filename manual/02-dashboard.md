# Dashboard

Página de entrada `/` (requiere `invoices.read`). Usa este documento cuando el usuario pregunte qué significa una tarjeta, un número o un gráfico del panel principal.

## Selector de período

Arriba a la derecha: **Mes actual · Mes anterior · Últimos 90 días · Año · Todo**. Toda la sección "Resumen del período" (tarjetas, tendencias, top clientes/proveedores, ventas por emisor, gráficos) obedece ese filtro.

## Sección "Requiere atención" (3 tarjetas siempre actuales)

Aviso visible en la propia pantalla: estas tarjetas son **estado puntual del sistema, NO del período seleccionado**.

1. **Facturas pendientes de procesar** — facturas con estado `pending` (el parser no logró extraer número + total). Click lleva a `/invoices?status=pending`.
2. **Facturas con error** — estado `error`. Click a `/invoices?status=error`.
3. **Fallidos en la última importación** — archivos fallidos de la ejecución MÁS RECIENTE que tuvo fallos. **No es un acumulado**: los fallos de corridas anteriores solo se ven en Importaciones → Archivos con filtro "Fallido". Click a `/imports?status=failed`. Muestra la fecha de esa corrida.

## Resumen del período

- **Importe total** — suma de `total_amount` del período, **una línea por moneda**. Las monedas nunca se convierten ni suman entre sí.
- **Ventas (n)** — documentos tipo venta del período y su monto por moneda.
- **Compras (n)** — documentos tipo compra del período y su monto por moneda.

Nota sobre fechas: las facturas sin `issue_date` (fecha de emisión) cuentan en los totales cuando no hay rango de fechas, pero quedan fuera de cualquier filtro por fecha y de la tendencia mensual (su mes de emisión es desconocido).

## Ventas por emisor

Tarjetas por entidad emisora propia (empresa + país) con total y conteo por moneda; click en una tarjeta filtra las facturas de ese emisor.

## Gráficos

- **Tendencia mensual** (barras apiladas ventas vs compras) con selector de moneda: cada moneda es una serie independiente.
- **Ventas vs compras** (dona) del período.
- **Distribución por moneda**.
- **Top 5 clientes** (ventas) y **Top 5 proveedores** (compras).

## Preguntas frecuentes de soporte

- **"El total del dashboard no cuadra con el de facturas"** → revisar el período seleccionado en ambas pantallas y recordar que las monedas se listan por separado (no hay conversión a una moneda única).
- **"¿Por qué una factura no aparece en la tendencia mensual?"** → no tiene fecha de emisión registrada; abrir el detalle y reprocesar el archivo o corregir la fecha.
- **"La tarjeta de fallidos sigue ahí"** → es deliberado: apunta al último batch con fallos hasta que una corrida posterior salga limpia. Ver el detalle en Importaciones.
