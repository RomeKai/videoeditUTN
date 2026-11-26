# Contrato de la API de OneCreator

> Archivo generado desde `src/api/contracts/*.ts` con `npm run contract:doc`. No lo edites a mano: cambiá el contrato y volvé a generarlo. Un test falla si queda desactualizado.

Este es el contrato de integración entre el frontend (`front/`) y el backend Django (`back/`). Cada endpoint está descripto una sola vez, con esquemas [zod](https://zod.dev); de ahí salen los tipos de TypeScript, los hooks (`src/api/hooks/`), los mocks (`src/api/mocks/`) y este documento.

**80 endpoints:** 14 existen hoy en el backend, 3 existen en parte y 63 son propuestos para los huecos de la [sección 8 del mapa de pantallas](../../../documents/producto/mapa-de-pantallas.md#8-huecos-de-backend).

## Cómo leerlo

- **✅ existe:** los nombres de los campos están copiados del serializer o la vista de DRF que se cita.
- **⚠️ existe en parte:** el endpoint está, pero al frontend le falta algo (se dice qué en la nota).
- **❌ falta (propuesto):** no existe. La forma es una propuesta REST para el hueco indicado; si el backend la cambia, se cambia primero en `src/api/contracts/`.
- Un campo con `?` es opcional. Los tipos con nombre (`Project`, `Clip`…) están detallados en [Esquemas](#esquemas).

## Convenciones

- **Base:** `NEXT_PUBLIC_API_URL` (por defecto `http://localhost:8000`). Las rutas llevan la barra final de DRF.
- **Sesión:** `Authorization: Bearer <access>` con el JWT de `POST /api/token/`. Ante un 401 el cliente renueva una vez con `POST /api/token/refresh/` (el backend rota el refresh) y repite el pedido; si la renovación falla, abre el diálogo de sesión vencida.
- **Tiempo de espera:** un pedido sin respuesta completa en 30 s (`REQUEST_TIMEOUT_MS`) se corta y falla como error de red (`NetworkError`, con el mismo reintento y el mismo mensaje). Los que siguen trabajando en el servidor después de recibir el pedido esperan 180 s (`timeoutMs`): crear un proyecto (valida el video, lo mueve al storage, calcula el precio y reserva), guardar o duplicar un brand kit, armar el .zip y exportar los movimientos. En una subida multipart el reloj arranca cuando el archivo terminó de salir; mientras sube, se corta si pasa un minuto sin avanzar. Un pedido que cambia algo (todo lo que no es `GET`) y vence **puede haberse hecho**: `NetworkError.outcomeUnknown` lo marca, la UI dice «Puede que se haya hecho igual: revisá antes de probar de nuevo» en lugar de «No pudimos conectar», y vuelve a leer los datos.
- **Workspace activo:** el cliente manda `X-Workspace-Id` en cada pedido con sesión, y el backend lo respeta (PR #125): las listas traen solo lo de ese workspace, y un id mal formado, inexistente o de un workspace donde el usuario no es miembro **activo** (una invitación pendiente no cuenta) responde 404 «No encontramos ese espacio de trabajo.». Sin el header usa el workspace más antiguo del usuario. Al cambiar de workspace el cliente primero sale de la pantalla y recién después cambia el id, para que ninguna pida su dato en el workspace equivocado.
- **Roles:** el backend aplica la tabla 1.7 del mapa (PR #125). El Lector solo lee; el Editor crea y edita, y borra solo los proyectos que subió; redes, miembros, plan y nombre del workspace son del Dueño y los Admin. Un rol que no alcanza recibe 403 `{ detail }` con una frase en español («Con el rol Lector podés ver, pero no crear ni cambiar nada. Pedile acceso a un admin.»), sin `code`: la UI la muestra tal cual y nunca la confunde con un límite del plan (D3), que se reconoce por su `code` o por el texto de `PricingEngine`.
- **JSON en snake_case**, como el backend. Ids UUID. Fechas ISO 8601 en UTC.
- **Decimales:** un `DecimalField` de un serializer llega como string (`"4860.0000000000"`); un `Decimal` que la vista pone directo en la respuesta (`tokens_reserved`, `final_cost`, `required`) llega como número, porque el encoder de DRF lo pasa a float. El esquema `Decimal` acepta los dos y entrega un número.
- **Colores:** el backend los guarda como texto libre de hasta 7 caracteres. Los pedidos mandan siempre `#RRGGBB` (`HexColor`); las respuestas aceptan cualquier texto (`ColorValue`) y la UI los normaliza con `normalizeHex()`.
- **Booleanos en multipart:** DRF lee como `false` un booleano que no viene en un formulario multipart, sin mirar el default del modelo. `add_subtitles` es la excepción desde el PR #121 del backend (`_NotSentBooleanField`): «no enviado» es un caso propio, que al crear deja el proyecto con subtítulos (`true`) y al editar no cambia lo que había. En `POST /projects/` el cliente lo manda igual, con lo que muestra el formulario. En `from-prompt` lo deja afuera salvo que la persona lo haya elegido: un campo que viaja cuenta como explícito (`pte_explicit`) y le gana al texto, así que mandarlo siempre impediría que «sin subtítulos» en el prompt funcione.
- **Idioma:** el backend responde con `LANGUAGE_CODE = "en-us"`, así que los mensajes propios de DRF llegan en inglés. La UI no los muestra tal cual: `errorMessage()`, `fieldError()` y `translateFieldError()` (`src/lib/error-messages.ts`) traducen los conocidos y caen a un texto genérico en español.
- **Descargas:** los links firmados son de otro origen, y ahí el navegador ignora el atributo `download`. Para que el archivo se guarde en lugar de reproducirse, R2 tiene que firmar con `ResponseContentDisposition=attachment; filename="…"`. `downloadUrl()` abre esos links en una pestaña nueva para no reemplazar la app.
- **503:** cualquier respuesta 503 muestra la página de mantenimiento (K5) hasta que la API vuelve a responder.
- **Listas paginadas (propuesto, hueco 14):** `{ count, next, previous, results }` con `page` y `page_size` (25 por defecto), como `PageNumberPagination`.
- **Errores:** validación de DRF `{ "campo": ["mensaje"] }`; vistas propias `{ "error": "mensaje", "required"?: "117.00" }`; permisos y 404 `{ "detail": "mensaje" }`. Propuesto: sumar `code` legible por máquina (`duration`, `resolution`, `seo`, `scheduling`, `crossposting`, `seats_exceeded`, `email_not_verified`, `account_not_connected`, `permission_denied`) y, en los errores de campo, ítems `{ "message", "code" }`.
- **402** siempre abre el diálogo de saldo insuficiente y **403 de precios** el de límite del plan.
- **Roles (hueco 3):** la tabla 1.7 del mapa está en `src/lib/permissions.ts`. Hoy la API solo controla que el usuario sea miembro; el contrato espera un 403 con `code: "permission_denied"` cuando el rol no alcanza.

## Diferencias encontradas con el backend de hoy

| # | Qué pasa hoy | Qué espera el frontend |
|---|---|---|
| 1 | `GET /api/v1/projects/` devuelve un array sin paginar, sin filtros, y cada ítem trae `transcript_data`, `ai_rationale_log` y `metadata` completos. | Paginado, con `status_group`, `search` y `status_counts`. La lista no necesita la transcripción: conviene un serializer liviano. El cliente acepta el array actual y lo envuelve. |
| 2 | El código de `InvalidUploadError` (`not_a_video`, `too_long`…) no llega en el 400: DRF solo manda el mensaje. | `{ "source_file": [{ "message", "code" }] }`. Mientras tanto el frontend reconoce el código por el texto (`uploadErrorCode()` en `src/lib/error-messages.ts`). |
| 3 | Los 403 de `PricingEngine` traen un mensaje en inglés sin código (`Video duration (…) exceeds your plan limit`). | `code` (`duration`, `resolution`, `seo`) y `limit`. Mientras tanto se reconoce por el texto (`planLimitKind()`). |
| 4 | El rol del modelo es `viewer` (`WorkspaceMember.Role.VIEWER`); el mapa lo llama «Lector». | El contrato usa los valores del backend (`admin`, `editor`, `viewer`) más `owner` derivado de `Workspace.owner`. «Lector» es solo la etiqueta. |
| 5 | `POST/GET /api/v1/clips/{id}/preview/` (PTE-03) existe; el mapa (D4 y §7) todavía lo da por faltante. | Está en el contrato como existente. |
| 6 | `VideoProjectSerializer.create` lee `subtitle_scale_factor` del pedido y lo asigna a un atributo que el modelo no tiene: se pierde. | El frontend no lo manda; el tamaño va en `subtitle_size`. |
| 7 | `subtitle_size` se guarda en `metadata.subtitle_size` al crear, además del campo del modelo. | El frontend lee el campo `subtitle_size` del proyecto. |
| 8 | `brand_kit_name` no viene cuando el proyecto no tiene brand kit (DRF omite el campo). | Opcional en el esquema. |
| 9 | Un `SPEND` devuelto queda `FAILED`; no hay estado «devuelto» ni vínculo proyecto ↔ transacción. | `Transaction.kind` (`released`, `refund`…), `Transaction.project` y `Project.cost { amount, state, transaction_id }`. |
| 10 | `BrandKit.is_default` no es único por workspace. | Al marcar uno, el backend desmarca el resto. |
| 11 | Los links no pasan por la validación de duración del plan. | `POST /projects/probe-link/` (propuesto) reconoce el link y responde 403 si supera el plan. |
| 12 | `error_log` de una publicación guarda el texto de la excepción. | `error_message` legible y `error_code`; `error_log` no se muestra. |
| 13 | `approve-paper-edit` responde 400 `{ error }` para un proyecto que ya no está en revisión («…no está en espera de aprobación. Estado actual: rendering») y para un límite del plan («Error en el motor de precios: …»). El 409 solo sale en la carrera de dos aprobaciones. | Propuesto: 409 con `code: "not_awaiting_approval"` y 403 con `code` (`duration`, `resolution`). Mientras tanto `approveErrorKind()` los reconoce por el texto: el primero avisa y lleva a Clips, el segundo abre el diálogo de límite del plan. |
| 14 | Resuelto en el backend (PR #125): CORS admite `X-Workspace-Id` y las vistas leen el workspace activo de ese header, con los roles aplicados. | Nada pendiente. |
| 15 | `LANGUAGE_CODE = "en-us"`: los errores propios de DRF llegan en inglés («This field is required.», «You do not have permission to perform this action.»). | `LANGUAGE_CODE = "es"` (o `es-ar`). Mientras tanto la UI traduce los conocidos y no muestra texto en inglés. |
| 16 | El 409 de `PATCH /projects/{id}/` mientras se renderiza trae solo `{ detail }`: el código `project_rendering` de la excepción no llega al cuerpo. | `{ detail, code }`. El frontend solo manda `title`, que siempre se puede cambiar. |
| 17 | Un 403 de permisos de DRF (`PermissionDenied`) tampoco trae código. | `code: "permission_denied"`. El frontend trata como límite del plan solo los 403 con un código de plan o con un mensaje de `PricingEngine`; el resto es un 403 de rol. |
| 18 | Resuelto en el backend (PR #121): un `add_subtitles` que no se manda ya no se lee como `false`. Es «no enviado»: `true` al crear, sin cambios al editar. Cualquier otro booleano que falte en un formulario multipart se sigue leyendo como `false`. | Nada pendiente. El frontend lo manda en `POST /projects/` y lo deja afuera en `from-prompt` salvo que la persona lo haya elegido. |
| 19 | Los links de descarga firmados no fuerzan la descarga. | Firmar con `ResponseContentDisposition=attachment` (ver Convenciones). |
| 20 | «Renderizar sin revisar» que no se puede pagar deja `metadata.auto_render_skipped` (`insufficient_funds` o `plan_limit`) y el proyecto en revisión; al re-despachar sin saldo falla con `AutoRenderNotPaid`. | Está en el contrato (`ProjectMetadata`, `KNOWN_PIPELINE_ERROR_CODES`). Falta sumar el mensaje de `AutoRenderNotPaid` a la sección 4 del mapa. |

## Resumen

| Método | Ruta | Qué hace | Backend | Pantallas |
|---|---|---|---|---|
| POST | `/api/token/` | Inicia sesión con email y contraseña; devuelve el par JWT. | ✅ existe | A1, D9 |
| POST | `/api/token/refresh/` | Renueva el token de acceso. El backend rota el refresh (ROTATE_REFRESH_TOKENS). | ✅ existe | A1, D9 |
| POST | `/api/v1/auth/register/` | Crea la cuenta y manda el email de verificación. | ❌ falta (propuesto) · hueco 1 | A2, A5 |
| POST | `/api/v1/auth/verify-email/` | Verifica el email con el token del link y deja la sesión iniciada. | ❌ falta (propuesto) · hueco 1 | A3 |
| POST | `/api/v1/auth/verify-email/resend/` | Reenvía el email de verificación (una vez cada 60 s). Responde 204 exista o no el email y esté o no verificado, como recuperar contraseña. | ❌ falta (propuesto) · hueco 1 | A3 |
| POST | `/api/v1/auth/password-reset/` | Pide el link para recuperar la contraseña. Siempre responde 204, exista o no el email. | ❌ falta (propuesto) · hueco 1 | A4, D9 |
| POST | `/api/v1/auth/password-reset/confirm/` | Define la contraseña nueva con el token del link. Responde 204 sin tokens: la UI inicia sesión con el email del link y la contraseña nueva. | ❌ falta (propuesto) · hueco 1 | A4 |
| POST | `/api/v1/auth/logout/` | Cierra la sesión: invalida el refresh. Con `all_devices`, todos los del usuario. | ❌ falta (propuesto) · hueco 2 | I1, Barra |
| GET | `/api/v1/me/` | Perfil del usuario con sus workspaces, el rol en cada uno y el workspace activo. | ❌ falta (propuesto) · hueco 2 | Barra, B1, B2, J3, I1 |
| PATCH | `/api/v1/me/` | Actualiza nombre, email, tour, uso elegido en la bienvenida y workspace activo. | ❌ falta (propuesto) · hueco 2 | B1, B2, J3, I1 |
| POST | `/api/v1/me/password/` | Cambia la contraseña del usuario. | ❌ falta (propuesto) · hueco 2 | I1 |
| GET | `/api/v1/me/notification-preferences/` | Qué emails recibe el usuario, por tipo. | ❌ falta (propuesto) · hueco 16 | I2 |
| PATCH | `/api/v1/me/notification-preferences/` | Prende o apaga los emails por tipo. | ❌ falta (propuesto) · hueco 16 | I2 |
| GET | `/api/v1/workspaces/` | Workspaces del usuario. | ❌ falta (propuesto) · hueco 2 | J3 |
| POST | `/api/v1/workspaces/` | Crea un workspace con el usuario como dueño (plan Gratis y billetera vacía). | ❌ falta (propuesto) · hueco 2 | J3, B1 |
| GET | `/api/v1/workspaces/:id/` | Datos del workspace. | ❌ falta (propuesto) · hueco 2 | H1 |
| PATCH | `/api/v1/workspaces/:id/` | Renombra el workspace (Dueño y Admin). | ❌ falta (propuesto) · hueco 2, 3 | H1, B1 |
| DELETE | `/api/v1/workspaces/:id/` | Borra el workspace. Solo el Dueño, escribiendo el nombre para confirmar. | ❌ falta (propuesto) · hueco 2, 3 | H1 |
| POST | `/api/v1/workspaces/:id/transfer/` | Transfiere el workspace a otro miembro activo. El dueño anterior queda como Admin. | ❌ falta (propuesto) · hueco 2, 3 | H1 |
| GET | `/api/v1/workspaces/:id/members/` | Miembros e invitaciones pendientes, con los lugares usados del plan. | ❌ falta (propuesto) · hueco 13 | H2, D7, H1 |
| PATCH | `/api/v1/workspaces/:id/members/:memberId/` | Cambia el rol de un miembro. | ❌ falta (propuesto) · hueco 3, 13 | H2 |
| DELETE | `/api/v1/workspaces/:id/members/:memberId/` | Quita a un miembro o cancela una invitación pendiente. | ❌ falta (propuesto) · hueco 3, 13 | H2 |
| POST | `/api/v1/workspaces/:id/invitations/` | Invita por email con un rol. Crea WorkspaceMember en `pending` y manda el email. | ❌ falta (propuesto) · hueco 13 | D7, H2 |
| POST | `/api/v1/workspaces/:id/invitations/:memberId/resend/` | Reenvía el email de una invitación pendiente. | ❌ falta (propuesto) · hueco 13 | H2 |
| GET | `/api/v1/invitations/:token/` | Datos públicos de una invitación: quién invita, a qué workspace y con qué rol. | ❌ falta (propuesto) · hueco 13 | A5 |
| POST | `/api/v1/invitations/:token/accept/` | Acepta la invitación. Sin cuenta: la crea con `name` y `password`. Con cuenta: basta con la sesión iniciada. | ❌ falta (propuesto) · hueco 13 | A5 |
| GET | `/api/v1/plans/` | Planes activos, en orden, para Precios y Uso › Planes. | ❌ falta (propuesto) · hueco 6, 12 | A6, G3, D3 (diálogo), C1 |
| GET | `/api/v1/pricing/` | Valores de la fórmula de precios y los topes de subida, para estimar en el navegador. | ❌ falta (propuesto) · hueco 6 | C1, A6, G3, G4, D2, D1 (diálogo) |
| POST | `/api/v1/pricing/estimate/` | Estimado del backend para una configuración: costo, descuento, saldo después y tiempo estimado. | ❌ falta (propuesto) · hueco 6, 18 | C1, D2, D4, D5 |
| GET | `/api/v1/subscription/` | Plan actual del workspace, su renovación y el cambio pendiente si lo hay. | ❌ falta (propuesto) · hueco 12 | Barra, G1, G3, C1, E1, H2 |
| POST | `/api/v1/subscription/change/` | Cambia de plan. Subir se aplica al momento, o al volver del checkout si hay que pagar; bajar, al fin del período. Mandar el plan actual cancela un cambio programado. | ❌ falta (propuesto) · hueco 12 | G3 |
| GET | `/api/v1/wallet/` | Saldo disponible y reservado del workspace, con el detalle de lo reservado por proyecto. | ❌ falta (propuesto) · hueco 4, 19 | Barra, G1, C1, D2, G4, D1 (diálogo), D2 (diálogo) |
| GET | `/api/v1/wallet/usage/` | Consumo por día de un mes (solo lo cobrado), separado en proyectos, renders y SEO. El mes y los días son los de la zona horaria del workspace (America/Argentina/Buenos_Aires). | ❌ falta (propuesto) · hueco 4 | G1 |
| GET | `/api/v1/transactions/` | Movimientos de la billetera, paginados, con filtro por tipo, mes y proyecto. `month=YYYY-MM` es el mes en la zona horaria del workspace (America/Argentina/Buenos_Aires). | ❌ falta (propuesto) · hueco 4 | G2, D1f |
| GET | `/api/v1/transactions/export/` | Los mismos movimientos en CSV. El monto lleva el signo de lo que le hizo al saldo: negativo lo reservado y lo cobrado, positivo lo comprado y lo devuelto. | ❌ falta (propuesto) · hueco 4 | G2 |
| GET | `/api/v1/token-packages/` | Los 3 paquetes fijos, el precio de la cantidad libre y la conversión de referencia. | ❌ falta (propuesto) · hueco 12 | G4, D2 (diálogo) |
| POST | `/api/v1/token-purchases/` | Inicia una compra de tokens y devuelve la URL del checkout del proveedor de pagos. | ❌ falta (propuesto) · hueco 12 | G4, D2 (diálogo) |
| GET | `/api/v1/token-purchases/:id/` | Estado de un pago: pendiente, acreditado o rechazado. Es una compra de tokens (`tokens` > 0) o el pago de una subida de plan (`plan_id`, `tokens` 0). | ❌ falta (propuesto) · hueco 12 | G5 |
| POST | `/api/v1/projects/probe-link/` | Reconoce un link antes de crear el proyecto: de dónde es, su título y su duración. | ❌ falta (propuesto) | C1 |
| GET | `/api/v1/projects/` | Proyectos del workspace, del más nuevo al más viejo. | ⚠️ existe en parte · hueco 14 | B2, J1, B1 |
| POST | `/api/v1/projects/` | Crea un proyecto desde un archivo (multipart) o un link, reserva los tokens y arranca la ingesta. | ✅ existe | C1 |
| POST | `/api/v1/projects/from-prompt/` | Igual que crear, más `prompt`: la IA arma la configuración al iniciar la ingesta (queda en `metadata.pte`). | ✅ existe | C1 |
| GET | `/api/v1/projects/:id/` | Detalle del proyecto con el estado del pipeline, la metadata, la transcripción y sus clips. | ⚠️ existe en parte · hueco 4, 15, 18 | D1, D1e, D1f, D3, D4, D5 |
| PATCH | `/api/v1/projects/:id/` | Renombra el proyecto. | ✅ existe | B2, D1 |
| DELETE | `/api/v1/projects/:id/` | Borra el proyecto con su video, sus clips y sus publicaciones programadas. No devuelve lo cobrado. | ✅ existe | B2, D1, D4 (diálogo) |
| GET | `/api/v1/projects/:id/paper-edit-data/` | Datos de la revisión: video proxy, transcripción, clips sugeridos y metadata. | ✅ existe | D2 |
| POST | `/api/v1/projects/:id/approve-paper-edit/` | Aprueba los segmentos elegidos, reserva el costo del render y lo despacha. | ✅ existe | D2 |
| POST | `/api/v1/projects/:id/redispatch/` | Vuelve a despachar el pipeline de un proyecto detenido, desde la última etapa terminada. No cobra. | ❌ falta (propuesto) | D1 |
| POST | `/api/v1/projects/:id/rerender/` | Renderiza todos los clips de nuevo con otro estilo (layout, subtítulos, brand kit). Cobra el render. | ❌ falta (propuesto) · hueco 11 | D1, D4, D5 |
| GET | `/api/v1/projects/:id/activity/` | Quién hizo qué en el proyecto y sus movimientos de tokens, del más nuevo al más viejo. | ❌ falta (propuesto) · hueco 4 | D1f |
| POST | `/api/v1/projects/:id/clips/download/` | Arma un .zip con los clips listos (todos, o los de `clip_ids`) y devuelve la URL temporal. | ❌ falta (propuesto) · hueco 17 | D3 |
| GET | `/api/v1/clips/:id/` | Un clip con sus datos, sus capas y dónde está publicado. | ❌ falta (propuesto) · hueco 11 | D4, E1 |
| PATCH | `/api/v1/clips/:id/` | Cambia el título o el recorte. No renderiza: para eso está `render`. | ❌ falta (propuesto) · hueco 11 | D4, D3, D5 |
| DELETE | `/api/v1/clips/:id/` | Borra el clip y cancela sus publicaciones programadas. | ❌ falta (propuesto) · hueco 11 | D3 |
| POST | `/api/v1/clips/:id/render/` | Vuelve a renderizar el clip con su recorte actual y la configuración del proyecto. Cobra por la duración del clip. | ❌ falta (propuesto) · hueco 11 | D4, D5 |
| POST | `/api/v1/clips/:id/retry/` | Reintenta el render de un clip que falló. No cobra si el error fue del sistema. | ❌ falta (propuesto) · hueco 11 | D3 |
| GET | `/api/v1/clips/:id/download/` | Link de descarga firmado y temporal del clip renderizado. | ❌ falta (propuesto) · hueco 5 | D3, D4, D5 |
| POST | `/api/v1/clips/:clipId/preview/` | Pide una vista previa gratis en 360p, o devuelve la que ya está en curso. Nunca reserva tokens. | ✅ existe | D2, D4 |
| GET | `/api/v1/clips/:clipId/preview/` | La vista previa vigente más nueva del clip. Se consulta cada 5 s hasta `ready` o `failed`. | ✅ existe | D2, D4 |
| GET | `/api/v1/brand-kits/` | Brand kits del workspace. | ✅ existe | F1, C1, B2, D5 |
| POST | `/api/v1/brand-kits/` | Crea un brand kit (multipart por las fuentes, el logo y los videos). | ✅ existe | F1, F2 |
| GET | `/api/v1/brand-kits/:id/` | Un brand kit. | ✅ existe | F2 |
| PATCH | `/api/v1/brand-kits/:id/` | Edita un brand kit. Con `is_default: true` pasa a ser el default del workspace. | ⚠️ existe en parte | F1, F2 |
| DELETE | `/api/v1/brand-kits/:id/` | Borra un brand kit. Los proyectos que lo usaban quedan sin kit (SET_NULL). | ✅ existe | F1, F2 |
| POST | `/api/v1/brand-kits/:id/duplicate/` | Copia un brand kit con sus archivos ("Copia de …"). La copia nunca es default. | ❌ falta (propuesto) | F1 |
| GET | `/api/v1/social-accounts/` | Una entrada por red (TikTok, Instagram Reels, YouTube Shorts) con su estado y sus límites. | ❌ falta (propuesto) · hueco 10 | H3, E1, B2 |
| POST | `/api/v1/social-accounts/connect/` | Devuelve la URL de la red (vía Ayrshare) para autorizar la cuenta en una ventana aparte. | ❌ falta (propuesto) · hueco 10 | D6, H3, E1 |
| DELETE | `/api/v1/social-accounts/:platform/` | Desconecta la cuenta de una red. Las publicaciones programadas en esa red quedan en error. | ❌ falta (propuesto) · hueco 10 | H3 |
| GET | `/api/v1/scheduled-posts/` | Publicaciones del workspace, ordenadas por `publish_at`, con filtros por estado, red, rango y clip. | ❌ falta (propuesto) · hueco 8 | E2, D3, D4 (diálogo) |
| POST | `/api/v1/scheduled-posts/` | Publica ahora o programa un clip en una red. Con `draft: true` crea un borrador, que dispara la generación del texto con IA. | ❌ falta (propuesto) · hueco 8, 9 | E1 |
| POST | `/api/v1/scheduled-posts/bulk/` | Crea varias publicaciones juntas (varios clips, varias redes). Todo o nada. | ❌ falta (propuesto) · hueco 8 | E1c |
| GET | `/api/v1/scheduled-posts/:id/` | Detalle de una publicación. Se consulta cada 5 s mientras está en cola o publicando. | ❌ falta (propuesto) · hueco 8 | E2, E1 |
| PATCH | `/api/v1/scheduled-posts/:id/` | Edita el texto, reprograma, o publica un borrador (`publish_at`: fecha para programar, `null` para publicar ahora). Solo antes de `QUEUED`. | ❌ falta (propuesto) · hueco 8 | E2 |
| POST | `/api/v1/scheduled-posts/:id/cancel/` | Cancela una publicación que todavía no salió. La borra. | ❌ falta (propuesto) · hueco 8 | E2 |
| POST | `/api/v1/scheduled-posts/:id/retry/` | Reintenta una publicación que falló (también una trabada en `PROCESSING`). | ❌ falta (propuesto) · hueco 8 | E2 |
| POST | `/api/v1/scheduled-posts/:id/regenerate-seo/` | Vuelve a generar el caption y los hashtags con IA. Responde con `seo_status: "pending"`; el texto anterior se conserva hasta que llega el nuevo. | ❌ falta (propuesto) · hueco 8 | E1 |
| GET | `/api/v1/notifications/` | Las últimas 20 notificaciones del usuario en el workspace y cuántas están sin leer. | ❌ falta (propuesto) · hueco 16 | J2, Barra |
| POST | `/api/v1/notifications/:id/read/` | Marca una notificación como leída. | ❌ falta (propuesto) · hueco 16 | J2 |
| POST | `/api/v1/notifications/read-all/` | Marca todas como leídas. | ❌ falta (propuesto) · hueco 16 | J2 |
| GET | `/api/v1/status/` | Si la plataforma está en mantenimiento y hasta cuándo. | ❌ falta (propuesto) | K5 |

## Detalle por dominio

### Acceso

Contrato: `src/api/contracts/auth.ts` · Hooks: `src/api/hooks/auth.ts` · Mocks: `src/api/mocks/handlers/auth.ts`

#### `POST /api/token/`

Inicia sesión con email y contraseña; devuelve el par JWT.

| | |
|---|---|
| Id | `auth.login` |
| Backend | ✅ existe · `back/backend/urls.py · rest_framework_simplejwt TokenObtainPairView (USERNAME_FIELD = email)` |
| Nota | Hoy no distingue la cuenta sin verificar (falta el 403 propuesto, hueco 1). |
| Sesión | Pública |
| Pedido | LoginRequest |
| Respuesta 200 | TokenPair |
| Errores | **401** Credenciales incorrectas. La UI muestra "El email o la contraseña no coinciden", sin decir cuál.<br>**403** Propuesto: `code: "email_not_verified"` cuando la cuenta no verificó el email. La UI lleva a A3.<br>**429** Demasiados intentos seguidos (throttle de DRF). La UI pide esperar y no borra lo escrito. |
| Pantallas | A1, D9 |

#### `POST /api/token/refresh/`

Renueva el token de acceso. El backend rota el refresh (ROTATE_REFRESH_TOKENS).

| | |
|---|---|
| Id | `auth.refresh` |
| Backend | ✅ existe · `back/backend/urls.py · TokenRefreshView; SIMPLE_JWT en backend/settings/base.py (acceso 25 min)` |
| Sesión | Pública |
| Pedido | { refresh: string } |
| Respuesta 200 | { access: string, refresh?: string } |
| Errores | **401** Refresh vencido o inválido. La UI abre el diálogo D9 (sesión vencida). |
| Pantallas | A1, D9 |

#### `POST /api/v1/auth/register/`

Crea la cuenta y manda el email de verificación.

| | |
|---|---|
| Id | `auth.register` |
| Backend | ❌ falta (propuesto) · hueco 1 |
| Nota | Decir "email ya usado" permite averiguar qué emails tienen cuenta. El mapa (A2) lo pide así; para no ayudar a enumerar, el endpoint necesita un límite de intentos por IP. Recuperar contraseña y reenviar la verificación no lo revelan. |
| Sesión | Pública |
| Pedido | RegisterRequest |
| Respuesta 201 | RegisterResponse |
| Errores | **400** Validación por campo (contraseña débil; email ya usado con `code: "unique"`, que la UI muestra con un link a Iniciar sesión). Se muestra debajo de cada campo. |
| Pantallas | A2, A5 |

#### `POST /api/v1/auth/verify-email/`

Verifica el email con el token del link y deja la sesión iniciada.

| | |
|---|---|
| Id | `auth.verifyEmail` |
| Backend | ❌ falta (propuesto) · hueco 1 |
| Sesión | Pública |
| Pedido | { token: string } |
| Respuesta 200 | TokenPair |
| Errores | **400** Token inválido o vencido. La UI ofrece reenviar el email. |
| Pantallas | A3 |

#### `POST /api/v1/auth/verify-email/resend/`

Reenvía el email de verificación (una vez cada 60 s). Responde 204 exista o no el email y esté o no verificado, como recuperar contraseña.

| | |
|---|---|
| Id | `auth.resendVerification` |
| Backend | ❌ falta (propuesto) · hueco 1 |
| Sesión | Pública |
| Pedido | { email: string } |
| Respuesta 204 | NoContent |
| Errores | **429** Se pidió hace menos de 60 s. La UI deja el botón deshabilitado con la cuenta regresiva. |
| Pantallas | A3 |

#### `POST /api/v1/auth/password-reset/`

Pide el link para recuperar la contraseña. Siempre responde 204, exista o no el email.

| | |
|---|---|
| Id | `auth.requestPasswordReset` |
| Backend | ❌ falta (propuesto) · hueco 1 |
| Nota | El link del email (N2) tiene que ser `/recuperar?token=<token>&email=<email>`: con el email la pantalla dice de quién es la cuenta e inicia sesión después de guardar ("Guardar y entrar"). |
| Sesión | Pública |
| Pedido | { email: string } |
| Respuesta 204 | NoContent |
| Pantallas | A4, D9 |

#### `POST /api/v1/auth/password-reset/confirm/`

Define la contraseña nueva con el token del link. Responde 204 sin tokens: la UI inicia sesión con el email del link y la contraseña nueva.

| | |
|---|---|
| Id | `auth.confirmPasswordReset` |
| Backend | ❌ falta (propuesto) · hueco 1 |
| Sesión | Pública |
| Pedido | { token: string, password: string } |
| Respuesta 204 | NoContent |
| Errores | **400** Token inválido o vencido, o contraseña débil (error en `password`). |
| Pantallas | A4 |

#### `POST /api/v1/auth/logout/`

Cierra la sesión: invalida el refresh. Con `all_devices`, todos los del usuario.

| | |
|---|---|
| Id | `auth.logout` |
| Backend | ❌ falta (propuesto) · hueco 2 |
| Nota | Necesita la app token_blacklist de SimpleJWT. |
| Sesión | JWT |
| Pedido | { refresh: string, all_devices?: boolean } |
| Respuesta 204 | NoContent |
| Pantallas | I1, Barra |

### Usuario y perfil

Contrato: `src/api/contracts/users.ts` · Hooks: `src/api/hooks/users.ts` · Mocks: `src/api/mocks/handlers/users.ts`

#### `GET /api/v1/me/`

Perfil del usuario con sus workspaces, el rol en cada uno y el workspace activo.

| | |
|---|---|
| Id | `users.me` |
| Backend | ❌ falta (propuesto) · hueco 2 |
| Nota | Los campos salen de User, Workspace, WorkspaceMember y Wallet (apps/users/models.py). |
| Sesión | JWT |
| Respuesta 200 | Me |
| Errores | **401** Sin sesión: el cliente renueva el token o abre D9. |
| Pantallas | Barra, B1, B2, J3, I1 |

#### `PATCH /api/v1/me/`

Actualiza nombre, email, tour, uso elegido en la bienvenida y workspace activo.

| | |
|---|---|
| Id | `users.updateMe` |
| Backend | ❌ falta (propuesto) · hueco 2 |
| Sesión | JWT |
| Pedido | UpdateMeRequest |
| Respuesta 200 | Me |
| Errores | **400** Validación por campo: email ya usado, o `email` nuevo sin `current_password` o con una incorrecta (error en `current_password`). |
| Pantallas | B1, B2, J3, I1 |

#### `POST /api/v1/me/password/`

Cambia la contraseña del usuario.

| | |
|---|---|
| Id | `users.changePassword` |
| Backend | ❌ falta (propuesto) · hueco 2 |
| Sesión | JWT |
| Pedido | { current_password: string, new_password: string } |
| Respuesta 204 | NoContent |
| Errores | **400** Contraseña actual incorrecta (`current_password`) o nueva débil (`new_password`). |
| Pantallas | I1 |

#### `GET /api/v1/me/notification-preferences/`

Qué emails recibe el usuario, por tipo.

| | |
|---|---|
| Id | `users.notificationPreferences` |
| Backend | ❌ falta (propuesto) · hueco 16 |
| Sesión | JWT |
| Respuesta 200 | NotificationPreferences |
| Pantallas | I2 |

#### `PATCH /api/v1/me/notification-preferences/`

Prende o apaga los emails por tipo.

| | |
|---|---|
| Id | `users.updateNotificationPreferences` |
| Backend | ❌ falta (propuesto) · hueco 16 |
| Sesión | JWT |
| Pedido | { project_ready?: boolean, render_finished?: boolean, project_failed?: boolean, post_failed?: boolean, low_balance?: boolean } |
| Respuesta 200 | NotificationPreferences |
| Pantallas | I2 |

### Workspaces, miembros e invitaciones

Contrato: `src/api/contracts/workspaces.ts` · Hooks: `src/api/hooks/workspaces.ts` · Mocks: `src/api/mocks/handlers/workspaces.ts`

#### `GET /api/v1/workspaces/`

Workspaces del usuario.

| | |
|---|---|
| Id | `workspaces.list` |
| Backend | ❌ falta (propuesto) · hueco 2 |
| Nota | El selector (J3) usa la lista de `/me`; este listado es el recurso REST completo. |
| Sesión | JWT |
| Respuesta 200 | Workspace[] |
| Pantallas | J3 |

#### `POST /api/v1/workspaces/`

Crea un workspace con el usuario como dueño (plan Gratis y billetera vacía).

| | |
|---|---|
| Id | `workspaces.create` |
| Backend | ❌ falta (propuesto) · hueco 2 |
| Sesión | JWT |
| Pedido | { name: string } |
| Respuesta 201 | Workspace |
| Errores | **400** Nombre vacío o demasiado largo. |
| Pantallas | J3, B1 |

#### `GET /api/v1/workspaces/:id/`

Datos del workspace.

| | |
|---|---|
| Id | `workspaces.detail` |
| Backend | ❌ falta (propuesto) · hueco 2 |
| Sesión | JWT |
| Respuesta 200 | Workspace |
| Errores | **404** No existe o el usuario no es miembro. |
| Pantallas | H1 |

#### `PATCH /api/v1/workspaces/:id/`

Renombra el workspace (Dueño y Admin).

| | |
|---|---|
| Id | `workspaces.update` |
| Backend | ❌ falta (propuesto) · hueco 2, 3 |
| Sesión | JWT |
| Pedido | { name: string } |
| Respuesta 200 | Workspace |
| Errores | **400** Nombre vacío o demasiado largo.<br>**403** El rol no puede renombrar (Editor, Lector). |
| Pantallas | H1, B1 |

#### `DELETE /api/v1/workspaces/:id/`

Borra el workspace. Solo el Dueño, escribiendo el nombre para confirmar.

| | |
|---|---|
| Id | `workspaces.delete` |
| Backend | ❌ falta (propuesto) · hueco 2, 3 |
| Sesión | JWT |
| Pedido | { confirm_name: string } |
| Respuesta 204 | NoContent |
| Errores | **400** `confirm_name` no coincide con el nombre.<br>**403** No es el Dueño. |
| Pantallas | H1 |

#### `POST /api/v1/workspaces/:id/transfer/`

Transfiere el workspace a otro miembro activo. El dueño anterior queda como Admin.

| | |
|---|---|
| Id | `workspaces.transfer` |
| Backend | ❌ falta (propuesto) · hueco 2, 3 |
| Sesión | JWT |
| Pedido | { new_owner_id: Id } |
| Respuesta 200 | Workspace |
| Errores | **400** El usuario no es miembro activo.<br>**403** No es el Dueño. |
| Pantallas | H1 |

#### `GET /api/v1/workspaces/:id/members/`

Miembros e invitaciones pendientes, con los lugares usados del plan.

| | |
|---|---|
| Id | `workspaces.members` |
| Backend | ❌ falta (propuesto) · hueco 13 |
| Nota | Modelo: WorkspaceMember (apps/users/models.py). |
| Sesión | JWT |
| Respuesta 200 | MembersResponse |
| Errores | **403** Solo Dueño y Admin ven la sección (para Editor y Lector no aparece). |
| Pantallas | H2, D7, H1 |

#### `PATCH /api/v1/workspaces/:id/members/:memberId/`

Cambia el rol de un miembro.

| | |
|---|---|
| Id | `workspaces.updateMember` |
| Backend | ❌ falta (propuesto) · hueco 3, 13 |
| Sesión | JWT |
| Pedido | { role: MemberRole } |
| Respuesta 200 | Member |
| Errores | **400** No se puede cambiar el rol del Dueño.<br>**403** El rol no administra miembros. |
| Pantallas | H2 |

#### `DELETE /api/v1/workspaces/:id/members/:memberId/`

Quita a un miembro o cancela una invitación pendiente.

| | |
|---|---|
| Id | `workspaces.removeMember` |
| Backend | ❌ falta (propuesto) · hueco 3, 13 |
| Sesión | JWT |
| Respuesta 204 | NoContent |
| Errores | **400** No se puede quitar al Dueño.<br>**403** El rol no administra miembros. |
| Pantallas | H2 |

#### `POST /api/v1/workspaces/:id/invitations/`

Invita por email con un rol. Crea WorkspaceMember en `pending` y manda el email.

| | |
|---|---|
| Id | `workspaces.invite` |
| Backend | ❌ falta (propuesto) · hueco 13 |
| Sesión | JWT |
| Pedido | InviteRequest |
| Respuesta 201 | { invited: Member[] } |
| Errores | **400** Email inválido o ya es miembro (error en `emails`).<br>**403** Sin lugares en el plan (`code: "seats_exceeded"`: límite del plan, se resuelve subiendo de plan o quitando miembros; el mismo código en `billing.changePlan` es un 409 porque ahí lo que sobra son miembros) o el rol no puede invitar. |
| Pantallas | D7, H2 |

#### `POST /api/v1/workspaces/:id/invitations/:memberId/resend/`

Reenvía el email de una invitación pendiente.

| | |
|---|---|
| Id | `workspaces.resendInvitation` |
| Backend | ❌ falta (propuesto) · hueco 13 |
| Sesión | JWT |
| Respuesta 204 | NoContent |
| Errores | **404** La invitación ya se aceptó o se canceló. |
| Pantallas | H2 |

#### `GET /api/v1/invitations/:token/`

Datos públicos de una invitación: quién invita, a qué workspace y con qué rol.

| | |
|---|---|
| Id | `workspaces.invitation` |
| Backend | ❌ falta (propuesto) · hueco 13 |
| Nota | Vencida o usada responden 200 con `status`, para poder explicar el motivo. |
| Sesión | Pública |
| Respuesta 200 | InvitationPreview |
| Errores | **404** El token no existe. La UI muestra "Esta invitación no existe". |
| Pantallas | A5 |

#### `POST /api/v1/invitations/:token/accept/`

Acepta la invitación. Sin cuenta: la crea con `name` y `password`. Con cuenta: basta con la sesión iniciada.

| | |
|---|---|
| Id | `workspaces.acceptInvitation` |
| Backend | ❌ falta (propuesto) · hueco 13 |
| Sesión | Pública (manda el token si hay sesión) |
| Pedido | { name?: string, password?: string, terms_accepted?: boolean } |
| Respuesta 200 | { workspace_id: Id, tokens: TokenPair \\| null } |
| Errores | **400** Faltan `name` o `password` para crear la cuenta.<br>**401** El email ya tiene cuenta y no hay sesión: la UI pide iniciar sesión. Con sesión, el cliente manda el token (y lo renueva si hace falta).<br>**410** Invitación vencida o ya usada. |
| Pantallas | A5 |

### Planes, billetera, movimientos, compras y precios

Contrato: `src/api/contracts/billing.ts` · Hooks: `src/api/hooks/billing.ts` · Mocks: `src/api/mocks/handlers/billing.ts`

#### `GET /api/v1/plans/`

Planes activos, en orden, para Precios y Uso › Planes.

| | |
|---|---|
| Id | `billing.plans` |
| Backend | ❌ falta (propuesto) · hueco 6, 12 |
| Nota | Modelo: SubscriptionPlan (apps/payments/models.py); faltan precio, descripción, orden y recomendado. |
| Sesión | Pública |
| Respuesta 200 | Plan[] |
| Pantallas | A6, G3, D3 (diálogo), C1 |

#### `GET /api/v1/pricing/`

Valores de la fórmula de precios y los topes de subida, para estimar en el navegador.

| | |
|---|---|
| Id | `billing.pricing` |
| Backend | ❌ falta (propuesto) · hueco 6 |
| Nota | Valores de PricingEngine (apps/payments/services/pricing_engine.py) y de settings. |
| Sesión | Pública |
| Respuesta 200 | Pricing |
| Pantallas | C1, A6, G3, G4, D2, D1 (diálogo) |

#### `POST /api/v1/pricing/estimate/`

Estimado del backend para una configuración: costo, descuento, saldo después y tiempo estimado.

| | |
|---|---|
| Id | `billing.estimate` |
| Backend | ❌ falta (propuesto) · hueco 6, 18 |
| Nota | Envuelve PricingEngine.calculate_render_cost con el plan del workspace. |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | EstimateRequest |
| Respuesta 200 | Estimate |
| Errores | **403** El plan no permite la duración, la resolución o el SEO (`code`: `duration` \| `resolution` \| `seo`). La UI abre D3. |
| Pantallas | C1, D2, D4, D5 |

#### `GET /api/v1/subscription/`

Plan actual del workspace, su renovación y el cambio pendiente si lo hay.

| | |
|---|---|
| Id | `billing.subscription` |
| Backend | ❌ falta (propuesto) · hueco 12 |
| Nota | Hoy solo existe Workspace.subscription_plan. |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | Subscription |
| Pantallas | Barra, G1, G3, C1, E1, H2 |

#### `POST /api/v1/subscription/change/`

Cambia de plan. Subir se aplica al momento, o al volver del checkout si hay que pagar; bajar, al fin del período. Mandar el plan actual cancela un cambio programado.

| | |
|---|---|
| Id | `billing.changePlan` |
| Backend | ❌ falta (propuesto) · hueco 12 |
| Nota | Con `plan_id` igual al plan actual y un cambio programado, lo cancela (`pending_plan` vuelve a null, `applied: "now"`). Si subir necesita un pago, `checkout_url` viene con valor, el plan no cambia todavía y el proveedor vuelve a `return_url` con `compra=<id>`. |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | { plan_id: Id, return_url?: string } |
| Respuesta 200 | { applied: "now" \\| "period_end", checkout_url: string \\| null, subscription: Subscription } |
| Errores | **400** Ya es el plan actual y no hay ningún cambio programado para cancelar.<br>**403** El rol no puede cambiar el plan (solo Dueño y Admin).<br>**409** Bajar a un plan con menos lugares que los que el workspace ocupa hoy, contando invitaciones pendientes (`code: "seats_exceeded"`). No se programa nada: primero hay que quitar miembros. Es un 409 porque lo impide el estado del workspace; el mismo código en `workspaces.invite` es un 403 de límite de plan, que se resuelve subiendo de plan. |
| Pantallas | G3 |

#### `GET /api/v1/wallet/`

Saldo disponible y reservado del workspace, con el detalle de lo reservado por proyecto.

| | |
|---|---|
| Id | `billing.wallet` |
| Backend | ❌ falta (propuesto) · hueco 4, 19 |
| Nota | Modelo: Wallet (available_balance, reserved_balance). |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | Wallet |
| Pantallas | Barra, G1, C1, D2, G4, D1 (diálogo), D2 (diálogo) |

#### `GET /api/v1/wallet/usage/`

Consumo por día de un mes (solo lo cobrado), separado en proyectos, renders y SEO. El mes y los días son los de la zona horaria del workspace (America/Argentina/Buenos_Aires).

| | |
|---|---|
| Id | `billing.usage` |
| Backend | ❌ falta (propuesto) · hueco 4 |
| Sesión | JWT · `X-Workspace-Id` |
| Query | { month?: string } |
| Respuesta 200 | UsageDay[] |
| Errores | **403** Solo Dueño y Admin. |
| Pantallas | G1 |

#### `GET /api/v1/transactions/`

Movimientos de la billetera, paginados, con filtro por tipo, mes y proyecto. `month=YYYY-MM` es el mes en la zona horaria del workspace (America/Argentina/Buenos_Aires).

| | |
|---|---|
| Id | `billing.transactions` |
| Backend | ❌ falta (propuesto) · hueco 4 |
| Nota | Modelo: Transaction. Faltan el vínculo al proyecto y `kind`. Hoy el backend solo escribe DEPOSIT y SPEND: un cobro parcial (`commit_partial_reservation`) baja el monto del SPEND a lo cobrado y lo deja COMPLETED, y devuelve el resto al saldo sin escribir otra fila; una reserva que se cae queda SPEND + FAILED (`kind: "released"`, «Devuelto»). El tipo REFUND existe en el modelo pero nada lo escribe: `kind: "refund"` queda propuesto para devoluciones de algo ya cobrado. |
| Sesión | JWT · `X-Workspace-Id` |
| Query | TransactionsQuery |
| Respuesta 200 | { count: number, next: string \\| null, previous: string \\| null, results: Transaction[], totals: { [clave]: TransactionKindTotal } } |
| Errores | **403** Solo Dueño y Admin (Editor y Lector ven únicamente el saldo). |
| Pantallas | G2, D1f |

#### `GET /api/v1/transactions/export/`

Los mismos movimientos en CSV. El monto lleva el signo de lo que le hizo al saldo: negativo lo reservado y lo cobrado, positivo lo comprado y lo devuelto.

| | |
|---|---|
| Id | `billing.exportTransactions` |
| Backend | ❌ falta (propuesto) · hueco 4 |
| Sesión | JWT · `X-Workspace-Id` |
| Query | { kind?: TransactionKind, month?: string, project?: Id } |
| Respuesta 200 | texto (CSV) |
| Errores | **403** Solo Dueño y Admin. |
| Pantallas | G2 |

#### `GET /api/v1/token-packages/`

Los 3 paquetes fijos, el precio de la cantidad libre y la conversión de referencia.

| | |
|---|---|
| Id | `billing.tokenPackages` |
| Backend | ❌ falta (propuesto) · hueco 12 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | TokenPackages |
| Pantallas | G4, D2 (diálogo) |

#### `POST /api/v1/token-purchases/`

Inicia una compra de tokens y devuelve la URL del checkout del proveedor de pagos.

| | |
|---|---|
| Id | `billing.createPurchase` |
| Backend | ❌ falta (propuesto) · hueco 12 |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | { package_id?: string, custom_tokens?: number, return_url: string } |
| Respuesta 201 | TokenPurchase |
| Errores | **400** Cantidad por debajo del mínimo o paquete inexistente.<br>**403** El rol no puede comprar (solo Dueño y Admin). |
| Pantallas | G4, D2 (diálogo) |

#### `GET /api/v1/token-purchases/:id/`

Estado de un pago: pendiente, acreditado o rechazado. Es una compra de tokens (`tokens` > 0) o el pago de una subida de plan (`plan_id`, `tokens` 0).

| | |
|---|---|
| Id | `billing.purchase` |
| Backend | ❌ falta (propuesto) · hueco 12 |
| Nota | El webhook del proveedor acredita; la pantalla consulta cada 5 s mientras está pendiente. |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | Payment |
| Errores | **404** La compra no existe en este workspace. |
| Pantallas | G5 |

### Proyectos

Contrato: `src/api/contracts/projects.ts` · Hooks: `src/api/hooks/projects.ts` · Mocks: `src/api/mocks/handlers/projects.ts`

#### `POST /api/v1/projects/probe-link/`

Reconoce un link antes de crear el proyecto: de dónde es, su título y su duración.

| | |
|---|---|
| Id | `projects.probeLink` |
| Backend | ❌ falta (propuesto) |
| Nota | Sin número de hueco: lo pide C1 (artboard «Pegar link») y cierra el aviso del mapa de que los links no pasan por la validación de duración del plan. |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | { video_url: string } |
| Respuesta 200 | LinkProbe |
| Errores | **400** El link no es de un sitio soportado (`code: "unsupported"`) o no se puede bajar: privado, borrado o con restricción (`code: "unavailable"`).<br>**403** La duración supera el plan (`code: "duration"`, `limit` en segundos). La UI lo muestra junto al campo, con link a Planes. |
| Pantallas | C1 |

#### `GET /api/v1/projects/`

Proyectos del workspace, del más nuevo al más viejo.

| | |
|---|---|
| Id | `projects.list` |
| Backend | ⚠️ existe en parte · hueco 14 · `back/apps/videos/views.py · VideoProjectViewSet.list + VideoProjectSerializer` |
| Nota | Hoy devuelve un array sin paginar y sin filtros. Desde el PR #125 lee `X-Workspace-Id` y trae solo los proyectos del workspace activo (404 si el usuario no es miembro activo); B2 igual filtra por `workspace` en el navegador, como defensa. Propuesto: paginación de 25, `status_group`, `search`, `status_counts` y los campos extra del proyecto. |
| Sesión | JWT · `X-Workspace-Id` |
| Query | ProjectsQuery |
| Respuesta 200 | ProjectsResponse |
| Pantallas | B2, J1, B1 |

#### `POST /api/v1/projects/`

Crea un proyecto desde un archivo (multipart) o un link, reserva los tokens y arranca la ingesta.

| | |
|---|---|
| Id | `projects.create` |
| Backend | ✅ existe · `back/apps/videos/views.py · VideoProjectViewSet.create + VideoProjectSerializer.create/validate` |
| Nota | El código de InvalidUploadError no llega en el cuerpo (DRF solo manda el mensaje); el 403 trae un texto en inglés sin código. Desde el PR #125 el proyecto se crea en el workspace de `X-Workspace-Id` y se aplica el rol (el Lector recibe 403 `{ detail }` en español). |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido (multipart) | CreateProjectRequest |
| Respuesta 201 | CreateProjectResponse |
| Errores | **400** Validación. En `source_file` llega el mensaje de InvalidUploadError; la UI lo traduce por código (propuesto: `{message, code}`) o por el texto. `project_type` para video completo con link o de más de 10 min. Link con resolución distinta de 720p.<br>**402** Saldo insuficiente: `{ error, required }`. La UI abre el diálogo D2.<br>**403** El plan no lo permite (duración, resolución o SEO). La UI abre el diálogo D3.<br>**503** No se pudo encolar la ingesta (broker caído): `{ error, code: "IngestionNotQueued", project_id }`. El proyecto queda `failed` con ese `pipeline_error_code` y la reserva vuelve. La UI muestra el genérico de 5xx. |
| Pantallas | C1 |

#### `POST /api/v1/projects/from-prompt/`

Igual que crear, más `prompt`: la IA arma la configuración al iniciar la ingesta (queda en `metadata.pte`).

| | |
|---|---|
| Id | `projects.createFromPrompt` |
| Backend | ✅ existe · `back/apps/videos/views.py · VideoProjectViewSet.from_prompt` |
| Nota | `add_subtitles` es opcional y «no enviado» es un caso propio (`_NotSentBooleanField`, PR #121): al crear queda en true y al editar no cambia. Así el texto puede apagar los subtítulos; si el campo viaja, cuenta como elegido a mano y le gana al texto. |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido (multipart) | CreateFromPromptRequest |
| Respuesta 201 | CreateFromPromptResponse |
| Errores | **400** `prompt` vacío ("Contanos qué querés") o más largo que el máximo; más las validaciones de crear.<br>**402** Saldo insuficiente: `{ error, required }`. La UI abre el diálogo D2.<br>**403** El plan no lo permite (duración, resolución o SEO). La UI abre el diálogo D3.<br>**503** No se pudo encolar la ingesta (broker caído): `{ error, code: "IngestionNotQueued", project_id }`. El proyecto queda `failed` con ese `pipeline_error_code` y la reserva vuelve. La UI muestra el genérico de 5xx. |
| Pantallas | C1 |

#### `GET /api/v1/projects/:id/`

Detalle del proyecto con el estado del pipeline, la metadata, la transcripción y sus clips.

| | |
|---|---|
| Id | `projects.detail` |
| Backend | ⚠️ existe en parte · hueco 4, 15, 18 · `back/apps/videos/views.py · VideoProjectViewSet.retrieve + VideoProjectSerializer` |
| Nota | Faltan `cost` (vínculo con la transacción y estado devuelto), `stage_timings`, `eta_seconds`, `duration_seconds`, `uploaded_by_detail` y `thumbnail_url`. |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | Project |
| Errores | **404** No existe o es de otro workspace. La UI muestra K1. |
| Pantallas | D1, D1e, D1f, D3, D4, D5 |

#### `PATCH /api/v1/projects/:id/`

Renombra el proyecto.

| | |
|---|---|
| Id | `projects.update` |
| Backend | ✅ existe · `back/apps/videos/views.py · VideoProjectViewSet.partial_update (ModelViewSet) + VideoProjectSerializer.update` |
| Nota | El PATCH acepta más campos del serializer; el frontend solo manda `title`, así que no debería ver el 409. |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | UpdateProjectRequest |
| Respuesta 200 | Project |
| Errores | **400** Título vacío.<br>**403** El rol no puede editar (Lector).<br>**409** El proyecto se está renderizando y el pedido cambia algo más que `title` (ProjectRenderingError). El cuerpo trae solo `{ detail }`; el código `project_rendering` no llega. Renombrar siempre se puede. |
| Pantallas | B2, D1 |

#### `DELETE /api/v1/projects/:id/`

Borra el proyecto con su video, sus clips y sus publicaciones programadas. No devuelve lo cobrado.

| | |
|---|---|
| Id | `projects.delete` |
| Backend | ✅ existe · `back/apps/videos/views.py · VideoProjectViewSet.destroy (ModelViewSet)` |
| Nota | No aplica la regla de roles (hueco 3). |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 204 | NoContent |
| Errores | **403** El rol no puede borrar (Lector; Editor solo los propios). |
| Pantallas | B2, D1, D4 (diálogo) |

#### `GET /api/v1/projects/:id/paper-edit-data/`

Datos de la revisión: video proxy, transcripción, clips sugeridos y metadata.

| | |
|---|---|
| Id | `projects.paperEditData` |
| Backend | ✅ existe · `back/apps/videos/views.py · VideoProjectViewSet.paper_edit_data` |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | PaperEditData |
| Errores | **404** No existe o es de otro workspace. |
| Pantallas | D2 |

#### `POST /api/v1/projects/:id/approve-paper-edit/`

Aprueba los segmentos elegidos, reserva el costo del render y lo despacha.

| | |
|---|---|
| Id | `projects.approvePaperEdit` |
| Backend | ✅ existe · `back/apps/videos/views.py · VideoProjectViewSet.approve_paper_edit` |
| Nota | Propuesto: 409 con `code: "not_awaiting_approval"` para un proyecto que ya no está en revisión y 403 con `code` para el límite del plan, en lugar de los 400 de hoy. |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | ApprovePaperEditRequest |
| Respuesta 200 | ApprovePaperEditResponse |
| Errores | **400** Tres casos, todos `{ error }` (se distinguen con `approveErrorKind()` de `src/lib/error-messages.ts`): el proyecto ya no está en revisión ("El proyecto no está en espera de aprobación. Estado actual: rendering"): la UI avisa y lleva a Clips; el plan no permite la duración o la resolución ("Error en el motor de precios: …"): la UI abre D3; segmentos inválidos. De estos últimos, uno más largo que el límite del render (PR #124: "…cada segmento puede durar hasta 1800 segundos. Dividí los más largos en varios.") se muestra con la frase del servidor; el resto, con un mensaje genérico.<br>**402** Saldo insuficiente: `{ error, required }`. La UI abre D2.<br>**409** Solo en la carrera de dos aprobaciones simultáneas: `{ error }`. La UI lleva a Clips. |
| Pantallas | D2 |

#### `POST /api/v1/projects/:id/redispatch/`

Vuelve a despachar el pipeline de un proyecto detenido, desde la última etapa terminada. No cobra.

| | |
|---|---|
| Id | `projects.redispatch` |
| Backend | ❌ falta (propuesto) |
| Nota | Hoy es una acción del admin de Django (mapa §7: herramientas internas fuera del MVP). Se define para no tener que tocar el contrato cuando se exponga. |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 202 | Project |
| Errores | **403** Solo Dueño y Admin.<br>**409** El proyecto ya está en curso o terminó. |
| Pantallas | D1 |

#### `POST /api/v1/projects/:id/rerender/`

Renderiza todos los clips de nuevo con otro estilo (layout, subtítulos, brand kit). Cobra el render.

| | |
|---|---|
| Id | `projects.rerender` |
| Backend | ❌ falta (propuesto) · hueco 11 |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | RerenderRequest |
| Respuesta 202 | PaidActionResponse |
| Errores | **402** Saldo insuficiente: `{ error, required }`. La UI abre D2.<br>**409** El proyecto está procesando o renderizando. |
| Pantallas | D1, D4, D5 |

#### `GET /api/v1/projects/:id/activity/`

Quién hizo qué en el proyecto y sus movimientos de tokens, del más nuevo al más viejo.

| | |
|---|---|
| Id | `projects.activity` |
| Backend | ❌ falta (propuesto) · hueco 4 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | ActivityItem[] |
| Pantallas | D1f |

#### `POST /api/v1/projects/:id/clips/download/`

Arma un .zip con los clips listos (todos, o los de `clip_ids`) y devuelve la URL temporal.

| | |
|---|---|
| Id | `projects.downloadAll` |
| Backend | ❌ falta (propuesto) · hueco 17 |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | { clip_ids?: Id[] } |
| Respuesta 200 | { url: string, expires_at: IsoDateTime } |
| Errores | **409** Ningún clip está listo todavía. |
| Pantallas | D3 |

### Clips

Contrato: `src/api/contracts/clips.ts` · Hooks: `src/api/hooks/clips.ts` · Mocks: `src/api/mocks/handlers/clips.ts`

#### `GET /api/v1/clips/:id/`

Un clip con sus datos, sus capas y dónde está publicado.

| | |
|---|---|
| Id | `clips.detail` |
| Backend | ❌ falta (propuesto) · hueco 11 |
| Nota | Hoy los clips solo llegan dentro del proyecto (VideoClipSerializer). |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | Clip |
| Errores | **404** No existe o es de otro workspace. |
| Pantallas | D4, E1 |

#### `PATCH /api/v1/clips/:id/`

Cambia el título o el recorte. No renderiza: para eso está `render`.

| | |
|---|---|
| Id | `clips.update` |
| Backend | ❌ falta (propuesto) · hueco 11 |
| Nota | El 409 sigue la regla que ya aplica `VideoProjectSerializer.update` al PATCH del proyecto (ProjectRenderingError): lo que el render lee no cambia debajo de él. |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | UpdateClipRequest |
| Respuesta 200 | Clip |
| Errores | **400** Recorte inválido (fin menor o igual al inicio, o fuera del video).<br>**403** El rol no puede editar (Lector).<br>**409** El proyecto se está renderizando y el pedido cambia el recorte (`code: "project_rendering"`). Solo `title` se puede cambiar mientras tanto. La UI avisa y deja el borrador como está. |
| Pantallas | D4, D3, D5 |

#### `DELETE /api/v1/clips/:id/`

Borra el clip y cancela sus publicaciones programadas.

| | |
|---|---|
| Id | `clips.delete` |
| Backend | ❌ falta (propuesto) · hueco 11 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 204 | NoContent |
| Errores | **403** El rol no puede borrar. |
| Pantallas | D3 |

#### `POST /api/v1/clips/:id/render/`

Vuelve a renderizar el clip con su recorte actual y la configuración del proyecto. Cobra por la duración del clip.

| | |
|---|---|
| Id | `clips.render` |
| Backend | ❌ falta (propuesto) · hueco 11 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 202 | PaidActionResponse |
| Errores | **402** Saldo insuficiente: `{ error, required }`. La UI abre D2.<br>**403** El plan actual no permite la resolución del proyecto (`code: "resolution"`). La UI abre D3.<br>**409** El clip ya se está renderizando, o su proyecto (`code: "project_rendering"`). |
| Pantallas | D4, D5 |

#### `POST /api/v1/clips/:id/retry/`

Reintenta el render de un clip que falló. No cobra si el error fue del sistema.

| | |
|---|---|
| Id | `clips.retry` |
| Backend | ❌ falta (propuesto) · hueco 11 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 202 | Clip |
| Errores | **409** El clip no está en error. |
| Pantallas | D3 |

#### `GET /api/v1/clips/:id/download/`

Link de descarga firmado y temporal del clip renderizado.

| | |
|---|---|
| Id | `clips.download` |
| Backend | ❌ falta (propuesto) · hueco 5 |
| Nota | `s3_object_key` no es una URL: hay que firmarla (CloudflareR2Manager.generate_presigned_url). |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | { url: string, filename: string, expires_at: IsoDateTime } |
| Errores | **409** El clip todavía no está renderizado. |
| Pantallas | D3, D4, D5 |

#### `POST /api/v1/clips/:clipId/preview/`

Pide una vista previa gratis en 360p, o devuelve la que ya está en curso. Nunca reserva tokens.

| | |
|---|---|
| Id | `clips.requestPreview` |
| Backend | ✅ existe · `back/apps/videos/views.py · ClipPreviewView.post` |
| Nota | Responde 202 si la encola y 200 si ya había una en curso. El mapa (D4) todavía la da por faltante. |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 202 | ClipPreview |
| Errores | **409** El video fuente todavía no está, o el proyecto es de video completo (no tiene vista previa): `{ error }`.<br>**429** El workspace ya tiene el máximo de vistas previas en curso. La UI pide esperar unos segundos. |
| Pantallas | D2, D4 |

#### `GET /api/v1/clips/:clipId/preview/`

La vista previa vigente más nueva del clip. Se consulta cada 5 s hasta `ready` o `failed`.

| | |
|---|---|
| Id | `clips.preview` |
| Backend | ✅ existe · `back/apps/videos/views.py · ClipPreviewView.get` |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | ClipPreview |
| Errores | **404** No hay vista previa vigente: `{ error }`. No es un error para la UI: ofrece pedir una. |
| Pantallas | D2, D4 |

### Brand kits

Contrato: `src/api/contracts/brandkits.ts` · Hooks: `src/api/hooks/brandkits.ts` · Mocks: `src/api/mocks/handlers/brandkits.ts`

#### `GET /api/v1/brand-kits/`

Brand kits del workspace.

| | |
|---|---|
| Id | `brandKits.list` |
| Backend | ✅ existe · `back/apps/videos/views.py · BrandKitViewSet.list + BrandKitSerializer` |
| Nota | Desde el PR #125 la vista lee `X-Workspace-Id` y devuelve solo los kits del workspace activo (404 si el usuario no es miembro activo de ese workspace). La UI igual compara `workspace` antes de tocar el default de otro kit: es una defensa que no cuesta nada. Propuesto, todavía no existe: `watermark_position` (esquina de la marca de agua, F2). Mientras la respuesta no lo traiga, la UI no muestra el selector de esquina y el logo va abajo a la derecha. |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | BrandKit[] |
| Pantallas | F1, C1, B2, D5 |

#### `POST /api/v1/brand-kits/`

Crea un brand kit (multipart por las fuentes, el logo y los videos).

| | |
|---|---|
| Id | `brandKits.create` |
| Backend | ✅ existe · `back/apps/videos/views.py · BrandKitViewSet.create/perform_create` |
| Nota | Desde el PR #125 lo crea en el workspace de `X-Workspace-Id`, y aplica el rol: el Lector recibe 403 `{ detail }` en español. Sin ningún workspace responde 400. |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido (multipart) | BrandKitInput |
| Respuesta 201 | BrandKit |
| Errores | **400** Validación por campo (nombre vacío, color que no es hex, archivo inválido).<br>**403** El rol no puede crear ni editar brand kits (Lector). |
| Pantallas | F1, F2 |

#### `GET /api/v1/brand-kits/:id/`

Un brand kit.

| | |
|---|---|
| Id | `brandKits.detail` |
| Backend | ✅ existe · `back/apps/videos/views.py · BrandKitViewSet.retrieve` |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | BrandKit |
| Errores | **404** No existe o es de otro workspace. |
| Pantallas | F2 |

#### `PATCH /api/v1/brand-kits/:id/`

Edita un brand kit. Con `is_default: true` pasa a ser el default del workspace.

| | |
|---|---|
| Id | `brandKits.update` |
| Backend | ⚠️ existe en parte · `back/apps/videos/views.py · BrandKitViewSet.partial_update` |
| Nota | El backend no garantiza un solo default por workspace: al marcar uno debería desmarcar el resto (mapa F2). |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido (multipart) | { name?: string, description?: string, primary_color?: HexColor, secondary_color?: HexColor, accent_color?: HexColor, primary_font?: unknown \\| "", secondary_font?: unknown \\| "", watermark_logo?: unknown \\| "", intro_video?: unknown \\| "", outro_video?: unknown \\| "", is_default?: boolean, watermark_position?: WatermarkPosition } |
| Respuesta 200 | BrandKit |
| Errores | **400** Validación por campo.<br>**403** El rol no puede crear ni editar brand kits (Lector). |
| Pantallas | F1, F2 |

#### `DELETE /api/v1/brand-kits/:id/`

Borra un brand kit. Los proyectos que lo usaban quedan sin kit (SET_NULL).

| | |
|---|---|
| Id | `brandKits.delete` |
| Backend | ✅ existe · `back/apps/videos/views.py · BrandKitViewSet.destroy` |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 204 | NoContent |
| Errores | **403** El rol no puede crear ni editar brand kits (Lector). |
| Pantallas | F1, F2 |

#### `POST /api/v1/brand-kits/:id/duplicate/`

Copia un brand kit con sus archivos ("Copia de …"). La copia nunca es default.

| | |
|---|---|
| Id | `brandKits.duplicate` |
| Backend | ❌ falta (propuesto) |
| Nota | Sin número de hueco: el mapa (F1) pide duplicar y los archivos no se pueden copiar desde el navegador. |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 201 | BrandKit |
| Errores | **403** El rol no puede crear ni editar brand kits (Lector). |
| Pantallas | F1 |

### Redes sociales y publicaciones

Contrato: `src/api/contracts/social.ts` · Hooks: `src/api/hooks/social.ts` · Mocks: `src/api/mocks/handlers/social.ts`

#### `GET /api/v1/social-accounts/`

Una entrada por red (TikTok, Instagram Reels, YouTube Shorts) con su estado y sus límites.

| | |
|---|---|
| Id | `social.accounts` |
| Backend | ❌ falta (propuesto) · hueco 10 |
| Nota | Hoy solo existe Workspace.ayrshare_profile_key, sin flujo para conectar. |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | SocialAccount[] |
| Pantallas | H3, E1, B2 |

#### `POST /api/v1/social-accounts/connect/`

Devuelve la URL de la red (vía Ayrshare) para autorizar la cuenta en una ventana aparte.

| | |
|---|---|
| Id | `social.connect` |
| Backend | ❌ falta (propuesto) · hueco 10 |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | { platform: Platform, return_url?: string } |
| Respuesta 200 | { connect_url: string } |
| Errores | **403** El rol no conecta redes (solo Dueño y Admin). |
| Pantallas | D6, H3, E1 |

#### `DELETE /api/v1/social-accounts/:platform/`

Desconecta la cuenta de una red. Las publicaciones programadas en esa red quedan en error.

| | |
|---|---|
| Id | `social.disconnect` |
| Backend | ❌ falta (propuesto) · hueco 10 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 204 | NoContent |
| Errores | **403** El rol no desconecta redes. |
| Pantallas | H3 |

#### `GET /api/v1/scheduled-posts/`

Publicaciones del workspace, ordenadas por `publish_at`, con filtros por estado, red, rango y clip.

| | |
|---|---|
| Id | `social.posts` |
| Backend | ❌ falta (propuesto) · hueco 8 |
| Nota | Modelo: ScheduledPost. Hoy solo se crean desde el admin. |
| Sesión | JWT · `X-Workspace-Id` |
| Query | PostsQuery |
| Respuesta 200 | { count: number, next: string \\| null, previous: string \\| null, results: ScheduledPost[], status_counts?: { [clave]: number } } |
| Pantallas | E2, D3, D4 (diálogo) |

#### `POST /api/v1/scheduled-posts/`

Publica ahora o programa un clip en una red. Con `draft: true` crea un borrador, que dispara la generación del texto con IA.

| | |
|---|---|
| Id | `social.createPost` |
| Backend | ❌ falta (propuesto) · hueco 8, 9 |
| Nota | Crear solo encola: la respuesta sale apenas el post queda guardado (`QUEUED` o `SCHEDULED`) y la publicación en la red la hace el worker después, así que el pedido entra en el tiempo de espera normal. Acepta `Idempotency-Key`: el cliente manda una clave por envío y la repite si reintenta (por ejemplo, después de un pedido que venció); el backend guarda la respuesta exitosa (2xx) por clave y workspace al menos 24 h y la devuelve tal cual, sin crear ni publicar de nuevo; una respuesta de error no se guarda, así que reintentar con la misma clave después de corregir el problema (por ejemplo, conectar la cuenta) vuelve a intentarlo. El backend tiene que permitir el header en CORS (`CORS_ALLOW_HEADERS`). Al crear el post, la señal existente genera el SEO y la publicación usa Ayrshare. Reglas propuestas: (1) una publicación en cola sin texto propio espera a que termine la generación con IA antes de salir (hoy saldría con el caption de reserva); (2) si la generación falla en un borrador, sigue en `DRAFT` con `seo_status: "failed"` (hoy `_fail_seo_post` lo pasa a `FAILED`); (3) un borrador creado con `draft: true` que nadie programa ni publica en N horas (sugerido: 24) se borra solo: el panel E1 los descarta al cerrarse, pero una pestaña que se cierra de golpe puede dejar alguno. |
| Sesión | JWT · `X-Workspace-Id` · `Idempotency-Key` (una por envío; se repite al reintentar) |
| Pedido | CreatePostInput |
| Respuesta 201 | ScheduledPost |
| Errores | **400** Texto por encima del límite de la red, clip sin renderizar, o falta `tiktok.privacy_level`.<br>**403** El plan no permite programar (`code: "scheduling"`) o publicar en varias redes (`code: "crossposting"`), o el rol no publica.<br>**409** La cuenta de la red está desconectada o vencida (`code: "account_not_connected"`): la UI pide reconectar. |
| Pantallas | E1 |

#### `POST /api/v1/scheduled-posts/bulk/`

Crea varias publicaciones juntas (varios clips, varias redes). Todo o nada.

| | |
|---|---|
| Id | `social.createPostsBulk` |
| Backend | ❌ falta (propuesto) · hueco 8 |
| Nota | Hasta 60 publicaciones. Igual que `createPost`: solo encola (ninguna red se llama dentro del pedido) y acepta `Idempotency-Key`, con la misma regla: una clave repetida devuelve las publicaciones que creó el primer pedido. |
| Sesión | JWT · `X-Workspace-Id` · `Idempotency-Key` (una por envío; se repite al reintentar) |
| Pedido | { posts: CreatePostInput[] } |
| Respuesta 201 | { posts: ScheduledPost[] } |
| Errores | **400** Texto por encima del límite de la red, clip sin renderizar, o falta `tiktok.privacy_level`.<br>**403** El plan no permite programar (`code: "scheduling"`) o publicar en varias redes (`code: "crossposting"`), o el rol no publica.<br>**409** La cuenta de la red está desconectada o vencida (`code: "account_not_connected"`): la UI pide reconectar. |
| Pantallas | E1c |

#### `GET /api/v1/scheduled-posts/:id/`

Detalle de una publicación. Se consulta cada 5 s mientras está en cola o publicando.

| | |
|---|---|
| Id | `social.post` |
| Backend | ❌ falta (propuesto) · hueco 8 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | ScheduledPost |
| Errores | **404** No existe o es de otro workspace. |
| Pantallas | E2, E1 |

#### `PATCH /api/v1/scheduled-posts/:id/`

Edita el texto, reprograma, o publica un borrador (`publish_at`: fecha para programar, `null` para publicar ahora). Solo antes de `QUEUED`.

| | |
|---|---|
| Id | `social.updatePost` |
| Backend | ❌ falta (propuesto) · hueco 8 |
| Sesión | JWT · `X-Workspace-Id` |
| Pedido | UpdatePostRequest |
| Respuesta 200 | ScheduledPost |
| Errores | **400** Texto por encima del límite, fecha en el pasado, o falta `tiktok.privacy_level` al publicar un borrador de TikTok.<br>**403** El plan no permite programar (`code: "scheduling"`), o el rol no publica.<br>**409** La publicación ya está en cola, publicando o publicada: no se puede editar. |
| Pantallas | E2 |

#### `POST /api/v1/scheduled-posts/:id/cancel/`

Cancela una publicación que todavía no salió. La borra.

| | |
|---|---|
| Id | `social.cancelPost` |
| Backend | ❌ falta (propuesto) · hueco 8 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 204 | NoContent |
| Errores | **409** Ya se está publicando o ya se publicó. |
| Pantallas | E2 |

#### `POST /api/v1/scheduled-posts/:id/retry/`

Reintenta una publicación que falló (también una trabada en `PROCESSING`).

| | |
|---|---|
| Id | `social.retryPost` |
| Backend | ❌ falta (propuesto) · hueco 8 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | ScheduledPost |
| Errores | **409** La publicación no está en error. |
| Pantallas | E2 |

#### `POST /api/v1/scheduled-posts/:id/regenerate-seo/`

Vuelve a generar el caption y los hashtags con IA. Responde con `seo_status: "pending"`; el texto anterior se conserva hasta que llega el nuevo.

| | |
|---|---|
| Id | `social.regeneratePostSeo` |
| Backend | ❌ falta (propuesto) · hueco 8 |
| Nota | Hoy solo existe como acción del admin: ScheduledPostAdmin.regenerate_ai_metadata → queue_post_seo(post_id, regenerate=True). |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 202 | ScheduledPost |
| Errores | **403** El proyecto no pidió SEO o el plan no lo incluye (`code: "seo"`), o el rol no publica.<br>**409** La publicación ya se está publicando o ya se publicó: su texto no cambia. |
| Pantallas | E1 |

### Notificaciones

Contrato: `src/api/contracts/notifications.ts` · Hooks: `src/api/hooks/notifications.ts` · Mocks: `src/api/mocks/handlers/notifications.ts`

#### `GET /api/v1/notifications/`

Las últimas 20 notificaciones del usuario en el workspace y cuántas están sin leer.

| | |
|---|---|
| Id | `notifications.list` |
| Backend | ❌ falta (propuesto) · hueco 16 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | NotificationsResponse |
| Pantallas | J2, Barra |

#### `POST /api/v1/notifications/:id/read/`

Marca una notificación como leída.

| | |
|---|---|
| Id | `notifications.markRead` |
| Backend | ❌ falta (propuesto) · hueco 16 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 200 | Notification |
| Errores | **404** No existe. |
| Pantallas | J2 |

#### `POST /api/v1/notifications/read-all/`

Marca todas como leídas.

| | |
|---|---|
| Id | `notifications.markAllRead` |
| Backend | ❌ falta (propuesto) · hueco 16 |
| Sesión | JWT · `X-Workspace-Id` |
| Respuesta 204 | NoContent |
| Pantallas | J2 |

### Sistema

Contrato: `src/api/contracts/system.ts` · Hooks: `src/api/hooks/system.ts` · Mocks: `src/api/mocks/handlers/system.ts`

#### `GET /api/v1/status/`

Si la plataforma está en mantenimiento y hasta cuándo.

| | |
|---|---|
| Id | `system.status` |
| Backend | ❌ falta (propuesto) |
| Nota | Sin número de hueco: lo pide la pantalla K5 (horario estimado). |
| Sesión | Pública |
| Respuesta 200 | SystemStatus |
| Errores | **503** En mantenimiento sin poder responder: la UI muestra K5 igual. |
| Pantallas | K5 |

## Esquemas

### `common.ts`

**ColorValue:** string

**Decimal:** number \| string

**ErrorBody**

| Campo | Tipo |
|---|---|
| `error` (opcional) | string |
| `detail` (opcional) | string |
| `code` (opcional) | string |
| `required` (opcional) | Decimal |

**HexColor:** string

**Id:** string

**IsoDateTime:** string

**MemberRole:** "admin" \| "editor" \| "viewer"

**NoContent:** —

**PageQuery**

| Campo | Tipo |
|---|---|
| `page` (opcional) | number |
| `page_size` (opcional) | number |

**Platform:** "TIKTOK" \| "INSTAGRAM_REELS" \| "YOUTUBE_SHORTS"

**RenderResolution:** "720p" \| "1080p" \| "4K"

**Role:** "owner" \| "admin" \| "editor" \| "viewer"

**UserRef**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `name` | string |
| `email` (opcional) | string |

### `auth.ts`

**LoginRequest**

| Campo | Tipo |
|---|---|
| `email` | string |
| `password` | string |

**RegisterRequest**

| Campo | Tipo |
|---|---|
| `name` | string |
| `email` | string |
| `password` | string |
| `terms_accepted` | true |

**RegisterResponse**

| Campo | Tipo |
|---|---|
| `id` | string |
| `email` | string |
| `is_email_verified` | boolean |

**TokenPair**

| Campo | Tipo |
|---|---|
| `access` | string |
| `refresh` | string |

### `users.ts`

**Me**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `email` | string |
| `name` | string |
| `is_email_verified` | boolean |
| `tour_completed` | boolean |
| `terms_accepted_at` | IsoDateTime \\| null |
| `pending_email` (opcional) | string \\| null |
| `onboarding_use_case` (opcional) | OnboardingUseCase \\| null |
| `onboarding` (opcional) | { has_project: boolean, has_brand_kit: boolean, has_social_account: boolean, has_invited_member: boolean } |
| `active_workspace_id` | Id \\| null |
| `workspaces` | WorkspaceSummary[] |

**NotificationPreferences**

| Campo | Tipo |
|---|---|
| `project_ready` | boolean |
| `render_finished` | boolean |
| `project_failed` | boolean |
| `post_failed` | boolean |
| `low_balance` | boolean |

**OnboardingUseCase:** "podcast" \| "gaming" \| "education" \| "other"

**UpdateMeRequest**

| Campo | Tipo |
|---|---|
| `name` (opcional) | string |
| `email` (opcional) | string |
| `current_password` (opcional) | string |
| `tour_completed` (opcional) | boolean |
| `onboarding_use_case` (opcional) | OnboardingUseCase |
| `active_workspace_id` (opcional) | Id |

**WorkspaceSummary**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `name` | string |
| `role` | Role |
| `plan` | { id: Id, name: string } \\| null |
| `available_balance` | Decimal |

### `workspaces.ts`

**InvitationPreview**

| Campo | Tipo |
|---|---|
| `workspace_id` | Id |
| `workspace_name` | string |
| `inviter_name` | string |
| `role` | MemberRole |
| `email` | string |
| `status` | InvitationStatus |
| `has_account` | boolean |

**InvitationStatus:** "pending" \| "expired" \| "used"

**InviteRequest**

| Campo | Tipo |
|---|---|
| `emails` | string[] |
| `role` | MemberRole |

**Member**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `user` | UserRef \\| null |
| `email` | string |
| `role` | Role |
| `status` | MemberStatus |
| `joined_at` | IsoDateTime |
| `invited_by` (opcional) | UserRef \\| null |

**MembersResponse**

| Campo | Tipo |
|---|---|
| `members` | Member[] |
| `max_members` | number |
| `seats_used` | number |

**MemberStatus:** "pending" \| "active"

**Workspace**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `name` | string |
| `owner` | Id |
| `subscription_plan` | Id \\| null |
| `created_at` | IsoDateTime |
| `role` (opcional) | Role |
| `admins` (opcional) | { id: Id, name: string, role: Role }[] |

### `billing.ts`

**Estimate**

| Campo | Tipo |
|---|---|
| `base_cost` | Decimal |
| `add_ons_cost` | Decimal |
| `discount_rate` | Decimal |
| `discount_amount` | Decimal |
| `total` | Decimal |
| `available_balance` | Decimal |
| `balance_after` | Decimal |
| `sufficient` | boolean |
| `has_watermark` | boolean |
| `eta_seconds` | number \\| null |

**EstimateRequest**

| Campo | Tipo |
|---|---|
| `source` | "file" \\| "link" |
| `duration_seconds` (opcional) | number |
| `render_resolution` (opcional) | RenderResolution |
| `use_seo` (opcional) | boolean |
| `max_clips` (opcional) | number |

**Payment**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `status` | PurchaseStatus |
| `tokens` | number |
| `amount_usd` | Decimal |
| `plan_id` (opcional) | Id \\| null |
| `checkout_url` | string \\| null |
| `failure_reason` | string \\| null |
| `created_at` | IsoDateTime |
| `balance_after` | Decimal \\| null |

**Plan**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `name` | string |
| `max_video_duration_seconds` | number |
| `max_resolution` | RenderResolution |
| `has_watermark` | boolean |
| `allow_scheduling` | boolean |
| `allow_crossposting` | boolean |
| `has_seo_optimization` | boolean |
| `has_thumbnail_engine` | boolean |
| `base_discount_rate` | Decimal |
| `max_members` | number |
| `is_active` | boolean |
| `price_usd_monthly` | Decimal |
| `description` | string |
| `sort_order` | number |
| `is_recommended` | boolean |

**Pricing**

| Campo | Tipo |
|---|---|
| `base_cost_per_second_720p` | Decimal |
| `resolution_multipliers` | { 720p: Decimal, 1080p: Decimal, 4K: Decimal } |
| `feature_costs` | { seo_optimization: Decimal, ai_thumbnail: Decimal } |
| `link_flat_cost` | Decimal |
| `min_cost` | Decimal |
| `token_price_usd` | Decimal |
| `single_edit_max_duration_seconds` | number |
| `upload_max_duration_seconds` | number |
| `upload_max_size_bytes` | number |
| `upload_extensions` | string[] |
| `selection_criteria_max_chars` | number |
| `prompt_max_chars` | number |

**PurchaseStatus:** "pending" \| "approved" \| "rejected"

**Subscription**

| Campo | Tipo |
|---|---|
| `plan` | Plan |
| `status` | "active" \\| "past_due" \\| "canceled" |
| `renews_at` | IsoDateTime \\| null |
| `pending_plan` | { id: Id, name: string, effective_at: IsoDateTime } \\| null |

**TokenPackage**

| Campo | Tipo |
|---|---|
| `id` | string |
| `tokens` | number |
| `price_usd` | Decimal |
| `discount_rate` | Decimal |

**TokenPackages**

| Campo | Tipo |
|---|---|
| `packages` | TokenPackage[] |
| `custom_price_per_token_usd` | Decimal |
| `custom_min_tokens` | number |
| `local_currency` | string |
| `usd_to_local_rate` | Decimal |

**TokenPurchase**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `status` | PurchaseStatus |
| `tokens` | number |
| `amount_usd` | Decimal |
| `plan_id` (opcional) | Id \\| null |
| `checkout_url` | string \\| null |
| `failure_reason` | string \\| null |
| `created_at` | IsoDateTime |
| `balance_after` | Decimal \\| null |

**Transaction**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `wallet` | Id |
| `created_by` | Id \\| null |
| `amount` | Decimal |
| `transaction_type` | TransactionType |
| `status` | TransactionStatus |
| `description` | string |
| `created_at` | IsoDateTime |
| `kind` | TransactionKind |
| `created_by_detail` | UserRef \\| null |
| `project` | { id: Id, title: string } \\| null |
| `category` | "project" \\| "render" \\| "seo" \\| "purchase" \\| "other" |

**TransactionKind:** "purchase" \| "reserved" \| "charged" \| "refund" \| "released" \| "pending"

**TransactionKindTotal**

| Campo | Tipo |
|---|---|
| `count` | number |
| `amount` | Decimal |

**TransactionsQuery**

| Campo | Tipo |
|---|---|
| `page` (opcional) | number |
| `page_size` (opcional) | number |
| `kind` (opcional) | TransactionKind |
| `month` (opcional) | string |
| `project` (opcional) | Id |

**TransactionStatus:** "PENDING" \| "RESERVED" \| "COMPLETED" \| "FAILED"

**TransactionType:** "DEPOSIT" \| "SPEND" \| "REFUND"

**UsageDay**

| Campo | Tipo |
|---|---|
| `date` | string |
| `projects` | Decimal |
| `renders` | Decimal |
| `seo` | Decimal |

**Wallet**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `workspace` | Id |
| `available_balance` | Decimal |
| `reserved_balance` | Decimal |
| `updated_at` | IsoDateTime |
| `spent_this_month` | Decimal |
| `low_balance_threshold` | Decimal |
| `reservations` | { transaction_id: Id, project_id: Id \\| null, project_title: string \\| null, amount: Decimal, created_at: IsoDateTime }[] |

### `projects.ts`

**ActivityItem**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `type` | "project_created" \\| "prompt_applied" \\| "review_ready" \\| "review_approved" \\| "render_finished" \\| "clip_edited" \\| "clip_retried" \\| "post_scheduled" \\| "post_published" \\| "post_failed" \\| "project_renamed" \\| "project_failed" \\| "tokens_reserved" \\| "tokens_charged" \\| "tokens_refunded" |
| `actor` | UserRef \\| null |
| `created_at` | IsoDateTime |
| `description` | string |
| `amount` | Decimal \\| null |
| `platform` (opcional) | Platform \\| null |

**ApprovedSegment**

| Campo | Tipo |
|---|---|
| `start` | number |
| `end` | number |
| `text` (opcional) | string |

**ApprovePaperEditRequest**

| Campo | Tipo |
|---|---|
| `approved_segments` | { start: number, end: number, text?: string, title?: string, clip_id?: Id }[] |

**ApprovePaperEditResponse**

| Campo | Tipo |
|---|---|
| `message` | string |
| `project_id` | Id |
| `final_cost` | Decimal |
| `transaction_id` | Id |

**AspectRatio:** "9:16" \| "1:1" \| "16:9"

**Clip**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `project` | Id |
| `title` | string |
| `s3_object_key` | string \\| null |
| `start_time` | number |
| `end_time` | number |
| `virality_score` | number |
| `ai_reasoning` | string |
| `status` | ClipStatus |
| `created_at` | IsoDateTime |
| `layers` | ClipLayer[] |
| `preview_url` (opcional) | string \\| null |
| `thumbnail_url` (opcional) | string \\| null |
| `error_code` (opcional) | string \\| null |
| `posts` (opcional) | ClipPostRef[] |

**ClipLayer**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `clip` | Id |
| `layer_type` | "text" \\| "image" \\| "video" \\| "audio" |
| `start_time` | number |
| `end_time` | number \\| null |
| `content` | string |
| `asset_file` | string \\| null |
| `config` | { [clave]: unknown } |
| `is_ai_generated` | boolean |
| `prompt_used` | string |

**ClipPostRef**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `platform` | Platform |
| `status` | PostStatus |
| `publish_at` | IsoDateTime |

**ClipStatus:** "draft" \| "rendering" \| "completed" \| "published" \| "failed"

**CostDetails**

| Campo | Tipo |
|---|---|
| `tokens_reserved` | Decimal |
| `transaction_id` | Id |

**CreateFromPromptRequest**

| Campo | Tipo |
|---|---|
| `title` (opcional) | string |
| `source_file` (opcional) | unknown |
| `video_url` (opcional) | string |
| `project_type` (opcional) | ProjectType |
| `editing_style` (opcional) | EditingStyle |
| `intelligence_level` (opcional) | IntelligenceLevel |
| `auto_render_bypass` (opcional) | boolean |
| `brand_kit` (opcional) | Id |
| `aspect_ratio` (opcional) | AspectRatio |
| `render_layout` (opcional) | Layout |
| `gameplay_position` (opcional) | Position |
| `camera_selection_position` (opcional) | Position |
| `manual_camera_x` (opcional) | number |
| `manual_camera_y` (opcional) | number |
| `manual_camera_zoom` (opcional) | number |
| `speaker_tracking` (opcional) | boolean |
| `add_subtitles` (opcional) | boolean |
| `subtitle_color` (opcional) | HexColor |
| `subtitle_words_per_segment` (opcional) | number |
| `subtitle_size` (opcional) | SubtitleSize |
| `subtitle_position` (opcional) | SubtitlePosition |
| `use_facetracking` (opcional) | boolean |
| `remove_silences` (opcional) | boolean |
| `max_clips` (opcional) | number |
| `selection_criteria` (opcional) | string |
| `use_seo` (opcional) | boolean |
| `render_resolution` (opcional) | RenderResolution |
| `prompt` | string |

**CreateFromPromptResponse**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `workspace` | Id |
| `uploaded_by` | Id \\| null |
| `title` | string |
| `original_r2_key` | string \\| null |
| `proxy_r2_key` | string \\| null |
| `final_export_r2_key` | string \\| null |
| `project_type` | ProjectType |
| `editing_style` | EditingStyle |
| `intelligence_level` | IntelligenceLevel |
| `auto_render_bypass` | boolean |
| `brand_kit` | Id \\| null |
| `brand_kit_name` (opcional) | string |
| `aspect_ratio` | AspectRatio |
| `render_layout` | Layout |
| `gameplay_position` | Position |
| `camera_selection_position` | Position |
| `manual_camera_x` | number |
| `manual_camera_y` | number |
| `manual_camera_zoom` | number |
| `speaker_tracking` | boolean |
| `add_subtitles` | boolean |
| `subtitle_color` | ColorValue |
| `subtitle_with_emojis` | boolean |
| `subtitle_words_per_segment` | number |
| `subtitle_size` | SubtitleSize |
| `subtitle_position` | SubtitlePosition |
| `use_facetracking` | boolean |
| `remove_silences` | boolean |
| `source_file` | string \\| null |
| `video_url` | string \\| null |
| `metadata` | ProjectMetadata |
| `ai_rationale_log` | { [clave]: unknown } |
| `transcript_data` | TranscriptItem[] \\| null |
| `approved_segments` | ApprovedSegment[] \\| null |
| `status` | ProjectStatus |
| `pipeline_stage` | PipelineStage |
| `pipeline_stage_status` | PipelineStageStatus |
| `pipeline_attempts` | number |
| `pipeline_error_code` | string |
| `pipeline_error_at` | IsoDateTime \\| null |
| `created_at` | IsoDateTime |
| `clips` | Clip[] |
| `proxy_url` | string \\| null |
| `uploaded_by_detail` (opcional) | UserRef \\| null |
| `thumbnail_url` (opcional) | string \\| null |
| `duration_seconds` (opcional) | number \\| null |
| `cost` (opcional) | ProjectCost \\| null |
| `stage_timings` (opcional) | { [clave]: StageTiming } |
| `eta_seconds` (opcional) | number \\| null |
| `cost_details` | CostDetails |
| `message` | string |
| `prompt_config` | { status: "pending", detail: string } |

**CreateProjectRequest**

| Campo | Tipo |
|---|---|
| `title` (opcional) | string |
| `source_file` (opcional) | unknown |
| `video_url` (opcional) | string |
| `project_type` (opcional) | ProjectType |
| `editing_style` (opcional) | EditingStyle |
| `intelligence_level` (opcional) | IntelligenceLevel |
| `auto_render_bypass` (opcional) | boolean |
| `brand_kit` (opcional) | Id |
| `aspect_ratio` (opcional) | AspectRatio |
| `render_layout` (opcional) | Layout |
| `gameplay_position` (opcional) | Position |
| `camera_selection_position` (opcional) | Position |
| `manual_camera_x` (opcional) | number |
| `manual_camera_y` (opcional) | number |
| `manual_camera_zoom` (opcional) | number |
| `speaker_tracking` (opcional) | boolean |
| `add_subtitles` (opcional) | boolean |
| `subtitle_color` (opcional) | HexColor |
| `subtitle_words_per_segment` (opcional) | number |
| `subtitle_size` (opcional) | SubtitleSize |
| `subtitle_position` (opcional) | SubtitlePosition |
| `use_facetracking` (opcional) | boolean |
| `remove_silences` (opcional) | boolean |
| `max_clips` (opcional) | number |
| `selection_criteria` (opcional) | string |
| `use_seo` (opcional) | boolean |
| `render_resolution` (opcional) | RenderResolution |

**CreateProjectResponse**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `workspace` | Id |
| `uploaded_by` | Id \\| null |
| `title` | string |
| `original_r2_key` | string \\| null |
| `proxy_r2_key` | string \\| null |
| `final_export_r2_key` | string \\| null |
| `project_type` | ProjectType |
| `editing_style` | EditingStyle |
| `intelligence_level` | IntelligenceLevel |
| `auto_render_bypass` | boolean |
| `brand_kit` | Id \\| null |
| `brand_kit_name` (opcional) | string |
| `aspect_ratio` | AspectRatio |
| `render_layout` | Layout |
| `gameplay_position` | Position |
| `camera_selection_position` | Position |
| `manual_camera_x` | number |
| `manual_camera_y` | number |
| `manual_camera_zoom` | number |
| `speaker_tracking` | boolean |
| `add_subtitles` | boolean |
| `subtitle_color` | ColorValue |
| `subtitle_with_emojis` | boolean |
| `subtitle_words_per_segment` | number |
| `subtitle_size` | SubtitleSize |
| `subtitle_position` | SubtitlePosition |
| `use_facetracking` | boolean |
| `remove_silences` | boolean |
| `source_file` | string \\| null |
| `video_url` | string \\| null |
| `metadata` | ProjectMetadata |
| `ai_rationale_log` | { [clave]: unknown } |
| `transcript_data` | TranscriptItem[] \\| null |
| `approved_segments` | ApprovedSegment[] \\| null |
| `status` | ProjectStatus |
| `pipeline_stage` | PipelineStage |
| `pipeline_stage_status` | PipelineStageStatus |
| `pipeline_attempts` | number |
| `pipeline_error_code` | string |
| `pipeline_error_at` | IsoDateTime \\| null |
| `created_at` | IsoDateTime |
| `clips` | Clip[] |
| `proxy_url` | string \\| null |
| `uploaded_by_detail` (opcional) | UserRef \\| null |
| `thumbnail_url` (opcional) | string \\| null |
| `duration_seconds` (opcional) | number \\| null |
| `cost` (opcional) | ProjectCost \\| null |
| `stage_timings` (opcional) | { [clave]: StageTiming } |
| `eta_seconds` (opcional) | number \\| null |
| `cost_details` | CostDetails |
| `message` | string |

**EditingStyle:** "dynamic" \| "minimalist" \| "vlog" \| "hormozi" \| "custom"

**IntelligenceLevel:** "fast" \| "smart"

**Layout:** "fill" \| "fit" \| "blurred" \| "split" \| "pip" \| "versus" \| "active"

**LinkProbe**

| Campo | Tipo |
|---|---|
| `video_url` | string |
| `provider` | string |
| `title` | string |
| `duration_seconds` | number |
| `thumbnail_url` | string \\| null |

**PaidActionResponse**

| Campo | Tipo |
|---|---|
| `message` | string |
| `final_cost` | Decimal |
| `transaction_id` | Id |

**PaperEditData**

| Campo | Tipo |
|---|---|
| `project_id` | Id |
| `title` | string |
| `status` | ProjectStatus |
| `proxy_url` | string \\| null |
| `transcript` | TranscriptItem[] \\| null |
| `ai_suggestions` | Clip[] |
| `metadata` | ProjectMetadata |

**PipelineStage:** "uploaded" \| "audio_extracted" \| "transcribed" \| "moderated" \| "clips_selected" \| "render_dispatched" \| "completed" \| "partial" \| "failed"

**PipelineStageStatus:** { [clave]: StageStatus }

**Position:** "left" \| "center" \| "right"

**PostStatus:** "DRAFT" \| "SCHEDULED" \| "QUEUED" \| "PROCESSING" \| "PUBLISHED" \| "FAILED"

**Project**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `workspace` | Id |
| `uploaded_by` | Id \\| null |
| `title` | string |
| `original_r2_key` | string \\| null |
| `proxy_r2_key` | string \\| null |
| `final_export_r2_key` | string \\| null |
| `project_type` | ProjectType |
| `editing_style` | EditingStyle |
| `intelligence_level` | IntelligenceLevel |
| `auto_render_bypass` | boolean |
| `brand_kit` | Id \\| null |
| `brand_kit_name` (opcional) | string |
| `aspect_ratio` | AspectRatio |
| `render_layout` | Layout |
| `gameplay_position` | Position |
| `camera_selection_position` | Position |
| `manual_camera_x` | number |
| `manual_camera_y` | number |
| `manual_camera_zoom` | number |
| `speaker_tracking` | boolean |
| `add_subtitles` | boolean |
| `subtitle_color` | ColorValue |
| `subtitle_with_emojis` | boolean |
| `subtitle_words_per_segment` | number |
| `subtitle_size` | SubtitleSize |
| `subtitle_position` | SubtitlePosition |
| `use_facetracking` | boolean |
| `remove_silences` | boolean |
| `source_file` | string \\| null |
| `video_url` | string \\| null |
| `metadata` | ProjectMetadata |
| `ai_rationale_log` | { [clave]: unknown } |
| `transcript_data` | TranscriptItem[] \\| null |
| `approved_segments` | ApprovedSegment[] \\| null |
| `status` | ProjectStatus |
| `pipeline_stage` | PipelineStage |
| `pipeline_stage_status` | PipelineStageStatus |
| `pipeline_attempts` | number |
| `pipeline_error_code` | string |
| `pipeline_error_at` | IsoDateTime \\| null |
| `created_at` | IsoDateTime |
| `clips` | Clip[] |
| `proxy_url` | string \\| null |
| `uploaded_by_detail` (opcional) | UserRef \\| null |
| `thumbnail_url` (opcional) | string \\| null |
| `duration_seconds` (opcional) | number \\| null |
| `cost` (opcional) | ProjectCost \\| null |
| `stage_timings` (opcional) | { [clave]: StageTiming } |
| `eta_seconds` (opcional) | number \\| null |

**ProjectCost**

| Campo | Tipo |
|---|---|
| `amount` | Decimal |
| `state` | "reserved" \\| "charged" \\| "refunded" |
| `transaction_id` | Id \\| null |

**ProjectMetadata**

| Campo | Tipo |
|---|---|
| `max_clips` (opcional) | number |
| `selection_criteria` (opcional) | string |
| `render_resolution` (opcional) | RenderResolution |
| `use_seo` (opcional) | boolean |
| `source_media` (opcional) | { duration_s: number, width: number, height: number, mime: string } |
| `priced_duration_s` (opcional) | number |
| `duration` (opcional) | number |
| `reservation_id` (opcional) | string |
| `render_reservation_id` (opcional) | string |
| `final_duration` (opcional) | number |
| `full_text` (opcional) | string |
| `pte_prompt` (opcional) | string |
| `pte_explicit` (opcional) | string[] |
| `pte` (opcional) | PteResult |
| `no_speech` (opcional) | boolean |
| `auto_render_skipped` (opcional) | "insufficient_funds" \\| "plan_limit" |
| `auto_render_reservation_id` (opcional) | string |
| `auto_render_seconds` (opcional) | number |
| `auto_render_clip_ids` (opcional) | string[] |

**ProjectsQuery**

| Campo | Tipo |
|---|---|
| `page` (opcional) | number |
| `page_size` (opcional) | number |
| `status_group` (opcional) | ProjectStatusGroup |
| `search` (opcional) | string |
| `ordering` (opcional) | "-created_at" \\| "created_at" \\| "title" |

**ProjectsResponse:** { count: number, next: string \| null, previous: string \| null, results: Project[], status_counts?: { [clave]: number } } \| Project[]

**ProjectStatus:** "uploaded" \| "ingesting" \| "awaiting_approval" \| "rendering" \| "completed" \| "partial" \| "failed"

**ProjectStatusGroup:** "all" \| "in_progress" \| "to_review" \| "ready" \| "failed"

**ProjectType:** "repurpose" \| "single_edit"

**PteResult**

| Campo | Tipo |
|---|---|
| `status` | "applied" \\| "empty" \\| "failed" |
| `applied` (opcional) | { [clave]: unknown } |
| `ignored` (opcional) | { field: string, reason: string }[] |
| `error_code` (opcional) | string |

**RerenderRequest**

| Campo | Tipo |
|---|---|
| `editing_style` (opcional) | EditingStyle |
| `render_layout` (opcional) | Layout |
| `aspect_ratio` (opcional) | AspectRatio |
| `brand_kit` (opcional) | Id \\| null |
| `add_subtitles` (opcional) | boolean |
| `subtitle_color` (opcional) | HexColor |
| `subtitle_size` (opcional) | SubtitleSize |
| `subtitle_position` (opcional) | SubtitlePosition |
| `subtitle_words_per_segment` (opcional) | number |
| `use_facetracking` (opcional) | boolean |
| `remove_silences` (opcional) | boolean |

**StageStatus:** "pending" \| "running" \| "completed" \| "failed" \| "pending_capacity"

**StageTiming**

| Campo | Tipo |
|---|---|
| `started_at` | IsoDateTime \\| null |
| `finished_at` | IsoDateTime \\| null |
| `duration_s` | number \\| null |

**SubtitlePosition:** "top" \| "center" \| "bottom"

**SubtitleSize:** "small" \| "medium" \| "large"

**TranscriptItem**

| Campo | Tipo |
|---|---|
| `start` | number |
| `end` | number |
| `text` | string |

**UpdateProjectRequest**

| Campo | Tipo |
|---|---|
| `title` (opcional) | string |

### `clips.ts`

**ClipPreview**

| Campo | Tipo |
|---|---|
| `preview_id` | Id |
| `clip_id` | Id |
| `status` | PreviewStatus |
| `url` | string \\| null |
| `expires_at` | IsoDateTime |
| `error_code` | string \\| null |
| `cost_tokens` | 0 |
| `simplified` | { height: number, face_tracking: boolean, silence_removal: boolean } |

**PreviewStatus:** "pending" \| "rendering" \| "ready" \| "failed"

**UpdateClipRequest**

| Campo | Tipo |
|---|---|
| `title` (opcional) | string |
| `start_time` (opcional) | number |
| `end_time` (opcional) | number |

### `brandkits.ts`

**BrandKit**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `workspace` | Id |
| `user` | Id \\| null |
| `name` | string |
| `description` | string |
| `primary_font` | string \\| null |
| `secondary_font` | string \\| null |
| `primary_color` | ColorValue |
| `secondary_color` | ColorValue |
| `accent_color` | ColorValue |
| `watermark_logo` | string \\| null |
| `intro_video` | string \\| null |
| `outro_video` | string \\| null |
| `is_default` | boolean |
| `created_at` | IsoDateTime |
| `watermark_position` (opcional) | WatermarkPosition |

**BrandKitInput**

| Campo | Tipo |
|---|---|
| `name` | string |
| `description` (opcional) | string |
| `primary_color` (opcional) | HexColor |
| `secondary_color` (opcional) | HexColor |
| `accent_color` (opcional) | HexColor |
| `primary_font` (opcional) | unknown \\| "" |
| `secondary_font` (opcional) | unknown \\| "" |
| `watermark_logo` (opcional) | unknown \\| "" |
| `intro_video` (opcional) | unknown \\| "" |
| `outro_video` (opcional) | unknown \\| "" |
| `is_default` (opcional) | boolean |
| `watermark_position` (opcional) | WatermarkPosition |

**WatermarkPosition:** "top_left" \| "top_right" \| "bottom_left" \| "bottom_right"

### `social.ts`

**CreatePostInput**

| Campo | Tipo |
|---|---|
| `video_clip` | Id |
| `platform` | Platform |
| `publish_at` (opcional) | IsoDateTime \\| null |
| `caption` (opcional) | string |
| `hashtags` (opcional) | string[] |
| `tiktok` (opcional) | TikTokOptions |
| `draft` (opcional) | boolean |

**PostsQuery**

| Campo | Tipo |
|---|---|
| `page` (opcional) | number |
| `page_size` (opcional) | number |
| `status` (opcional) | PostStatus |
| `platform` (opcional) | Platform |
| `from` (opcional) | IsoDateTime |
| `to` (opcional) | IsoDateTime |
| `project` (opcional) | Id |
| `clip` (opcional) | Id |

**ScheduledPost**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `video_clip` | Id |
| `platform` | Platform |
| `publish_at` | IsoDateTime |
| `status` | PostStatus |
| `error_log` | string \\| null |
| `retry_count` | number |
| `generated_caption` | string \\| null |
| `generated_hashtags` | string[] \\| null |
| `created_at` | IsoDateTime |
| `updated_at` | IsoDateTime |
| `caption` | string |
| `hashtags` | string[] |
| `seo_status` | "none" \\| "pending" \\| "ready" \\| "failed" |
| `clip` | { id: Id, title: string, project_id: Id, project_title: string, duration_seconds: number, thumbnail_url: string \\| null } |
| `post_url` | string \\| null |
| `error_message` | string \\| null |
| `error_code` | string \\| null |
| `tiktok` | TikTokOptions \\| null |

**SocialAccount**

| Campo | Tipo |
|---|---|
| `platform` | Platform |
| `status` | SocialAccountStatus |
| `username` | string \\| null |
| `connected_at` | IsoDateTime \\| null |
| `limits` | { caption_max_chars: number, hashtags_max: number, max_duration_seconds: number, aspect_ratios: string[] } |
| `tiktok` | { privacy_options: TikTokPrivacy[], comment_disabled: boolean, duet_disabled: boolean, stitch_disabled: boolean } \\| null |

**SocialAccountStatus:** "connected" \| "expired" \| "disconnected"

**TikTokOptions**

| Campo | Tipo |
|---|---|
| `privacy_level` | TikTokPrivacy |
| `allow_comment` | boolean |
| `allow_duet` | boolean |
| `allow_stitch` | boolean |
| `brand_organic` | boolean |
| `branded_content` | boolean |

**TikTokPrivacy:** "PUBLIC_TO_EVERYONE" \| "MUTUAL_FOLLOW_FRIENDS" \| "FOLLOWER_OF_CREATOR" \| "SELF_ONLY"

**UpdatePostRequest**

| Campo | Tipo |
|---|---|
| `publish_at` (opcional) | IsoDateTime \\| null |
| `caption` (opcional) | string |
| `hashtags` (opcional) | string[] |
| `tiktok` (opcional) | TikTokOptions |

### `notifications.ts`

**Notification**

| Campo | Tipo |
|---|---|
| `id` | Id |
| `type` | NotificationType |
| `title` | string |
| `body` | string |
| `created_at` | IsoDateTime |
| `read_at` | IsoDateTime \\| null |
| `project_id` | Id \\| null |
| `post_id` | Id \\| null |

**NotificationsResponse**

| Campo | Tipo |
|---|---|
| `unread_count` | number |
| `results` | Notification[] |

**NotificationType:** "project_ready" \| "render_finished" \| "project_failed" \| "post_failed" \| "low_balance"

### `system.ts`

**SystemStatus**

| Campo | Tipo |
|---|---|
| `maintenance` | boolean |
| `maintenance_window` | { starts_at: IsoDateTime, ends_at: IsoDateTime } \\| null |
