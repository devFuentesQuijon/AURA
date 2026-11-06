# FAQ y solución de problemas

Respuestas rápidas por síntoma. Si no encuentras el caso, deriva al documento del módulo correspondiente.

## Acceso y sesión

**No puedo entrar / me devuelve al login**
- ¿Local o Microsoft? Microsoft: solo correos ya dados de alta y activos (no hay alta automática). Pedir a un admin que revise `/users`.
- Local: verificar email/contraseña; si la olvidó, no hay recuperación: un admin (`users.manage`) debe asignar una nueva.
- Sesión expirada: recargar e identificarse de nuevo.

**Me desloguea constantemente**
- Normal por expiración del JWT. Si ocurre en minutos, revisar con el admin la vigencia del token.

**No veo una pestaña del menú**
- Faltan permisos. El menú se dibuja según permisos (ver documento 01). P. ej. "Usuarios" exige `users.manage`; "Auditoría" exige `audit.read`.

## Facturas

**Subí un PDF y quedó "Pendiente"**
- Abrir el detalle y leer la nota de estado (`status_note`). Causas: escaneo sin texto, imagen sin OCR, XML sin número/total. Reintentar "Reprocesar archivo"; si persiste, alta manual o enriquecimiento LLM (compras).

**El total/neto/impuesto está mal**
- "Reprocesar archivo" tras mejoras del parser; si el documento realmente trae otro valor, edición por API (`PUT /invoices/{id}`) o dar de baja y recargar.

**No se ve el PDF ( Ver archivo )**
- En desarrollo local los históricos (Silo) no renderizan (Minio local apagado) — probar en QA. En QA, si `file_path` apunta a un objeto inexistente, reportar al equipo.

**Quiero que una compra figure como venta (o al revés)**
- En la subida por API: `force_type=sale|purchase`. Ya cargada: `PUT` del campo `type` o reprocesar con force (equipo).

**¿Cómo elimino una factura duplicada?**
- `DELETE /api/v1/invoices/{id}` con `invoices.write`. Las importaciones duplicadas por sí solas no recrean la factura (marcan `duplicate`).

## Pagos (etiquetas)

**La venta sigue "Pendiente" y ya cobraron**
- Botón "Sincronizar pagos". Si la API de folios no la reporta (no es folio 33, año distinto), cambio manual con `payments.manage`.

**Marqué saldado y ahora dice "Pago + NC"**
- Re-verificación automática a 7 días al llegar la NC: el estado sigue saldado, cambió el camino. Corregir el camino solo con `payments.override` (usuario experto).

**Una NC queda "Pendiente" con su factura saldada**
- Caso borde: la NC entró después del cierre. Cambio manual sobre la NC con motivo (auditado).

**No me deja sobrescribir el camino de saldo**
- Se requiere el permiso extra `payments.override` además de `payments.manage`.

## Prefacturas

**No aparece la nota de venta en el buscador**
- Comprobar número exacto (admite varios separados por coma). ¿NV del Perú? El módulo legacy de prefactura es solo Chile. ¿Puente caído? El aviso lo indica y se puede continuar manualmente.

**Emití y quiero corregir algo**
- Emitida → se anula (confirmación, irreversible) y se crea de nuevo. Borrador → se edita libremente.

**El total de la glosa no cuadra con los ítems**
- El semáforo del wizard (RN-10) muestra la diferencia con tolerancia ± configurada; ajustar líneas de glosa hasta cuadrar.

## Importaciones

**Puse archivos y no aparecen facturas**
- ¿Corrida ejecutada? Botón "Importar ahora". Revisar `/imports` → Archivos: pueden estar `failed` (motivo en el detalle) o `duplicate` (mismo nombre+contenido ya procesado).

**Fallidos en el dashboard**
- Es solo la última corrida con fallos. El histórico completo: Importaciones → Archivos, filtro Fallido.

## Auditoría / integraciones

**¿Quién hizo este cambio?**
- `/audit`, filtrar por recurso o acción (`payment_status_change` para etiquetas de pago). Los eventos de dominio incluyen motivo y valores.

**El sistema externo no recibe webhooks**
- `GET /webhooks/{id}/deliveries`: reintentos agotados = problema del receptor. Probar con `POST /webhooks/{id}/test`.

**"503 LegacyError" al consultar notas de venta o plantillas**
- El puente SIX-LegacyBridge no está configurado o no responde. Para soporte de infra: `LEGACY_BRIDGE_URL` debe apuntar a la IP de la VM (no localhost) desde los contenedores.

## Errores de la API

- **401**: token vencido/ausente → renovar con `/auth/login`.
- **403**: falta permiso granular (no basta con "ser admin de datos"; revisar `permissions`).
- **422 en upload**: el parseo no produjo contraparte y no vino `customer_id` → indicar `customer_id` o subir un documento legible.
- **503**: dependencia externa no configurada (legacy, LLM).

## Qué NO hacer (límites por diseño)

- No se pueden editar prefacturas emitidas (anular y rehacer).
- No hay baja física de usuarios (desactivar) ni de contrapartes fusionadas (cascarones vacíos).
- El sync de pagos nunca reescribe `saldado`/`incobrable`.
- Las monedas nunca se suman/convierten en dashboard ni listados.
- No hay registro público: los usuarios los crea un admin.
