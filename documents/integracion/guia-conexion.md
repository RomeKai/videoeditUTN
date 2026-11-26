# Guía para conectar la web con el backend real

> **Para:** Pedro (moyanop).
> **Estado:** `dev` al 26/11/2026 (merge del PR #130).
> **Qué es:** cómo conectar la app web (`front/`) con Django (`back/`), un área por vez, sin pisarte con el resto.

El frontend ya funciona entero contra mocks. Conectar un área es hacer que el backend responda lo que el contrato dice y apagar el mock de esa área. Esta guía te dice cómo, qué reglas tiene que cumplir cada endpoint nuevo, qué falta, en qué orden conviene y quién está con qué.

## Índice

1. [Cómo funciona la conexión](#1-cómo-funciona-la-conexión)
2. [Reglas que ya rigen en el backend](#2-reglas-que-ya-rigen-en-el-backend)
3. [Endpoints que faltan, por área](#3-endpoints-que-faltan-por-área)
4. [Orden sugerido para el piloto](#4-orden-sugerido-para-el-piloto)
5. [Quién hace qué](#5-quién-hace-qué)
6. [Reglas decididas, huecos y bloqueos](#6-reglas-decididas-huecos-y-bloqueos)
7. [Checklist: «el área está conectada»](#7-checklist-el-área-está-conectada)

---

## 1. Cómo funciona la conexión

### El contrato es el acuerdo

- Cada endpoint está definido una sola vez en `front/src/api/contracts/<dominio>.ts`, con `defineEndpoint({ … })` y esquemas [zod](https://zod.dev).
- De ahí salen los tipos, los hooks (`front/src/api/hooks/`), los mocks (`front/src/api/mocks/handlers/`) y el documento generado `front/src/api/CONTRACT.md`.
- **JSON en snake_case, con los nombres de campo exactos del backend.** Ids UUID. Fechas ISO 8601 en UTC. Rutas con la barra final de DRF.
- Si el backend necesita otra forma, **se cambia primero el contrato** y después el mock y el hook. Nunca al revés.

Hoy el contrato tiene **80 endpoints: 14 existen, 3 existen en parte y 63 son propuestos.** `npm run contract:doc` lo confirma: imprime `CONTRACT.md: 80 endpoints (14 existen, 3 en parte, 63 propuestos)`.

Cada endpoint tiene un bloque `backend`:

```ts
backend: { status: 'proposed', gap: 13, note: '…' }
```

- `status`: `'exists'`, `'partial'` o `'proposed'` (el valor en el código es `exists`, no `existing`).
- `gap`: el número de hueco de la [sección 8 del mapa de pantallas](../producto/mapa-de-pantallas.md#8-huecos-de-backend).
- `source`: la vista de DRF que lo implementa. Es obligatorio para `exists` y `partial`: `contracts.test.ts` exige que empiece con `back/`.
- `note`: lo que falta o lo que hay que saber.

### Para conectar un endpoint

1. **Implementalo en Django siguiendo el contrato:** ruta, método, campos, código de éxito y errores. La sección 2 tiene las reglas que no se negocian.
2. **Pasá su `backend.status` a `'exists'`** y citá la vista:
   ```ts
   backend: { status: 'exists', source: 'back/apps/payments/views.py · PublicPlanListView' },
   ```
   Si al frontend todavía le falta algo, usá `'partial'` con una `note` que diga qué.
3. **Regenerá el documento:** `cd front && npm run contract:doc`. Actualiza `src/api/CONTRACT.md` y `ROUTES.md`. `npm test` falla si quedó desactualizado.
4. **Probalo:** pytest en el backend, `npm test` en el frontend y la pantalla a mano contra el backend real (sección 7).

### Modo híbrido

El backend responde una parte de la API y los mocks el resto, para conectar de a un dominio. El detalle, el puente de identidad y la tabla de qué combinaciones son coherentes están en [`front/README.md`](../../front/README.md) («Modo híbrido»). En corto:

```bash
# front/.env.development.local   (no .env.local: ese también lo lee `next build`)
NEXT_PUBLIC_API_MODE=hybrid
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_API_REAL=
```

- `NEXT_PUBLIC_API_MODE=mock|hybrid|real`. Sin ella sigue valiendo `NEXT_PUBLIC_API_MOCKS` (`true` = mock, `false` = real).
- `NEXT_PUBLIC_API_REAL`: lista separada por comas de lo que va al backend real. Acepta dominios del contrato (`users`, `workspaces`, `projects`, `clips`, `brandkits`, `billing`, `social`, `notifications`, `system`), ids de endpoint (`billing.plans`, `users.me`) y la palabra `existing`.
  - **Sin lista** va al backend todo endpoint que el contrato marca `exists` **o `partial`**; el resto sigue en mocks. Pasar un endpoint a `exists` en `front/src/api/contracts/` alcanza para que deje de simularse.
  - Un dominio en la lista va **entero**, también lo que todavía es `proposed` (responde 404 hasta que exista). Para sumar un solo endpoint a lo existente: `NEXT_PUBLIC_API_REAL=existing,users.me`.
- `auth` va siempre al backend real en `hybrid` (el backend no acepta los tokens simulados): se entra con un usuario del backend.
- Mientras `/me` es simulado hay un puente de desarrollo: el mock toma el `user_id` del JWT real y muestra un único workspace marcador, para el que el cliente no manda `X-Workspace-Id` (el backend usa el workspace por defecto del usuario). Cuando `/me` exista, se va solo.
- El panel de desarrollo (el matraz) muestra cada dominio como Real, Mock o Mixto y deja cambiarlo en ese navegador. Cada respuesta de un mock lleva la cabecera `X-OC-Mock`.
- El modo queda fijo al construir, y un build de producción que no sea `real` hay que pedirlo con `API_MOCKS_IN_BUILD=true`.

---

## 2. Reglas que ya rigen en el backend

Todo endpoint nuevo las cumple. Están probadas y el frontend cuenta con ellas.

### Workspace activo

- El cliente manda `X-Workspace-Id` en cada pedido con sesión. `resolve_active_workspace(request)` (`back/apps/core/workspace.py`) lo resuelve y lo guarda en el request.
- **Solo cuentan el dueño (`Workspace.owner`) y los miembros `ACTIVE`.** Una invitación `PENDING` no da acceso.
- Un id mal formado, un workspace que no existe, uno ajeno y una invitación pendiente responden lo mismo: 404 con `NOT_FOUND_DETAIL` («No encontramos ese espacio de trabajo.»). Así no se pueden adivinar workspaces.
- Sin el header, usa `_default_workspace`: el más viejo del que es dueño, si no la membresía activa más vieja.
- Las vistas de proyectos y brand kits usan `ActiveWorkspaceMixin`: pone `IsAuthenticated` y `WorkspaceRolePermission`, expone `self.active_workspace` y lo pasa al serializer como `context['workspace']`.
  - Hoy vive en `back/apps/videos/views.py`. `back/apps/core/mixins.py` está vacío. Si lo necesitás en otra app, proponé moverlo a `apps/core` en un PR chico; no lo copies.
- **El queryset filtra por el workspace activo.** Así un objeto de otro workspace da 404 solo (`get_object`), aunque el usuario sea miembro de los dos.
- **Ojo con `/workspaces/:id/…`:** esos endpoints son `workspaceScoped: false` en el contrato. El cliente no manda el header: el workspace viene en la ruta. `WorkspaceRolePermission` mira el header (o el default), no la ruta, así que no sirve tal cual. Usá `require_workspace_role(user, workspace_id, accion, lock=False)` (`back/apps/core/permissions.py`): toma el workspace de la ruta, resuelve el rol con `role_in` y reusa `ROLE_MATRIX` y `DENIED`. Quien no es dueño ni miembro activo recibe 404 con `NOT_FOUND_DETAIL`; un rol que no alcanza, 403 con solo `detail`. Así lo hacen las vistas de `back/apps/users/views_workspaces.py`.

Detalle y tabla de lo que ya tiene endpoint: [01, Workspace activo y roles](../ia/01-creacion-de-proyecto.md#workspace-activo-y-roles). Tests de referencia: `back/apps/videos/tests/test_active_workspace.py`.

### Roles

- `WorkspaceRolePermission` y `ROLE_MATRIX` (`back/apps/core/permissions.py`) copian `MATRIX` de `front/src/lib/permissions.ts`, que es la tabla 1.7 del mapa. Si sumás una acción, sumala en los dos.
- Cada vista mapea sus pedidos a acciones con `workspace_actions`: acciones del viewset (`create`, `approve_paper_edit`…) o métodos HTTP en un `APIView`.
  - Un método seguro sin mapear es una lectura (`project.view`, todos los roles).
  - **Una escritura sin mapear queda para Dueño y Admin** (`members.manage`).
  - Un método que la vista no tiene sigue de largo: DRF responde 405, no 403.
- Las acciones de tus áreas: `members.manage`, `workspace.rename`, `workspace.delete`, `workspace.transfer`, `billing.manage`, `wallet.view`, `social.manage` y `post.publish`.
- **Un rol que no alcanza recibe 403 `{ "detail": "…" }` con una frase en español** de `DENIED` (o `EDITOR_DELETE_DENIED`). No lleva `code`: la UI la muestra tal cual y nunca la confunde con un límite del plan.

### Endpoints públicos

- El default de DRF es `IsAuthenticated`. Un endpoint público necesita `permission_classes = [AllowAny]` y `authentication_classes = []`, como `PublicPlanListView`: así un token vencido no lo rompe con un 401.
- Si el contrato dice `auth: 'optional'` (aceptar invitación), dejá la autenticación JWT y poné `AllowAny`: hay que reconocer al usuario con sesión.

### Plata

- **Reservas** (`back/apps/payments/services/wallet_service.py`): `reserve_funds` y después una de tres:
  - `commit_reservation`: se cobra entera;
  - `commit_partial_reservation`: se cobra una parte y el resto vuelve al saldo;
  - `rollback_reservation`: se devuelve toda (queda `SPEND` + `FAILED`).
- Reservá dentro de `transaction.atomic`, junto con el cambio de estado que la justifica. El modelo es `approve_paper_edit` en `back/apps/videos/views.py`: toma, reserva y aprobación en una sola transacción.
- **No le pases `project_id` a `reserve_funds`:** hoy dispara `process_video_pipeline.delay`. Ninguna llamada de hoy se lo pasa.
- **Precio:** `PricingEngine.calculate_render_cost` (`back/apps/payments/services/pricing_engine.py`). La fórmula está en [01, Precio](../ia/01-creacion-de-proyecto.md#precio).
- **Saldo que no alcanza:** `InsufficientFundsError` → **402** `{ "error", "required" }`. El frontend siempre abre el diálogo de saldo insuficiente.
- **Límites del plan:** `PlanLimitExceededError` y `FeatureNotAllowedError`. Hoy salen como **403** al crear un proyecto y como **400** («Error en el motor de precios: …») al aprobar la revisión. El frontend reconoce los dos (`planLimitKind`, `approveErrorKind`). En lo nuevo, preferí 403 con `code` (`duration`, `resolution`, `seo`, `scheduling`, `crossposting`, `seats_exceeded`; ver `PLAN_LIMIT_CODES` en `front/src/api/errors.ts`).
- **El render se cobra por la parte entregada:** `finalize_project_render` cobra los segundos de los clips `completed` o `published` con `commit_partial_reservation` y devuelve el resto. Sin nada entregado, devuelve todo. Lo que quedó `RESERVED` lo liquida `settle_stale_reservations` con la misma regla.
- Borrar un proyecto no devuelve lo cobrado (mapa §9, decisión 6).
- **Acreditar tokens (PAY-02):** todavía no hay función para eso en `wallet_service`. La que hagas bloquea el `Wallet` con `select_for_update`, como `reserve_funds`, y es idempotente por el id de la sesión de pago.
- Para leer: [01, ciclo de vida de la reserva](../ia/01-creacion-de-proyecto.md#flujo-ciclo-de-vida-de-la-reserva), [02, aprobación](../ia/02-pipeline-de-ingesta.md#aprobación-de-la-revisión-approve-paper-edit) y [07, reservas que quedaron RESERVED](../ia/07-capacidad-costos-y-operacion.md#operación-reservas-de-tokens-que-quedaron-reserved).

### `Idempotency-Key` en publicar

- El cliente la manda en `social.createPost` y `social.createPostsBulk`: una clave por envío, repetida si reintenta (`usePostActions` en `front/src/api/hooks/social.ts`).
- `CORS_ALLOW_HEADERS` (`back/backend/settings/base.py`) ya la permite, junto con `x-workspace-id`.
- **Regla:** el backend guarda la respuesta exitosa (2xx) por clave y workspace, al menos 24 h, y la devuelve tal cual sin crear ni publicar de nuevo. **Una respuesta de error no se guarda**: reintentar con la misma clave después de corregir el problema vuelve a intentarlo.
- El guardado todavía no existe en el backend: lo construís con esos dos endpoints. El mock hace exactamente eso (`front/src/api/mocks/http.ts`), sirve de referencia.

### Errores

- Validación de DRF: `{ "campo": ["mensaje"] }`. Vistas propias: `{ "error": "mensaje", "required"?: … }`. Permisos y 404: `{ "detail": "mensaje" }`.
- **Tus mensajes van en español rioplatense**, como los de `DENIED`.
- El backend corre con `LANGUAGE_CODE = 'en-us'`: los mensajes propios de DRF llegan en inglés. El frontend traduce los conocidos y cae a un texto genérico en español con `errorMessage`, `fieldError` y `translateFieldError` (`front/src/lib/error-messages.ts`). Un texto en inglés que no conoce nunca se muestra.
- Si un error necesita que la UI haga algo distinto, sumá `code` (el contrato ya propone varios).

### Booleanos en multipart

- DRF lee como `false` un booleano que un formulario multipart no manda.
- `_NotSentBooleanField` y `_NOT_SENT_BOOLEANS` (`back/apps/videos/serializers.py`) lo arreglan: «no enviado» toma el default del modelo al crear y deja lo que había al editar.
- En un `ModelSerializer` que recibe multipart, poné `serializer_field_mapping = _NOT_SENT_BOOLEANS`, como `BrandKitSerializer` y `VideoProjectSerializer`. Test: `back/apps/videos/tests/test_multipart_booleans.py`.

### Tests

- pytest, con `DJANGO_SETTINGS_MODULE = backend.settings.ci` (`back/pytest.ini`, `testpaths = apps`).
- `backend.settings.ci` exige PostgreSQL local en `DATABASE_URL`: no corre con SQLite.
- La preparación completa está en [`CONTRIBUTING.md`, «Reproducir CI localmente»](../../CONTRIBUTING.md#reproducir-ci-localmente). Con el entorno listo:

  ```bash
  # PostgreSQL efímero, con las credenciales de CI
  docker run --rm -d --name onecreator-ci-postgres \
    -e POSTGRES_DB=onecreator_ci -e POSTGRES_USER=postgres \
    -e POSTGRES_PASSWORD=ci-password -p 5432:5432 postgres:15-alpine

  cd back
  export DJANGO_SETTINGS_MODULE=backend.settings.ci
  export DATABASE_URL=postgres://postgres:ci-password@127.0.0.1:5432/onecreator_ci
  python manage.py makemigrations --check --dry-run
  python -m pytest                     # toda la suite
  python -m pytest apps/users -k invit # una parte
  ```

- **Cada cambio trae un test que falla antes del cambio.** Escribilo primero, miralo fallar en `dev` y después implementá.
- El CI corta la red externa: Ayrshare, Stripe y los proveedores de IA se mockean.
- Modelos: `test_active_workspace.py` (workspace y roles), `back/apps/payments/tests/test_public_plans.py` (endpoint público y cache), `test_log_privacy.py` (logs).

### Logs

- **Solo ids, nunca texto del usuario:** ni títulos, ni emails, ni captions, ni prompts, ni el mensaje de una excepción.
- El patrón: `logger.error("render.approval_failed project_id=%s error=%s", project.pk, type(exc).__name__)`. El mensaje arranca con `dominio.evento`: `JsonFormatter` (`back/apps/core/structured_logging.py`) lo usa como `event`.
- No copies `logger.error(f"Error calculando costo V3: {e}")` de `VideoProjectViewSet.create`: es de antes y loguea el texto de la excepción.
- En invitaciones y registro el email es dato del usuario: no va al log.

### Esquema OpenAPI

- Usá `extend_schema` como en `PublicPlanListView`. `WorkspaceAutoSchema` (`back/apps/core/schema.py`) suma `X-Workspace-Id` a las vistas con `WorkspaceRolePermission`.

---

## 3. Endpoints que faltan, por área

### Cómo leer las tablas

- **Hook · mock:** el hook está en `front/src/api/hooks/<dominio>.ts`. El mock es `route(contract.<export>)` en `front/src/api/mocks/handlers/<dominio>.ts`; la columna da el nombre del export.
- **Pantalla:** código del [mapa de pantallas](../producto/mapa-de-pantallas.md) y su sección. D1 a D5 son pantallas (§5 D); D6 a D9 y los marcados «diálogo» son diálogos (§6).
- **Talla:** **S** hasta un día. **M** dos o tres días. **L** más, o con un modelo nuevo o una integración externa.
- Todas las rutas empiezan con `/api/v1/`, salvo las de token.

### Resumen

| Área | Propuestos | En parte | Quién |
|---|---|---|---|
| Acceso | 6 | — | Pedro (propuesta) |
| Usuario y perfil | 5 | — | Lead, en curso |
| Workspaces, miembros e invitaciones | 13 | — | Pedro (propuesta) |
| Planes, billetera y pagos | 12 (uno ya está en `dev`) | — | Pedro (tickets PAY) |
| Proyectos | 5 | 2 | Lead: propuestos en curso; los parciales, sin rama |
| Clips | 6 | — | Lead, en curso |
| Brand kits | 1 | 1 | Sin dueño |
| Redes y publicaciones | 11 | — | Pedro (propuesta) |
| Notificaciones | 3 | — | Pedro (propuesta) |
| Sistema | 1 | — | Lead, en curso |
| **Total** | **63** | **3** | |

### Acceso (6) · Pedro

Contrato `contracts/auth.ts`. Backend: app `apps/users` (modelo `User`). Al registrarse, la señal `create_default_workspace` (`back/apps/users/signals.py`) ya crea el workspace con el usuario como Admin activo, y `create_workspace_wallet` (`back/apps/payments/signals.py`) su billetera.

| Método y ruta | Id | Hook · mock | Pantalla | Backend | Depende de | Talla |
|---|---|---|---|---|---|---|
| POST `auth/register/` | `auth.register` | `useRegister` · `register` | A2, A5 (§5 A) | `User`; token de verificación; límite por IP | Emails (hueco 21), cache compartido, plan Gratis por defecto | M |
| POST `auth/verify-email/` | `auth.verifyEmail` | `useVerifyEmail` · `verifyEmail` | A3 (§5 A) | Existe en `feat/perfil-me` (`VerifyEmailView`). El registro solo tiene que mandar el link con `make_token(user, SIGNUP)` (`apps/users/email_verification.py`): ese token verifica y devuelve el par JWT | `auth.register` | S |
| POST `auth/verify-email/resend/` | `auth.resendVerification` | `useResendVerification` · `resendVerification` | A3 (§5 A) | 204 siempre; 429 antes de 60 s | Cache compartido (throttle), emails | S |
| POST `auth/password-reset/` | `auth.requestPasswordReset` | `useRequestPasswordReset` · `requestPasswordReset` | A4 (§5 A), D9 (§6) | 204 siempre; link `/recuperar?token=…&email=…` | Emails (N2, §9.2) | S |
| POST `auth/password-reset/confirm/` | `auth.confirmPasswordReset` | `useConfirmPasswordReset` · `confirmPasswordReset` | A4 (§5 A) | 204; `validate_password` | `auth.requestPasswordReset` | S |
| POST `auth/logout/` | `auth.logout` | `useLogout` · `logout` | I1 (§5 I), Barra (§1.1) | `token_blacklist` ya está instalada en `feat/perfil-me`; `all_devices` es `revoke_refresh_tokens(user)` (`apps/users/tokens.py`) | — | S |

Además, hueco 1: `auth.login` existe pero no distingue la cuenta sin verificar. Falta el 403 con `code: "email_not_verified"`: un serializer propio para `TokenObtainPairView` en `back/backend/urls.py`. Talla S.

### Usuario y perfil (5) y Sistema (1) · lead, en curso

Salen de tus dos commits (759f9f8 y f04db85), que el lead pasó con cherry-pick a la rama `feat/perfil-me` y está terminando con arreglos. **No los toques:** si ves algo, avisale.

| Método y ruta | Id | Hook · mock | Pantalla | Estado |
|---|---|---|---|---|
| GET `me/` | `users.me` | `useMe` · `getMe` | Barra (§1.1), B1, B2, J3, I1 | En curso (lead) |
| PATCH `me/` | `users.updateMe` | `useUpdateMe` · `updateMe` | B1, B2, J3, I1 | En curso (lead) |
| POST `me/password/` | `users.changePassword` | `useChangePassword` · `changePassword` | I1 (§5 I) | En curso (lead) |
| GET `me/notification-preferences/` | `users.notificationPreferences` | `useNotificationPreferences` · `getNotificationPreferences` | I2 (§5 I) | En curso (lead) |
| PATCH `me/notification-preferences/` | `users.updateNotificationPreferences` | `useUpdateNotificationPreferences` · `updateNotificationPreferences` | I2 (§5 I) | En curso (lead) |
| GET `status/` | `system.status` | `useSystemStatus` · `getSystemStatus` | K5 (§5 K) | En curso (lead) |

`feat/perfil-me` crea `back/apps/users/urls.py` con rutas y lo incluye en `back/backend/urls.py`. Tus workspaces van en esos mismos archivos: arrancá tu rama desde `feat/perfil-me`, o esperá a que se mergee.

### Workspaces, miembros e invitaciones (13) · Pedro

Contrato `contracts/workspaces.ts`. Backend: app `apps/users`, modelos `Workspace` y `WorkspaceMember`. Todos son `workspaceScoped: false`: el workspace viene en la ruta (ver la sección 2, «Ojo con `/workspaces/:id/…`»).

Los nueve de workspaces y miembros (hasta `workspaces.removeMember`) ya existen en `back/apps/users/views_workspaces.py`, con sus pruebas en `back/apps/users/tests/test_workspaces_api.py`. Faltan los cuatro de invitaciones.

- Crear usa `create_owned_workspace` (`back/apps/users/workspaces.py`), lo mismo que el registro. Tope de 10 workspaces propios (409 `workspace_limit`) y límite de intentos por usuario (`THROTTLE_WORKSPACE_CREATE`, scope `workspace_create`).
- Borrar responde 409 `{error, code}` y no borra nada en estos casos, en este orden: `project_processing` (proyecto subido, en ingesta o renderizando; uno trabado se borra primero), `posts_in_flight` (publicación `QUEUED` o `PROCESSING`), `reservation_open` (tokens reservados), `balance_left` (queda saldo disponible: los tokens nunca se borran) y `last_workspace` (es el único workspace del usuario).
- Si se borra, se van en cascada proyectos, clips, publicaciones (las `SCHEDULED` no se publican), brand kits, miembros y la billetera en cero con sus movimientos. El log `workspace.deleted` guarda las cifras finales de la billetera.
- Pendiente: el borrado no limpia los archivos en R2 ni el perfil de Ayrshare.
- Transferir cambia primero el dueño. Si el dueño anterior no tenía fila de miembro, la fila nueva pasa por el control de lugares del plan: sin lugar responde 400 y no transfiere.
- Miembros: una fila por `WorkspaceMember`; `invited_by` es siempre null hasta que exista el modelo de invitación.

| Método y ruta | Id | Hook · mock | Pantalla | Backend | Depende de | Talla |
|---|---|---|---|---|---|---|
| GET `workspaces/` | `workspaces.list` | `useWorkspaces` · `listWorkspaces` | J3 (§5 J) | `Workspace` + `role_in` | — | S |
| POST `workspaces/` | `workspaces.create` | `useCreateWorkspace` · `createWorkspace` | J3 (§5 J), B1 (§5 B) | `Workspace` con el usuario como dueño, plan Gratis; la billetera la crea `create_workspace_wallet` | Plan Gratis por defecto | S |
| GET `workspaces/:id/` | `workspaces.detail` | `useWorkspace` · `getWorkspace` | H1 (§5 H) | `Workspace`; `role` y `admins` propuestos | — | S |
| PATCH `workspaces/:id/` | `workspaces.update` | `useUpdateWorkspace` · `updateWorkspace` | H1 (§5 H), B1 | `workspace.rename` | — | S |
| DELETE `workspaces/:id/` | `workspaces.delete` | `useDeleteWorkspace` · `deleteWorkspace` | H1 (§5 H) | `workspace.delete`; `confirm_name` viaja en el cuerpo del DELETE; revisar qué cascadea (proyectos, archivos, billetera) | — | M |
| POST `workspaces/:id/transfer/` | `workspaces.transfer` | `useTransferWorkspace` · `transferWorkspace` | H1 (§5 H) | `workspace.transfer`; el dueño anterior queda Admin, en una transacción | `workspaces.members` | M |
| GET `workspaces/:id/members/` | `workspaces.members` | `useMembers` · `listMembers` | H2 (§5 H), D7 (§6), H1 | `WorkspaceMember` + dueño + invitaciones; `max_members` y `seats_used` | Modelo de invitación | S |
| PATCH `workspaces/:id/members/:memberId/` | `workspaces.updateMember` | `useUpdateMember` · `updateMember` | H2 (§5 H) | `members.manage`; al Dueño no se le cambia el rol (400) | `workspaces.members` | S |
| DELETE `workspaces/:id/members/:memberId/` | `workspaces.removeMember` | `useRemoveMember` · `removeMember` | H2 (§5 H) | Quita un miembro o cancela una invitación; al Dueño no (400) | `workspaces.members` | S |
| POST `workspaces/:id/invitations/` | `workspaces.invite` | `useInviteMembers` · `inviteMembers` | D7 (§6), H2 | **Modelo nuevo** (ver abajo); lugares del plan (403 `seats_exceeded`); email | Emails (N3, §9.2), plan por defecto | L |
| POST `workspaces/:id/invitations/:memberId/resend/` | `workspaces.resendInvitation` | `useResendInvitation` · `resendInvitation` | H2 (§5 H) | Reenvía el email; 404 si ya se aceptó o se canceló | `workspaces.invite` | S |
| GET `invitations/:token/` | `workspaces.invitation` | `useInvitation` · `getInvitation` | A5 (§5 A) | Público; vencida o usada responden 200 con `status` | `workspaces.invite` | S |
| POST `invitations/:token/accept/` | `workspaces.acceptInvitation` | `useAcceptInvitation` · `acceptInvitation` | A5 (§5 A) | `auth: 'optional'`; crea la cuenta si no hay, o usa la sesión; 401 si el email ya tiene cuenta y no hay sesión; 410 vencida o usada | `workspaces.invite`, `auth.register` | L |

Lo que tenés que decidir para invitar:

- `WorkspaceMember.user` es obligatorio. Una invitación a un email sin cuenta no entra en el modelo de hoy.
- Opciones: un modelo nuevo de invitación (email, rol, token, `invited_by`, vencimiento) o sumar esos campos a `WorkspaceMember` con `user` nulo. El contrato ya espera `Member.user` nulo y `email` en la lista, y usa el id de la fila pendiente como `:memberId`.
- `check_workspace_member_limit` (`back/apps/users/signals.py`) lanza un `ValidationError` de Django en `pre_save`. Si llega a la vista sin atrapar es un 500: traducilo al 403 `seats_exceeded`.
- Una invitación pendiente ocupa un lugar (mapa §9.1, punto 8). El conteo de hoy (`workspace.members.count()`) ya incluye las pendientes, y también la fila del dueño cuando la tiene (el workspace del registro la crea).

### Planes, billetera y pagos (12) · Pedro, tickets PAY

Contrato `contracts/billing.ts`. Backend: app `apps/payments` (`SubscriptionPlan`, `Wallet`, `Transaction`, `PricingEngine`, `wallet_service`). Los seguimientos del PR #130 los está haciendo el lead en `fix/planes-gratis-por-defecto`: el seed, `is_public`, la invalidación del cache y los validadores.

| Método y ruta | Id | Hook · mock | Pantalla | Backend | Depende de | Talla |
|---|---|---|---|---|---|---|
| GET `plans/` | `billing.plans` | `usePlans` · `listPlans` | A6, G3, C1, D3 diálogo | **Ya existe** en `dev`: `PublicPlanListView` (PR #130). Falta pasarlo a `exists` en el contrato | `fix/planes-gratis-por-defecto` | S |
| GET `pricing/` | `billing.pricing` | `usePricing` · `getPricing` | C1, A6, G3, G4, D2, D1 diálogo | Público; constantes de `PricingEngine` y settings (`AI_UPLOAD_MAX_DURATION_SECONDS`…) | — | S |
| POST `pricing/estimate/` | `billing.estimate` | `useEstimate` · `estimateCost` | C1, D2, D4, D5 | Envuelve `PricingEngine.calculate_render_cost` con el plan y la billetera; `eta_seconds` puede ir `null` (hueco 18) | `billing.wallet` | S |
| GET `subscription/` | `billing.subscription` | `useSubscription` · `getSubscription` | Barra, G1, G3, C1, E1, H2 | Hoy solo hay `Workspace.subscription_plan`; renovación y cambio pendiente piden modelo nuevo | — | M |
| POST `subscription/change/` | `billing.changePlan` | `useChangePlan` · `changePlan` | G3 (§5 G) | Subir ya o al volver del checkout; bajar al fin del período; 409 `seats_exceeded` | `billing.subscription`, `billing.createPurchase` | L |
| GET `wallet/` | `billing.wallet` | `useWallet` · `getWallet` | Barra, G1, C1, D2, G4, D1 y D2 diálogo | `Wallet`; el detalle de lo reservado por proyecto (hueco 19) pide el vínculo con el proyecto | Vínculo `Transaction` ↔ proyecto | M |
| GET `wallet/usage/` | `billing.usage` | `useUsage` · `getUsage` | G1 (§5 G) | Suma por día en `America/Argentina/Buenos_Aires`, separada en proyectos, renders y SEO | `kind` en `Transaction` | M |
| GET `transactions/` | `billing.transactions` | `useTransactions` · `listTransactions` | G2 (§5 G), D1 Actividad (§9.2) | `Transaction` + proyecto + `kind` (migración); paginado | Coordinar con el lead: las reservas se escriben en `apps/videos` | L |
| GET `transactions/export/` | `billing.exportTransactions` | `useExportTransactions` · `exportTransactions` | G2 (§5 G) | CSV con signo | `billing.transactions` | S |
| GET `token-packages/` | `billing.tokenPackages` | `useTokenPackages` · `listTokenPackages` | G4 (§5 G), D2 diálogo | Tres paquetes fijos y la cantidad libre: modelo nuevo o settings | — | S |
| POST `token-purchases/` | `billing.createPurchase` | `useCreateTokenPurchase` · `createTokenPurchase` | G4 (§5 G), D2 diálogo | PAY-02: checkout de Stripe (`stripe_service.py` está vacío); modelo nuevo de compra; webhook con firma, idempotente | `billing.tokenPackages`, función de acreditar | L |
| GET `token-purchases/:id/` | `billing.purchase` | `useTokenPurchase` · `getTokenPurchase` | G5 (§9.2) | Estado del pago; la pantalla consulta cada 5 s | `billing.createPurchase` y el webhook | S |

El webhook del proveedor no está en el contrato porque el navegador no lo llama. Va igual, con su test de doble envío (PAY-02).

### Proyectos (5 propuestos y 2 parciales) y Clips (6) · lead

Contratos `contracts/projects.ts` y `contracts/clips.ts`. Backend: `apps/videos`. Los propuestos los está haciendo el lead en `feat/clips-reintento-render` y `feat/proyecto-actividad-descargas`. No te metas en `VideoProjectViewSet` ni en las tareas de render sin hablar con él.

| Método y ruta | Id | Hook · mock | Pantalla | Backend | Depende de | Talla |
|---|---|---|---|---|---|---|
| POST `projects/probe-link/` | `projects.probeLink` | `useProbeLink` · `probeLink` | C1 (§5 C) | Acción de `VideoProjectViewSet`. Valida la duración del video con la regla de la descarga: más de 3 h, el máximo de la app, es 400 `duration_app` con `limit` y con cualquier plan (ningún plan lo aceptaría); dentro de las 3 h y más que el plan, 403 `duration` con `limit` | — | M |
| POST `projects/:id/redispatch/` | `projects.redispatch` | `useRedispatchProject` · `redispatchProject` | D1 (§5 D) | `ProjectRedispatchView`, solo Dueño y Admin. Cobra: si lo que falta se devolvió al fallar (una etapa antes de elegir los clips, o el render de un video completo), reserva de nuevo el mismo monto y responde `cost_details`; 402 sin saldo. Un proyecto con los clips ya elegidos se reanuda sin reservar (su render se paga solo). 409 con `code` si no se puede reanudar; `duration_app` si falló con `LinkTooLongForAppError` (su link pasa las 3 h: se crea otro) | — | S |
| POST `projects/:id/rerender/` | `projects.rerender` | `useRerenderProject` · `rerenderProject` | D1, D4, D5 | Reserva y `render_clip_task` por clip | — | M |
| GET `projects/:id/activity/` | `projects.activity` | `useProjectActivity` · `getProjectActivity` | D1 Actividad (§9.2) | Quién hizo qué y los movimientos de tokens | Vínculo `Transaction` ↔ proyecto | M |
| POST `projects/:id/clips/download/` | `projects.downloadAll` | `useDownloadAllClips` · `downloadAllClips` | D3 (§5 D) | .zip y URL temporal (hueco 17) | `clips.download` | M |
| GET `clips/:id/` | `clips.detail` | `useClip` · `getClip` | D4 (§5 D), E1 | `VideoClip`, `VideoClipSerializer` | — | S |
| PATCH `clips/:id/` | `clips.update` | `useUpdateClip` · `updateClip` | D4, D3, D5 | 409 mientras renderiza, como `ProjectRenderingError` | — | S |
| DELETE `clips/:id/` | `clips.delete` | `useDeleteClip` · `deleteClip` | D3 (§5 D) | Cancela sus `ScheduledPost` | — | S |
| POST `clips/:id/render/` | `clips.render` | `useRenderClip` · `renderClip` | D4, D5 | Reserva por la duración del clip | — | M |
| POST `clips/:id/retry/` | `clips.retry` | `useRetryClip` · `retryClip` | D3 (§5 D) | `ClipRetryView`. Cobra el render del clip (su parte se devolvió cuando falló): reserva como `clips.render` y responde el clip más `final_cost` y `transaction_id`; 402 sin saldo, 409 si no está en error o el proyecto ya renderiza. La UI confirma el costo antes | — | M |
| GET `clips/:id/download/` | `clips.download` | `useClipDownload` · `getClipDownload` | D3, D4, D5 | `CloudflareR2Manager.generate_presigned_url` con `ResponseContentDisposition=attachment` | — | S |
| GET `projects/` ⚠️ | `projects.list` | `useProjects` · `listProjects` | B2 (§5 B), J1, B1 | Paginación de 25, `status_group`, `search`, `status_counts`, serializer liviano (hueco 14) | — | M |
| GET `projects/:id/` ⚠️ | `projects.detail` | `useProject` · `getProject` | D1, D3, D4, D5 | Faltan `cost`, `stage_timings`, `eta_seconds`, `duration_seconds`, `uploaded_by_detail`, `thumbnail_url` (huecos 4, 15, 18) | Vínculo `Transaction` ↔ proyecto | M |

⚠️ = existe en parte. Los dos parciales son del área del lead, pero no tienen rama: coordinalo con él antes de tocarlos.

### Brand kits (1 propuesto y 1 parcial) · sin dueño

| Método y ruta | Id | Hook · mock | Pantalla | Backend | Depende de | Talla |
|---|---|---|---|---|---|---|
| PATCH `brand-kits/:id/` ⚠️ | `brandKits.update` | `useUpdateBrandKit`, `useSetDefaultBrandKit` · `updateBrandKit` | F1, F2 (§5 F) | `BrandKitViewSet.partial_update`: al marcar `is_default`, desmarcar el resto en una transacción | — | S |
| POST `brand-kits/:id/duplicate/` | `brandKits.duplicate` | `useDuplicateBrandKit` · `duplicateBrandKit` | F1 (§5 F) | Acción nueva de `BrandKitViewSet`; copia los archivos; la copia nunca es default | — | M |

### Redes y publicaciones (11) · Pedro

Contrato `contracts/social.ts`. Backend: `ScheduledPost` en `apps/videos/models.py`, tareas en `apps/videos/tasks/social.py`, señales en `apps/videos/signals.py`, cliente de Ayrshare en `apps/integrations/ayrshare_api.py`. Poné las vistas en un módulo propio dentro de `apps/videos` (por ejemplo `views_social.py`): las ramas del lead tocan `apps/videos/views.py`.

| Método y ruta | Id | Hook · mock | Pantalla | Backend | Depende de | Talla |
|---|---|---|---|---|---|---|
| GET `social-accounts/` | `social.accounts` | `useSocialAccounts` · `listSocialAccounts` | H3 (§5 H), E1, B2 | Una entrada por red; hoy solo existe `Workspace.ayrshare_profile_key`; modelo nuevo o datos de Ayrshare | Decisión de ADR-005 | M |
| POST `social-accounts/connect/` | `social.connect` | `useConnectSocialAccount` · `connectSocialAccount` | D6 (§6), H3, E1 | `social.manage`; perfil de Ayrshare por workspace y su URL para autorizar | `social.accounts`, ADR-005 | L |
| DELETE `social-accounts/:platform/` | `social.disconnect` | `useDisconnectSocialAccount` · `disconnectSocialAccount` | H3 (§5 H) | Las programadas en esa red quedan en error | `social.connect` | M |
| GET `scheduled-posts/` | `social.posts` | `usePosts` · `listPosts` | E2 (§5 E), D3, D4 diálogo | Paginado; filtros `status`, `platform`, `from`, `to`, `project`; filtra por `video_clip__project__workspace` | Campos nuevos de `ScheduledPost` | M |
| POST `scheduled-posts/` | `social.createPost` | `useCreatePost`, `usePostActions` · `createPost` | E1 (§5 E) | `post.publish`; migración con los campos nuevos (`caption`, `hashtags`, `seo_status`, `post_url`, `error_message`, `error_code`, `tiktok`); `Idempotency-Key`; `allow_scheduling` y `allow_crossposting`; borrador con `draft` | `social.accounts`, beat (hueco 9), `clips.download` | L |
| POST `scheduled-posts/bulk/` | `social.createPostsBulk` | `useCreatePostsBulk`, `usePostActions` · `createPostsBulk` | E1 varios (§9.2) | Hasta 60, todo o nada, misma regla de `Idempotency-Key` | `social.createPost` | M |
| GET `scheduled-posts/:id/` | `social.post` | `usePost` · `getPost` | E2, E1 | Mismo serializer que la lista | `social.posts` | S |
| PATCH `scheduled-posts/:id/` | `social.updatePost` | `useUpdatePost` · `updatePost` | E2 (§5 E) | Texto, reprogramar o publicar un borrador; solo antes de `QUEUED` | `social.createPost` | M |
| POST `scheduled-posts/:id/cancel/` | `social.cancelPost` | `useCancelPost` · `cancelPost` | E2 (§5 E) | Borra la que todavía no salió | `social.createPost` | S |
| POST `scheduled-posts/:id/retry/` | `social.retryPost` | `useRetryPost` · `retryPost` | E2 (§5 E) | Re-encola `upload_to_social_network`; con `AyrshareOutcomeUnknownError` no se sabe si salió: no publicar dos veces | `social.createPost` | M |
| POST `scheduled-posts/:id/regenerate-seo/` | `social.regeneratePostSeo` | `useRegeneratePostSeo` · `regeneratePostSeo` | E1 (§5 E) | `queue_post_seo(post_id, regenerate=True)`, como la acción del admin | `social.createPost` | S |

Para el texto con IA y la publicación, leé [05, SEO y publicación](../ia/05-seo-y-publicacion.md). El contrato propone tres reglas nuevas en la nota de `social.createPost` (esperar el texto de la IA, el borrador que falla sigue en `DRAFT`, y borrar borradores viejos).

### Notificaciones (3) · Pedro

Contrato `contracts/notifications.ts`. Backend: modelo nuevo `Notification` (usuario, workspace, tipo, título, cuerpo, `read_at`, proyecto, publicación). Puede ir en `apps/users`, al lado de `NotificationPreference` (llega con `feat/perfil-me`), o en una app nueva.

| Método y ruta | Id | Hook · mock | Pantalla | Backend | Depende de | Talla |
|---|---|---|---|---|---|---|
| GET `notifications/` | `notifications.list` | `useNotifications` · `listNotifications` | J2 (§5 J), Barra | Últimas 20 del usuario en el workspace y `unread_count` | Modelo nuevo | S |
| POST `notifications/:id/read/` | `notifications.markRead` | `useMarkNotificationRead` · `markNotificationRead` | J2 (§5 J) | Marca `read_at` | `notifications.list` | S |
| POST `notifications/read-all/` | `notifications.markAllRead` | `useMarkAllNotificationsRead` · `markAllNotificationsRead` | J2 (§5 J) | Todas las del usuario en el workspace | `notifications.list` | S |

Lo grande no son los tres endpoints: es **crear** las notificaciones en los cinco casos del mapa §1.11 (listo para revisar, render terminado, fallo con devolución, publicación fallida, saldo bajo). Esos eventos salen de las tareas de `apps/videos`, que son del área del lead. Proponé un servicio chico (una función que crea la notificación y, si las preferencias lo permiten, manda el email) y acordá con él dónde se llama. Talla M a L.

---

## 4. Orden sugerido para el piloto

El criterio: qué necesita cada flujo de punta a punta para andar con el backend real. Cada paso deja algo usable.

**0. Lo que ya está en curso (lead).** Te destraba lo de abajo:
- cache compartido en Redis (`fix/creacion-robusta`);
- plan Gratis por defecto y Estudio con tope de 3 h (`fix/planes-gratis-por-defecto`);
- `/me`, `/status`, perfil, contraseña y preferencias (`feat/perfil-me`);
- modo híbrido (`feat/front-modo-mixto`, PR #134): construido, ver la sección 1.

**1. Acceso y `/me`.** Sin sesión y sin `/me` no anda ni la barra.
1. `auth.logout` (S): `token_blacklist` ya está en `feat/perfil-me`.
2. `auth.requestPasswordReset` y `auth.confirmPasswordReset` (S).
3. `auth.register`, `auth.verifyEmail`, `auth.resendVerification` y el 403 de `auth.login`. Necesitan el envío de emails (hueco 21) y el cache compartido para los límites.

**2. Workspaces, miembros e invitaciones.** El equipo es lo que vuelve real al piloto.
1. `workspaces.list`, `workspaces.detail`, `workspaces.update` (H1).
2. `workspaces.members`, `workspaces.updateMember`, `workspaces.removeMember` (H2).
3. Modelo de invitación, `workspaces.invite`, `workspaces.resendInvitation`, `workspaces.invitation` y **`workspaces.acceptInvitation`**. Aceptar es el hueco 13 y hoy no tiene endpoint: una invitación pasa a `active` solo desde el admin de Django.
4. `workspaces.create`, `workspaces.transfer`, `workspaces.delete`.

**3. Planes y pagos (checkout).** Sin cobrar no hay piloto pago.
1. `billing.plans`: pasarlo a `exists` cuando se mergee `fix/planes-gratis-por-defecto`.
2. `billing.pricing`, `billing.estimate`, `billing.wallet`.
3. `Transaction` con proyecto y `kind`, después `billing.transactions`, `billing.exportTransactions` y `billing.usage`. Acordalo con el lead: también lo necesitan `projects.activity` y `projects.detail`.
4. `billing.tokenPackages`, `billing.createPurchase`, el webhook y `billing.purchase` (PAY-02).
5. `billing.subscription` y `billing.changePlan`: dependen del checkout.

**4. Redes y publicaciones.**
1. Cerrar ADR-005 (Ayrshare u OAuth directo).
2. `social.accounts`, `social.connect`, `social.disconnect`.
3. El beat de `dispatch_scheduled_posts_batch` (hueco 9).
4. `social.createPost` con `Idempotency-Key`, `social.posts`, `social.post`, `social.updatePost`, `social.cancelPost`, `social.retryPost`, `social.regeneratePostSeo` y `social.createPostsBulk`.
   - Necesitan clips renderizados con link de descarga (`clips.download`, del lead).

**5. Notificaciones.** Modelo, los tres endpoints y la creación desde los eventos del pipeline. Los emails necesitan las preferencias (`feat/perfil-me`) y las plantillas (hueco 21).

---

## 5. Quién hace qué

Para no pisarnos. Lo «en curso» ya tiene rama: no lo arranques por tu lado.

| Área | Endpoints | Quién | Rama | Estado |
|---|---|---|---|---|
| Acceso (lo que falta) | 6 + el 403 de `auth.login` | Pedro | — | Propuesta |
| Workspaces y miembros | 9 | — | `feat/workspaces-y-miembros` | Hechos: existen en el backend |
| Invitaciones | 4 | Pedro | — | Propuesta |
| Planes, billetera y pagos (PAY-xx) | 12 | Pedro | — | Tuyos. PAY-03 ya está mergeado (PR #130) |
| Seguimientos del PR #130: seed, `is_public`, invalidación del cache, validadores, plan Gratis por defecto | — | Lead | `fix/planes-gratis-por-defecto` | En curso (lead) |
| Redes y publicaciones | 11 | Pedro | — | Propuesta |
| Notificaciones | 3 | Pedro | — | Propuesta |
| Usuario y perfil, `/status` | 6 | Lead (desde tus commits 759f9f8 y f04db85) | `feat/perfil-me` | En curso (lead) |
| Proyectos y clips | 11 propuestos | Lead (Franco) | `feat/clips-reintento-render`, `feat/proyecto-actividad-descargas` | En curso (lead) |
| Proyectos: los 2 parciales | 2 | Área del lead | — | Sin rama: coordinar |
| Brand kits: duplicar y default único | 2 | — | — | Sin dueño |
| Cache compartido en Redis | — | Lead | `fix/creacion-robusta` | En curso (lead) |
| Modo híbrido | — | Lead | `feat/front-modo-mixto` | Construido (PR #134) |

Tu propuesta suma 45 endpoints (44 sin contar `billing.plans`, que ya está). Es una propuesta: si preferís otro reparto, se habla.

El backlog (`documents/backlog_mvp.md`) asigna por par, no por persona: PAY-01 a PAY-03, DIST-01 a DIST-04 y FE-01 a FE-03 son del 🚀 Par Producto.

---

## 6. Reglas decididas, huecos y bloqueos

### Decidido por el dueño del producto

- **Un workspace nuevo arranca en el plan Gratis.** Se configura con un setting y, para los workspaces que ya existen sin plan, con el comando `assign_default_plan`. Lo implementa el lead en `fix/planes-gratis-por-defecto`.
  - Hasta que se mergee, en `dev` la señal `create_default_workspace` busca un plan `Starter` que no existe, así que `subscription_plan` queda en `null`.
  - Sin plan, `PricingEngine` no aplica límites ni descuento, y `check_workspace_member_limit` deja un solo miembro: no se puede invitar a nadie.
- **Estudio tiene un tope de 3 h**, igual que la subida (`AI_UPLOAD_MAX_DURATION_SECONDS`, 10800 s). En `dev`, el seed de `payments/0005` todavía le da 14400 s (4 h): lo corrige el seguimiento del seed en `fix/planes-gratis-por-defecto`.

### Huecos y bloqueos

| Qué | Bloquea | Hueco (mapa §8) |
|---|---|---|
| **El cache no es compartido.** `base`, `dev` y `prod` no definen `CACHES`, así que cada proceso tiene su propio cache en memoria. Los límites de intentos, un guardado de `Idempotency-Key` en cache y la invalidación del cache de planes no cruzan procesos. Lo arregla `fix/creacion-robusta` (lead). | `auth.register`, `auth.resendVerification`, el 429 de `auth.login`, `social.createPost` | — |
| **No existe el flujo de aceptar una invitación.** No hay endpoints y `WorkspaceMember` no puede guardar una invitación a un email sin cuenta. | Toda la fila de invitaciones, A5 | 13 |
| **No hay emails transaccionales.** Ni plantillas ni envío. Las plantillas están dibujadas (N1 a N8, §9.2). | Registro, verificación, recuperar, invitación, notificaciones | 21 |
| Resuelto en `feat/perfil-me`: `token_blacklist` instalada, y límites de intentos en el login (por IP y por cuenta, solo los fallidos), el refresh, la contraseña y el cambio de email (`apps/users/throttles.py`). El registro y el reenvío necesitan los suyos. | `auth.register`, `auth.resendVerification` | 1, 2 |
| [ADR-005](../adrs/005-social-publishing-strategy.md) sigue `proposed`: el contrato asume Ayrshare, DIST-01 propone OAuth directo. | `social.accounts`, `social.connect`, `social.disconnect` | 10 |
| No hay beat para `dispatch_scheduled_posts_batch`: no está en `CELERY_BEAT_SCHEDULE` y `docker-compose.yml` no tiene servicio `beat`. | Publicaciones programadas | 9 |
| `Transaction` no tiene vínculo con el proyecto ni `kind`; un `SPEND` devuelto queda `FAILED`. | `billing.wallet` (detalle), `billing.transactions`, `billing.usage`, `projects.activity`, `projects.detail` | 4, 19 |
| Stripe: `stripe_service.py` está vacío y no hay función para acreditar tokens. | `billing.createPurchase`, `billing.purchase`, `billing.changePlan` | 12 |
| `LANGUAGE_CODE = 'en-us'`: los mensajes propios de DRF llegan en inglés (el frontend los traduce). | Nada, pero tus mensajes van en español igual | — |

---

## 7. Checklist: «el área está conectada»

Un área está conectada cuando se cumple todo esto:

- [ ] **Cada endpoint del área está en `status: 'exists'`**, con `source` citando la vista (`back/apps/<app>/views.py · <Vista>`). La `note` se borra o dice lo que sigue pendiente.
- [ ] **`CONTRACT.md` está regenerado:** `cd front && npm run contract:doc`, y `src/api/CONTRACT.md` y `ROUTES.md` van en el mismo PR.
- [ ] **La respuesta real parsea con el esquema zod.** Tres formas de verlo:
  1. Con la app contra el backend real, en la consola no aparece ningún `ContractError` («Contrato roto en `<id>` (response): …»). El cliente valida cada respuesta (`front/src/api/client.ts`).
  2. A mano, con una respuesta guardada:
     ```bash
     curl -s http://localhost:8000/api/v1/plans/ > /tmp/respuesta.json
     cd front
     ID=billing.plans FILE=/tmp/respuesta.json npx tsx -e "
     import { readFileSync } from 'node:fs';
     import { ALL_ENDPOINTS } from './src/api/contracts';
     const endpoint = ALL_ENDPOINTS.find((item) => item.id === process.env.ID);
     if (!endpoint) throw new Error('No existe el id ' + process.env.ID);
     const result = endpoint.response.safeParse(JSON.parse(readFileSync(process.env.FILE, 'utf8')));
     console.log(result.success ? 'OK' : result.error.issues.map((issue) => issue.path.join('.') + ': ' + issue.message).join('\n'));
     "
     ```
     Para un endpoint con sesión, sacá el token con `POST /api/token/` (`email` y `password`) y mandá `Authorization: Bearer <access>`, más `X-Workspace-Id` si el endpoint es del workspace.
  3. Fijalo en un test: sumá el cuerpo real a `front/src/api/contracts/drf/responses.json` y un caso en `drf.test.ts`. Si mañana cambia un serializer, el test dice qué esquema dejó de aceptarlo.
- [ ] **Pasan los tests del frontend:** `npm run lint`, `npm run typecheck`, `npm test`, `npm run build` y `npm run test:e2e`. Y el smoke del modo híbrido contra tu backend local: `E2E_HYBRID_API_URL=… E2E_HYBRID_EMAIL=… E2E_HYBRID_PASSWORD=… npm run test:e2e -- --project=hibrido` (detalle en `front/README.md`).
- [ ] **Pasa el backend:** `python -m pytest` y `python manage.py makemigrations --check --dry-run`, con al menos un test por endpoint que fallaba antes del cambio.
- [ ] **Lo probaste a mano con los mocks apagados.** `NEXT_PUBLIC_API_MODE=real` (o el anterior `NEXT_PUBLIC_API_MOCKS=false`), o `hybrid` con el dominio en `NEXT_PUBLIC_API_REAL`, en `front/.env.development.local`.
  - Recorré cada pantalla del área con los cuatro roles (Dueño, Admin, Editor, Lector).
  - Confirmá el 403 en español, el 404 con un objeto de otro workspace y los estados de error del mapa (§4).
- [ ] **El mock sigue diciendo la verdad.** Si el backend real respondió distinto que el mock, se corrigió primero el contrato y después el mock.
