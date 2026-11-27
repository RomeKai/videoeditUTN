# OneCreator · frontend

App web de OneCreator: Next.js 15 (App Router), React 19 y TypeScript estricto. Todas las pantallas funcionan contra una API simulada en el navegador, y el contrato con el backend está definido en un solo lugar, así que conectar el Django real es apagar los mocks.

- **Qué es cada pantalla:** [`documents/producto/mapa-de-pantallas.md`](../documents/producto/mapa-de-pantallas.md).
- **Qué ruta tiene cada pantalla y quién la construye:** [`ROUTES.md`](ROUTES.md).
- **Contrato de la API (para el equipo de backend):** [`src/api/CONTRACT.md`](src/api/CONTRACT.md).
- **Cómo conectar el backend real, área por área (reglas, orden y quién hace qué):** [`documents/integracion/guia-conexion.md`](../documents/integracion/guia-conexion.md).
- **Sistema visual:** [`documents/producto/diseno/`](../documents/producto/diseno/README.md), y todos los componentes en vivo en `/dev/kit`.

## Cómo correrlo

Hace falta Node 22.

```bash
cd front
npm ci
npm run dev          # http://localhost:3000
```

### Con datos de prueba (por defecto)

`npm run dev` arranca en modo `mock` (`NEXT_PUBLIC_API_MOCKS=true` en `.env.development`, que equivale a `NEXT_PUBLIC_API_MODE=mock`): [MSW](https://mswjs.io) responde toda la API desde el navegador y no hace falta levantar nada más.

- Entrás directo como Lucía, dueña de «Estudio Norte» (plan Pro). Para iniciar sesión a mano sirve cualquier contraseña. Tres contraseñas disparan los errores del login: `incorrecta` (401, «El email o la contraseña no coinciden»), `limite` (429, demasiados intentos) y `caida` (500, el servidor falló).
- El botón **Mocks** (el ícono del matraz, sobre el borde izquierdo; se despliega al pasarle por encima) cambia de **escenario**: usuario nuevo, workspace vacío, saldo bajo, cada plan, cada rol, sesión vencida, servidor caído, mantenimiento y más. La lista completa está en `src/api/mocks/scenarios.ts`.
- Los datos se guardan en el navegador (`localStorage`), así que sobreviven a una recarga. «Reiniciar datos» los vuelve a sembrar.
- Lo que creás avanza solo: un proyecto pasa por las etapas del pipeline cada pocos segundos, se puede aprobar, renderizar, publicar y cobrar.

Para llegar a los estados de error sin videos reales, el nombre del archivo (o el título) dispara cada caso:

| En el nombre del archivo o el título | Qué pasa |
|---|---|
| `no-video`, `ilegible`, `sin-pista`, `sin-duracion`, `muy-largo`, `muy-grande`, `muy-chico` | El 400 de subida con ese error |
| `moderacion`, `sin-voz`, `sin-clips`, `timeout`, `lento`, `falla` | El proyecto falla en el pipeline con ese `pipeline_error_code` y se devuelve la reserva |
| `demanda` | Queda «En espera» unos segundos antes de seguir |
| `parcial` | Un clip falla en el render: «Listo con errores» |
| `NNmin` (por ejemplo `clase-45min.mp4`) | Define la duración del video; sin eso se calcula por el peso del archivo |

Otros disparadores: un link con `privado` no se puede bajar; un texto de publicación con `falla` hace fallar la publicación; los tokens de invitación son `valida`, `con-cuenta`, `vencida` y `usada` (`/invitacion/valida`); el token `invalido` falla al verificar el email y al recuperar la contraseña.

### Contra el backend real

```bash
# front/.env.local
NEXT_PUBLIC_API_MODE=real
NEXT_PUBLIC_API_URL=http://localhost:8000
```

(`NEXT_PUBLIC_API_MOCKS=false` sigue funcionando y significa lo mismo. Si están las dos variables, manda `NEXT_PUBLIC_API_MODE`.)

Levantá el backend como dice [`back/README.md`](../back/README.md) y reiniciá `npm run dev`. Hoy el backend tiene 14 de los 80 endpoints del contrato, y 3 más en parte (login, proyectos, revisión, vista previa de clip y brand kits). Los otros 63 son propuestos para los huecos de la sección 8 del mapa. `src/api/CONTRACT.md` dice cuál es cuál, y [`documents/integracion/guia-conexion.md`](../documents/integracion/guia-conexion.md) cómo conectarlos.

### Modo híbrido: una parte real y el resto simulado

Sirve para conectar la app al backend de a un dominio, sin romper lo que todavía no existe ahí. MSW sigue corriendo: deja pasar al backend los pedidos de los endpoints «reales» y responde él los demás.

```bash
# front/.env.development.local
NEXT_PUBLIC_API_MODE=hybrid
NEXT_PUBLIC_API_URL=http://localhost:8000
# Opcional. Qué va al backend real, separado por comas:
NEXT_PUBLIC_API_REAL=
```

Va en **`.env.development.local`**, no en `.env.local`: ese último también lo lee `next build`, y un build de producción quedaría híbrido (con MSW, el panel, `/dev` y el puente de identidad). Por las dudas, `next build` **se niega** a armar un build que no sea `real` salvo que se le pida con `API_MOCKS_IN_BUILD=true` (lo usan los builds de Playwright).

El modo y la lista quedan fijos **al construir** (`env` en `next.config.ts`), en el servidor y en el navegador por igual: cambiar esas variables al arrancar `next start` no cambia nada. Después de tocarlas hay que reiniciar `npm run dev` o volver a construir.

| `NEXT_PUBLIC_API_MODE` | Quién responde |
|---|---|
| `mock` | MSW, todo (lo mismo que `NEXT_PUBLIC_API_MOCKS=true`) |
| `real` | El backend, todo; MSW no arranca (lo mismo que `NEXT_PUBLIC_API_MOCKS=false`) |
| `hybrid` | El backend lo que diga `NEXT_PUBLIC_API_REAL`; MSW el resto |

`NEXT_PUBLIC_API_REAL` acepta, en cualquier combinación:

- **nada (vacío):** va al backend todo endpoint que el contrato marca `exists` o `partial`. Marcar un endpoint como `exists` en `src/api/contracts/` alcanza para que deje de simularse; no hay otra lista que tocar.
- **dominios del contrato** (`users`, `workspaces`, `projects`, `clips`, `brandkits`, `billing`, `social`, `notifications`, `system`): el dominio va **entero** al backend, también lo que el contrato marca `proposed` (responderá 404 hasta que exista).
- **ids de endpoint** (`billing.plans`, `users.me`): solo ese endpoint.
- **`existing`:** lo mismo que la lista vacía, para sumarle algo: `NEXT_PUBLIC_API_REAL=existing,users.me`.

Con una lista, lo que no está en ella se simula. Un nombre que no es un dominio ni un endpoint del contrato (un error de tipeo) se ignora, con un aviso en la consola y en el panel. `auth` va siempre al backend en híbrido y no hace falta nombrarlo: el backend no acepta los tokens simulados, así que la sesión tiene que ser real. Por eso en híbrido se entra con un usuario del backend (el registro y la recuperación de contraseña todavía no existen ahí y responden 404).

El ruteo sale del registro del contrato (`src/api/routing.ts`); cada respuesta de un mock lleva la cabecera `X-OC-Mock: <id del endpoint>`, así que en la pestaña de red se ve quién contestó.

**Panel de desarrollo.** El botón del matraz dice «Híbrido» y muestra cada dominio como Real, Mock o Mixto, con un interruptor por dominio (también en `/dev/kit`). El cambio se guarda en el `localStorage` de ese navegador (`oc.api.routes`), le gana al env y se aplica al recargar; «Quitar el cambio» borra el de un dominio y «Volver a lo que dice el env», todos. Los escenarios que solo tienen sentido con todo simulado (los de sesión, rol y datos sembrados, y Mantenimiento) aparecen deshabilitados con el motivo; siguen sirviendo los de plan, saldo, pagos y «Servidor caído», que actúan sobre lo simulado.

**Subidas reales.** Un pedido que va al backend igual pasa por el service worker de MSW, que lee el cuerpo entero antes de soltarlo (no hay forma de saltearlo desde la página). Para una subida real en híbrido eso significa: la barra de progreso salta al 100 % cuando el worker termina de leer el archivo, no cuando llega al backend; el tiempo de espera de la respuesta (180 s al crear un proyecto) empieza ahí y cubre también la subida real; y el archivo queda dos veces en memoria. Para probar subidas grandes o el progreso, usá el modo `real`.

**Si el backend no responde,** un pedido real falla como cualquier error de red («No pudimos conectar» con Reintentar); lo simulado sigue andando. El backend tiene que aceptar el origen del front por CORS: `backend.settings.dev` acepta cualquiera.

#### Identidad: quién es el usuario y cuál es el workspace

Los mocks y el backend no pueden discrepar sobre eso. Hay dos casos:

- **`/me` real** (cuando exista en el backend: `NEXT_PUBLIC_API_REAL=existing,users.me`, o solo marcarlo `exists`). La app usa los workspaces reales y manda su id en `X-Workspace-Id`. Un dominio simulado que recibe un workspace que no conoce lo **adopta**: le crea una billetera con el saldo y el plan del escenario, las tres redes sin conectar y al usuario como miembro.
- **`/me` simulado** (hoy). Es un **puente de desarrollo**, marcado como tal en el código (`src/api/mocks/hybrid.ts`, `BACKEND_DEFAULT_WORKSPACE_PREFIX` en `src/api/config.ts`): el mock lee el `user_id` del JWT real (sin poder verificar la firma), usa el email con el que se inició sesión y muestra un único workspace, «Workspace por defecto (backend)». Su id es un marcador: para ese id el cliente **no manda** `X-Workspace-Id` y el backend usa el workspace por defecto del usuario (`back/apps/core/workspace.py`). Con este puente solo se ve ese workspace: no hay forma de cambiar a otro hasta que `/me` sea real, y «Crear workspace» queda deshabilitado con el motivo (el nuevo existiría solo en los mocks y el backend respondería 404 a su id). El puente solo existe en modo híbrido: en `mock` y en `real` un id marcador que haya quedado en el navegador se descarta al arrancar, y también en híbrido en cuanto `/me` pasa a ser real.

Lo mismo pasa con proyectos y clips: si un endpoint simulado recibe el id de uno que solo existe en el backend (su actividad, publicarlo), crea un **doble** con ese id («Proyecto del backend», «Clip del backend»). El doble no copia nada del real. Los ids de lo adoptado (billetera, membresía, proyecto de un clip) se derivan del id real, así que varios pedidos simultáneos adoptan lo mismo.

#### Qué combinaciones son coherentes

| Combinación | ¿Coherente? | Detalle |
|---|---|---|
| Lecturas reales de proyectos y brand kits + todo lo demás simulado (el valor por defecto) | Sí, para ver y navegar | Las listas, el detalle, la revisión y los brand kits son los del backend. |
| Subir un archivo a un endpoint real | Funciona, con límites | Ver «Subidas reales»: el progreso y el tiempo de espera no son los verdaderos. |
| Crear un proyecto real + billetera simulada | **No** | El backend reserva y cobra en su billetera; la simulada no se entera. El saldo, los movimientos y la estimación de costo que muestra la app no reflejan los cobros reales, y el backend puede responder 402 con un saldo simulado que alcanza. |
| Planes reales (`billing.plans`) + suscripción simulada | **No** | La suscripción simulada apunta a un plan sembrado, con otro id: «tu plan» no coincide con ninguno de la lista real. Los límites que aplica el backend son los del plan real. |
| Proyecto real + endpoints simulados de ese proyecto o sus clips (actividad, re-render, editar, renderizar o borrar un clip) | **No**, solo no se rompe | Trabajan sobre el doble: lo que se cambia ahí no llega al proyecto real ni se ve en su detalle. |
| Clip real + publicación simulada | A medias | Se puede recorrer el flujo de publicar, pero no se publica nada y el clip real no muestra sus publicaciones. |
| Workspaces o miembros simulados + todo lo demás | **No** para roles | Los mocks no conocen el rol real: tratan al usuario como admin. El backend aplica el rol verdadero en lo que responde, así que la app puede ofrecer acciones que el backend rechaza con 403. |
| `/me` simulado + `workspaces` real | **No** | El workspace que muestra `/me` es el marcador, que el backend no conoce: los endpoints `/workspaces/:id/…` responden 404. Pasá `users` (o `users.me`) a real antes que `workspaces`. |
| Notificaciones simuladas | Sí, pero vacías | No hay eventos reales que las generen. |
| Dos usuarios reales en el mismo navegador | Sí | Cada uno tiene su usuario y su workspace marcador en la base simulada. |
| Cambiar de modo en el mismo navegador | Sí | Al arrancar, la sesión del otro modo se descarta (un token simulado no sirve en el backend y uno real no sirve en los mocks). |

Regla práctica: es coherente **leer** de los dos lados; deja de serlo cuando una **escritura** real tiene efectos en un dominio simulado (o al revés). Pasá a real el dominio entero donde cae el efecto antes de confiar en lo que muestra.

#### Probar el modo híbrido contra un backend

`e2e/hibrido.hybrid.spec.ts` corre solo si se le dice dónde hay un backend; sin eso aparece como salteado.

```bash
E2E_HYBRID_API_URL=http://127.0.0.1:8000 \
E2E_HYBRID_EMAIL=alguien@ejemplo.com E2E_HYBRID_PASSWORD=… \
npm run test:e2e -- --project=hibrido
```

Arma un tercer build (`.next-e2e-hybrid`) y lo sirve en `E2E_PORT + 2` (o en `E2E_HYBRID_PORT`). El usuario tiene que existir en el backend y ser dueño de un workspace con al menos un proyecto y un brand kit (sin datos, el smoke falla en vez de pasar sin comprobar nada). Comprueba que el login, la lista de proyectos y los brand kits salgan del backend y la billetera del mock; crea y borra un brand kit; cambia un dominio desde el panel; y corta el backend para ver el estado de error. `E2E_HYBRID_REAL` pasa una lista a `NEXT_PUBLIC_API_REAL` (el smoke asume la lista vacía).

## Comandos

| Comando | Qué hace |
|---|---|
| `npm run dev` | Servidor de desarrollo |
| `npm run build` / `npm run start` | Build de producción y su servidor |
| `npm run lint` | ESLint, sin warnings |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest: componentes, cliente HTTP, mocks y contrato |
| `npm run test:e2e` | Playwright, sobre dos builds propios (tres con `E2E_HYBRID_API_URL`: ver «Modo híbrido»). Con mocks (`.next-e2e`): recorre todas las rutas, recarga y falla si hay errores de consola, pedidos fallidos o respuestas de la API que no dio un mock (cada una lleva `X-OC-Mock`). Sin mocks (`.next-e2e-real`, la API se finge en el navegador): la sesión al recargar y al entrar por un link directo, y que el modo del build no cambie con las variables del entorno al arrancar. `E2E_PORT` cambia el puerto (usa ese y el siguiente) |
| `npm run contract:doc` | Regenera `src/api/CONTRACT.md` y `ROUTES.md` |
| `npm run msw:init` | Regenera `public/mockServiceWorker.js` y le vuelve a aplicar el bypass de media (ver «Decisiones») |

Antes de cada push tienen que pasar `lint`, `typecheck`, `test`, `build` y `test:e2e`. El job `Frontend` del CI corre lo mismo.

## Carpetas

```
front/
├── e2e/                  Smoke de Playwright
├── scripts/              Generador de CONTRACT.md y ROUTES.md
└── src/
    ├── app/              Rutas (App Router)
    │   ├── (public)/     Acceso y Precios: sin sesión
    │   ├── (onboarding)/ Bienvenida: con sesión, sin barra
    │   ├── (app)/        Todo lo demás: sesión + barra superior
    │   └── dev/          Kit de componentes y dobles de terceros para los mocks
    ├── api/
    │   ├── contracts/    El contrato: un módulo por dominio, con esquemas zod
    │   ├── hooks/        Hooks de TanStack Query, uno por endpoint
    │   ├── mocks/        Handlers de MSW, datos sembrados, escenarios y simulación
    │   ├── client.ts     El único fetch: token, renovación, validación con zod
    │   └── CONTRACT.md   Generado
    ├── ui/               Sistema visual: un componente por archivo, con su CSS Module
    ├── shell/            Barra, sesión, permisos, protección de rutas, guarda de salida, páginas de sistema
    ├── dialogs/          Diálogos compartidos (costo, saldo, plan, borrar, sesión…)
    ├── lib/              Formatos, permisos, precios, estados del proyecto, mensajes de error
    ├── features/         Código de cada pantalla, por dominio
    ├── dev/              Registro de pantallas, cartel de pantalla pendiente, panel de mocks
    ├── testing/          Utilidades de los tests de pantallas (providers, API simulada, escenarios)
    └── styles/           Tokens de los dos temas y estilos base
```

## Cómo agregar una pantalla

1. Si es nueva en el producto, agregala primero al mapa de pantallas y después a `src/dev/screens.ts` (código, ruta, artboards y dominio). Corré `npm run contract:doc` para actualizar `ROUTES.md`.
2. Creá `src/app/(app)/<ruta>/page.tsx` (o en `(public)`). Si la ruta ya existe, tiene un cartel de «Pantalla pendiente»: reemplazalo.
3. Poné el código de la pantalla en `src/features/<dominio>/` y dejá el `page.tsx` fino.
4. Usá solo lo compartido:
   - **Componentes:** `import { Button, Card, Field, … } from '@/ui'`. Mirá `/dev/kit` antes de crear uno.
   - **Datos:** hooks de `@/api/hooks`. Las pantallas nunca llaman a `fetch` (ESLint lo impide).
   - **Sesión, rol y plan:** `useSession()`, `usePermissions()` y `usePlan()` de `@/shell/session`.
   - **Diálogos:** `@/dialogs`. Para un 402 o un 403 de precios: `useBillingDialogs().handleError(error)`.
   - **Avisos:** `useToast()`.
   - **Cambios sin guardar:** `useLeaveGuard(dirty)` de `@/shell/leaveGuard` y `<UnsavedChangesDialog {...guard.dialog} />`. La guarda del shell cubre los links, Atrás, cerrar la pestaña y lo que navega desde la barra; la pantalla no intercepta nada por su cuenta. Para salir después de guardar, `guard.leaveNow(href)`.
5. Cubrí los estados del mapa: cargando con `Skeleton` (nunca un spinner de pantalla completa), vacío con `EmptyState`, error con `<LoadError>` de `@/shell/LoadError`.
6. Lo que el rol no permite se deshabilita, no se oculta: `<Button {...gate('project.create')}>`. Lo que el plan no incluye se muestra con `<PlanLock plan="Creador" />`.
7. Los textos van en español rioplatense, tal como están en el mapa y en los artboards. Montos, fechas y duraciones salen de `@/lib/format` o de `<Tokens>`, `<DateTime>` y `<Duration>`.
8. Agregá tests de la pantalla (`renderScreen` y compañía están en `@/testing`) y fijate que el smoke (`npm run test:e2e`) siga pasando: visita todas las rutas del registro.

## Cómo agregar un endpoint al contrato

1. **Contrato:** en `src/api/contracts/<dominio>.ts` definí los esquemas zod y el endpoint con `defineEndpoint({ … })`, y sumalo a la lista `…Endpoints` del módulo.
   - Si existe en el backend, copiá los nombres de los campos del serializer y citá la vista en `backend.source` con `status: 'exists'` (o `'partial'` con una nota de lo que falta).
   - Si no existe, `status: 'proposed'` y el número de hueco del mapa en `gap`.
2. **Hook:** en `src/api/hooks/<dominio>.ts`, con `apiCall(endpoint, …)`. Las lecturas van con `useQuery` y una clave de `keys` (`src/api/query.ts`); las escrituras con `useMutation`, invalidando lo que cambian. Lo que está en curso se consulta cada 5 s con `pollWhile()`.
3. **Mock:** en `src/api/mocks/handlers/<dominio>.ts`, con `route(endpoint, resolver)`. La respuesta se valida contra el esquema, así que un mock que se aparta del contrato falla. Si hacen falta datos nuevos, sumalos a `seed.ts`.
4. **Documento:** `npm run contract:doc`.
5. **Tests:** `npm test` controla que cada endpoint tenga su handler, que los datos sembrados cumplan los esquemas y que `CONTRACT.md` esté al día.

## Decisiones

- **Tema:** oscuro por defecto; la elección se guarda en `localStorage` (`oc.theme`) y se aplica antes de pintar.
- **Sesión:** el par JWT vive en `localStorage` (`oc.auth`). Ante un 401 el cliente renueva una vez y repite; si no puede, se abre «Tu sesión venció» sin perder la pantalla. Si el 401 trae `code: "session_revoked"` (se cerró la sesión en todos los dispositivos desde otro), el mismo diálogo dice «Cerraste la sesión en todos tus dispositivos».
  - *Lo que se resigna:* un token en `localStorage` lo puede leer cualquier script que corra en la página, así que un XSS se lleva la sesión. Se eligió igual porque el backend entrega el par en el cuerpo de `POST /api/token/` (no hay cookie `HttpOnly`) y la app es de otro origen que la API. La defensa es no dejar entrar scripts ajenos: nada de `dangerouslySetInnerHTML` con datos del usuario, sin scripts de terceros, y una Content-Security-Policy estricta en producción (`script-src 'self'`). El único script inline es el del tema (`src/lib/theme-script.ts`): la CSP tiene que permitirlo con su hash o un nonce. Si el backend pasa a cookies `HttpOnly`, cambia solo `src/api/session.ts` y `src/api/client.ts`.
  - Varias pestañas comparten la sesión: si una renueva o vuelve a iniciar sesión, las otras toman el par nuevo.
- **Layout raíz:** `src/app/layout.tsx` es un Server Component y no importa valores (constantes, funciones) de módulos `'use client'`, solo componentes. Un valor así llega al navegador como referencia que espera el JS de ese módulo; dentro de `<head>` eso rompía la hidratación de toda la página cuando el JS llegaba tarde (error #418 de React, intermitente bajo carga). Por eso el script del tema vive en `src/lib/theme-script.ts`.
- **Mocks y service worker:** con los mocks apagados la app da de baja el worker de MSW que haya quedado registrado en ese origen.
- **El worker no toca los videos:** `public/mockServiceWorker.js` lo genera `msw init` (y el `postinstall` de MSW en cada `npm install`), así que no se edita a mano. `scripts/patch-msw-worker.mjs` le agrega, después de cada instalación (`postinstall`) y de `npm run msw:init`, una salida temprana para los pedidos con `Range`, los de un `<video>` o `<audio>` y todo lo que está bajo `/media/`. Sin eso el worker rearmaba el pedido sin el header `Range` y el servidor mandaba el archivo entero en cada salto del video. Para regenerar el worker se usa `npm run msw:init`, nunca `msw init` solo. `scripts/msw-worker.test.ts` falla si el worker que se sirve no tiene el bypass, y `e2e/media-rango.spec.ts` lo comprueba en Chromium.
- **Después de tocar MSW** (subir su versión o `npm rebuild msw`), corré `npm run msw:init`: el `postinstall` de MSW vuelve a generar el worker sin el bypass de media, y `msw:init` se lo pone otra vez.
- **Workspace activo:** se manda en el header `X-Workspace-Id` en cada pedido con sesión, y el backend lo respeta (PR #125): las listas traen solo lo de ese workspace, y uno donde el usuario no es miembro activo responde 404. Sin el header, el backend usa el workspace más antiguo del usuario.
- **Roles:** la tabla 1.7 del mapa está en `src/lib/permissions.ts`, y el backend aplica la misma (`ROLE_MATRIX` en `back/apps/core/permissions.py`, PR #125). Un rol que no alcanza recibe 403 con un `detail` en español y sin `code`. Los mocks también la aplican; su 403 suma `code: "permission_denied"`, que el backend todavía no manda.
- **Estimado de costo:** `estimateCost()` en `src/lib/pricing.ts` usa la misma fórmula que `PricingEngine`, con los valores de `GET /pricing/`.
- **Sin red externa:** fuentes (Geist) e íconos van en el bundle.
