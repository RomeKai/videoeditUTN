# OneCreator · frontend

App web de OneCreator: Next.js 15 (App Router), React 19 y TypeScript estricto. Todas las pantallas funcionan contra una API simulada en el navegador, y el contrato con el backend está definido en un solo lugar, así que conectar el Django real es apagar los mocks.

- **Qué es cada pantalla:** [`documents/producto/mapa-de-pantallas.md`](../documents/producto/mapa-de-pantallas.md).
- **Qué ruta tiene cada pantalla y quién la construye:** [`ROUTES.md`](ROUTES.md).
- **Contrato de la API (para el equipo de backend):** [`src/api/CONTRACT.md`](src/api/CONTRACT.md).
- **Sistema visual:** [`documents/producto/diseno/`](../documents/producto/diseno/README.md), y todos los componentes en vivo en `/dev/kit`.

## Cómo correrlo

Hace falta Node 22.

```bash
cd front
npm ci
npm run dev          # http://localhost:3000
```

### Con datos de prueba (por defecto)

`npm run dev` arranca con `NEXT_PUBLIC_API_MOCKS=true` (ver `.env.development`): [MSW](https://mswjs.io) responde toda la API desde el navegador y no hace falta levantar nada más.

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
NEXT_PUBLIC_API_MOCKS=false
NEXT_PUBLIC_API_URL=http://localhost:8000
```

Levantá el backend como dice [`back/README.md`](../back/README.md) y reiniciá `npm run dev`. Hoy el backend tiene 17 de los 79 endpoints del contrato (login, proyectos, revisión, vista previa de clip y brand kits); el resto son los huecos de la sección 8 del mapa. `src/api/CONTRACT.md` dice cuál es cuál.

## Comandos

| Comando | Qué hace |
|---|---|
| `npm run dev` | Servidor de desarrollo |
| `npm run build` / `npm run start` | Build de producción y su servidor |
| `npm run lint` | ESLint, sin warnings |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Vitest: componentes, cliente HTTP, mocks y contrato |
| `npm run test:e2e` | Playwright, sobre dos builds propios. Con mocks (`.next-e2e`): recorre todas las rutas, recarga y falla si hay errores de consola, pedidos fallidos o pedidos que se le escapan a MSW. Sin mocks (`.next-e2e-real`, la API se finge en el navegador): la sesión al recargar y al entrar por un link directo. `E2E_PORT` cambia el puerto (usa ese y el siguiente) |
| `npm run contract:doc` | Regenera `src/api/CONTRACT.md` y `ROUTES.md` |

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
    ├── shell/            Barra, sesión, permisos, protección de rutas, páginas de sistema
    ├── dialogs/          Diálogos compartidos (costo, saldo, plan, borrar, sesión…)
    ├── lib/              Formatos, permisos, precios, estados del proyecto, mensajes de error
    ├── features/         Código de cada pantalla, por dominio
    ├── dev/              Registro de pantallas, cartel de pantalla pendiente, panel de mocks
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
5. Cubrí los estados del mapa: cargando con `Skeleton` (nunca un spinner de pantalla completa), vacío con `EmptyState`, error con `<LoadError>` de `@/shell/LoadError`.
6. Lo que el rol no permite se deshabilita, no se oculta: `<Button {...gate('project.create')}>`. Lo que el plan no incluye se muestra con `<PlanLock plan="Creador" />`.
7. Los textos van en español rioplatense, tal como están en el mapa y en los artboards. Montos, fechas y duraciones salen de `@/lib/format` o de `<Tokens>`, `<DateTime>` y `<Duration>`.
8. Agregá tests de la pantalla y fijate que el smoke (`npm run test:e2e`) siga pasando: visita todas las rutas del registro.

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
- **Sesión:** el par JWT vive en `localStorage` (`oc.auth`). Ante un 401 el cliente renueva una vez y repite; si no puede, se abre «Tu sesión venció» sin perder la pantalla.
- **Workspace activo:** se manda en el header `X-Workspace-Id` (propuesto; hoy el backend usa el primer workspace del usuario).
- **Roles:** la tabla 1.7 del mapa está en `src/lib/permissions.ts`. La API todavía no la aplica; los mocks sí.
- **Estimado de costo:** `estimateCost()` en `src/lib/pricing.ts` usa la misma fórmula que `PricingEngine`, con los valores de `GET /pricing/`.
- **Sin red externa:** fuentes (Geist) e íconos van en el bundle.
