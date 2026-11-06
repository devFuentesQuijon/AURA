# SixFacturIA — Manual de conocimiento para asistente de soporte

Este directorio alimenta a una instancia de chat que responde preguntas de los usuarios de SixFacturIA. Cada documento es un módulo funcional autocontenido; el asistente debe leer el documento del módulo antes de responder sobre él.

## Cómo usar este manual (instrucciones para el asistente)

1. **Identifica el módulo** de la pregunta (acceso, facturas, pagos, prefacturas, importaciones…) y responde solo con la información de ese documento.
2. **No inventes funcionalidades**: si algo no está cubierto aquí, dilo y deriva al equipo de desarrollo.
3. **Las instrucciones operativas para el usuario son las de la interfaz web** (`/login`, `/invoices`, `/preinvoices`, …); los endpoints son para integraciones/API. No mezcles ambos planos salvo que el usuario pida la API.
4. **Los permisos mandan**: antes de indicar un paso, verifica que el rol del usuario lo permite; si no sabes su rol, explica qué permiso se necesita (`payments.manage`, `invoices.write`, etc.).
5. **Versión y fecha**: este manual refleja el estado del producto a nov-2026. Los volcados one-time (históricos, backfill legacy, Registro SII) ya se ejecutaron; no indiques al usuario que los repita.

## Mapa del producto

SixFacturIA es la plataforma de facturación de SIXMANAGER. Interfaz web server-rendered (FastAPI + HTMX) + API REST (`/api/v1`), con base PostgreSQL, storage de objetos (Silo/S3) para los PDF/XML, tareas Celery/Redis en segundo plano e integración con el legacy SixERP vía puente SIX-LegacyBridge.

Navegación principal (menú superior): **Dashboard · Facturas · Prefacturas · Contrapartes · Importaciones · Auditoría · Usuarios** (las pestañas visibles dependen de los permisos del usuario).

## Índice de documentos

| Doc | Módulo | Preguntas típicas |
|---|---|---|
| [01-acceso-y-usuarios](01-acceso-y-usuarios.md) | Login, SSO Microsoft, sesión, usuarios y permisos | "No puedo entrar", "no veo el menú", "cómo doy de alta a alguien" |
| [02-dashboard](02-dashboard.md) | Panel principal: KPIs, períodos, gráficos, tarjetas de atención | "Qué significa esta tarjeta", "por qué no cuadra el total" |
| [03-facturas](03-facturas.md) | Ventas/compras: carga, estados, detalle, reprocesar, ver archivo | "Subí un PDF y quedó pendiente", "cómo corrijo el total" |
| [04-etiquetas-de-pago](04-etiquetas-de-pago.md) | Seguimiento de cobro de ventas CL folio 33: sync, manual, NCs, comisiones | "La factura sigue pendiente y ya pagaron", "quién cambió la etiqueta" |
| [05-prefacturas](05-prefacturas.md) | Pre-facturación: wizard, NVs, glosas, estados, PDF/Excel, tipos de cambio | "Cómo facturo una NV", "emití y me equivoqué" |
| [06-contrapartes](06-contrapartes.md) | Clientes/proveedores | "Cliente duplicado", "el proveedor sale como RUT" |
| [07-importaciones](07-importaciones.md) | Inbox, SharePoint, históricos, batches, estados de archivo | "Archivos en failed", "el PDF no se adjuntó", "importar ahora" |
| [08-webhooks](08-webhooks.md) | Notificaciones HTTP salientes y relay de eventos | "El sistema externo no recibe eventos", "cómo valido la firma" |
| [09-auditoria](09-auditoria.md) | Registro de actividad y eventos de dominio | "Quién cambió X", "cómo reviso el historial" |
| [10-api-rest](10-api-rest.md) | Referencia de endpoints, auth, permisos, códigos de error | "¿Hay un endpoint para…?", "cómo integro" |
| [11-faq-y-solucion-de-problemas](11-faq-y-solucion-de-problemas.md) | Respuestas rápidas por síntoma + límites por diseño | Punto de entrada rápido para soporte |

## Glosario mínimo

- **DTE**: documento tributario electrónico. Códigos SII Chile: 33 factura, 61 NC, 56 ND, 34 factura exenta, 39/41 boletas. SUNAT Perú: 01 factura, 07 NC, 08 ND.
- **Folio**: número oficial del DTE asignado por el SII. La API de saldos solo reporta folios 33.
- **Emisor**: quien emitió el documento (entidad propia en ventas, proveedor en compras). En la base, `invoice.supplier` guarda el emisor.
- **Receptor / contraparte**: `customer` de la factura (cliente en ventas).
- **Etiqueta de pago**: estado de cobro de una venta (`pendiente/parcial/saldado/incobrable`).
- **Camino de saldo (`settlement_type`)**: cómo se cerró la deuda — `payment` (pago), `mixed` (pago + NC), `full_credit` (NC total).
- **Pool de sync**: ventas CL ≥2026, tipo factura, con etiqueta `pendiente` o `parcial` — las únicas que la sincronización revisa.
- **Batch**: corrida de importación registrada en `import_batches` con sus archivos (`imported_files`).
- **Beat**: scheduler Celery que dispara tareas periódicas (importaciones, sync de pagos, tipos de cambio).
- **QA / instancia autoritativa**: el entorno real de uso (VM 10.121.105.51, app en puerto 5200). El entorno local de desarrollo no renderiza los PDFs históricos del Silo.

## Datos de configuración que el asistente NO debe pedir nunca al usuario

Las claves y secretos no viajan con el usuario ni se pegan en el chat: API key de folios, credenciales SharePoint/Entra, secretos de webhooks, DSN del legacy. Si un procedimiento los requiere, indicar que los administra el equipo de plataforma.
