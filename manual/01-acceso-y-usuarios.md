# Acceso, sesiones y usuarios

Cómo se entra a SixFacturIA, cómo funciona la sesión y cómo se administran usuarios y permisos. Usa este documento cuando el usuario pregunte por login, contraseñas, "no puedo entrar", "no veo el menú X", o quién puede hacer qué.

## Formas de acceso

Hay dos caminos de autenticación que terminan en la MISMA sesión (la misma cookie y el mismo JWT interno):

1. **Usuario y contraseña locales** — formulario en `/login`. La contraseña se valida contra el hash bcrypt guardado en la tabla `users`.
2. **SSO Microsoft (Entra ID)** — botón "Iniciar sesión con Microsoft" en `/login` (solo visible si el admin habilitó `ENTRA_SSO_ENABLED`). Flujo OIDC estándar: redirige a Microsoft, vuelve a `/auth/microsoft/callback` y, si el correo del usuario ya existe y está activo en SixFacturIA, abre la sesión con la misma cookie.

**Política de SSO (importante para soporte): NO hay alta automática (sin JIT).** Solo entran por Microsoft los correos que un admin ya dio de alta en la plataforma. Si un usuario dice "el botón de Microsoft me devuelve al login", lo casi seguro es que su correo no esté registrado (o esté desactivado). Un admin debe crearlo primero desde `/users`.

## Sesión

- La sesión web es la cookie HttpOnly `sixfacturia_token` que guarda un JWT (mismo token que usa la API REST).
- **Sesión expirada**: en cargas completas de página redirige (303) a `/login`; en peticiones HTMX devuelve 401 con cabecera `HX-Redirect: /login` (el navegador completo va al login, no se carga el HTML del login dentro del panel). Si el usuario reporta "la tabla se quedó cargando" o "clic y no pasa nada", lo primero es pedirle que recargue: casi siempre es sesión vencida.
- **Cerrar sesión**: menú del usuario arriba a la derecha → logout borra la cookie.
- El login local tiene toggle para ver la contraseña y el botón se deshabilita al enviar (evita doble submit).

## Recuperación de contraseña

No existe flujo de "olvido mi contraseña" en la aplicación. Si un usuario la pierde: un admin con `users.manage` debe editarlo en `/users` y asignar una nueva contraseña (o crear la cuenta de nuevo si no existe).

## Roles y permisos (modelo real)

La autorización NO usa el campo `role`; usa la lista `permissions` de cada usuario. El rol es solo una etiqueta/preset a la hora de crear usuarios.

- `*` (wildcard) = superadmin, lo tiene todo.
- **15 permisos granulares**:

| Permiso | Permite |
|---|---|
| `users.manage` | Crear/editar usuarios y permisos (fuera de presets — solo admin o asignación explícita) |
| `invoices.read` | Ver facturas, detalle, archivos y dashboard |
| `invoices.write` | Crear, subir, editar y reprocesar facturas |
| `payments.manage` | Gestionar etiqueta de pago de ventas (fuera de presets) |
| `payments.override` | Sobrescribir el camino de saldo, validación experta (fuera de presets) |
| `customers.read` | Ver contrapartes |
| `customers.write` | Crear, editar y eliminar contrapartes |
| `preinvoices.read` | Ver prefacturas |
| `preinvoices.write` | Crear, editar y convertir prefacturas |
| `salesnotes.read` | Consultar notas de venta del legacy |
| `imports.read` | Ver ejecuciones de importación |
| `imports.run` | Disparar importaciones manuales |
| `enrichment.run` | Proponer/aplicar enriquecimiento LLM y extracción de PDF suelto |
| `webhooks.manage` | Gestionar webhooks y ver entregas |
| `audit.read` | Ver auditoría |

- **Presets** al crear usuario: `admin` (→ `["*"]`), `editor`/`user` (todos menos `users.manage`, `payments.manage`, `payments.override`), `viewer` (solo los `.read`).
- El menú superior se dibuja según `user.can(...)`: si al usuario "le falta una pestaña", le falta el permiso correspondiente.

## Página Usuarios (`/users`, requiere `users.manage`)

- Tabla de usuarios: email, nombre/rol, estado activo/inactivo.
- **Alta**: email, contraseña (o cuenta sin contraseña para uso exclusivo SSO — queda con hash aleatorio irrecuperable), preset o checkboxes de permisos sueltos.
- **Edición**: cambiar nombre, permisos, activar/desactivar (`is_active`), restablecer contraseña.
- **No hay baja física**: se desactiva. Esto preserva la auditoría histórica (quién hizo qué).
- Un admin **no puede quitarse `users.manage` a sí mismo** desde la web (evita quedarse fuera).
- API equivalente: `GET/POST /api/v1/users`, `PATCH /api/v1/users/{id}`, `GET /api/v1/users/permissions`.

## Registro libre

`POST /api/v1/auth/register` existe en la API pero está apagado por defecto (`SELF_REGISTRATION_ENABLED=off`). Los usuarios los crea un admin, coherente con el SSO sin alta automática. El primer admin se crea por línea de comandos: `python -m scripts.create_admin <email> <contraseña>`.

## Preguntas frecuentes de soporte

- **"Entro por Microsoft y vuelvo al login"** → el correo no está dado de alta o está inactivo. Verificar en `/users`; dar de alta/activar.
- **"No me aparece la pestaña Prefacturas/Usuarios/etc."** → permisos. Revisar la lista `permissions` del usuario.
- **"Me desloguea a cada rato"** → expiración del JWT; volver a entrar. Si es constante en minutos, revisar la config de expiración del token con el administrador de la plataforma.
- **"Quiero que X solo vea"** → preset `viewer` (lectura de facturas, contrapartes, prefacturas, importaciones y auditoría).
- **"Cómo doy acceso a marcar pagos"** → asignar `payments.manage` explícitamente (no viene en ningún preset); para sobrescribir caminos de saldo además `payments.override`.
