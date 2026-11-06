# Contrapartes (customers)

Catálogo de clientes y proveedores. Usa este documento cuando el usuario pregunte por clientes/proveedores, duplicados, razones sociales o el "Cliente" de una factura.

## Qué es

Una sola tabla `customers` alimenta ambos lados: el **cliente receptor** de las ventas y el **proveedor emisor** de las compras. Campos típicos: nombre (razón social), tax_id (RUT/RUC/NIF…), país, datos de contacto.

## Auto-creación

- Al subir/importar una factura sin `customer_id`, la contraparte se **busca o crea** desde los datos parseados (receptor en ventas, emisor en compras).
- Un `customer_id` explícito siempre gana sobre lo parseado.
- En compras históricas cargadas desde el registro legacy, el proveedor se resolvió por RUT; el `supplier` de la factura muestra la razón social del proveedor.

## Página web `/customers` (requiere `customers.read`)

- Listado con búsqueda.
- Alta y edición con `customers.write`.
- No hay fusión automática en la UI: los duplicados y renombres puntuales se resolvieron con scripts one-time (merge por tax_id/nombre normalizado, con traza en `raw_data.customer_cleanup` de cada factura movida). Si hoy aparece un duplicado nuevo, toca corrección puntual por API (`PUT /api/v1/customers/{id}`) o pedir al equipo de desarrollo el script correspondiente.

## API

- `GET /api/v1/customers` (lista), `GET /{id}`, `POST` (crear), `PUT /{id}` (editar), `DELETE /{id}` (204).
- `customers.read` para ver, `customers.write` para escribir.

## Preguntas frecuentes de soporte

- **"El cliente de esta factura es otro"** → corregir `customer_id` de la factura por API (`PUT /invoices/{id}`) o crear la contraparte correcta y reasignar; el parseo original queda en `raw_data`.
- **"Hay dos clientes iguales"** → elegir el canónico y reasignar facturas por API; reportar al equipo si son muchos (existe procedimiento de merge documentado para desarrollo).
- **"El proveedor sale como RUT"** → era un placeholder de cargas históricas; al enriquecerse/limpiarse heredó la razón social del emisor. Si persiste, corrección puntual del Customer.
