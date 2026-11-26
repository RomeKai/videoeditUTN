import { z, type ZodTypeAny } from 'zod';
import { CONTRACT } from '../src/api/contracts';
import * as auth from '../src/api/contracts/auth';
import * as billing from '../src/api/contracts/billing';
import * as brandKits from '../src/api/contracts/brandkits';
import * as clips from '../src/api/contracts/clips';
import * as common from '../src/api/contracts/common';
import * as notifications from '../src/api/contracts/notifications';
import * as projects from '../src/api/contracts/projects';
import * as social from '../src/api/contracts/social';
import * as system from '../src/api/contracts/system';
import * as users from '../src/api/contracts/users';
import * as workspaces from '../src/api/contracts/workspaces';
import type { AnyEndpoint } from '../src/api/endpoint';
import { ROUTELESS, SCREENS } from '../src/dev/screens';

/** Exported schemas by identity, so the tables can name them instead of spelling them out. */
const MODULES: Array<[string, Record<string, unknown>]> = [
  ['common', common],
  ['auth', auth],
  ['users', users],
  ['workspaces', workspaces],
  ['billing', billing],
  ['projects', projects],
  ['clips', clips],
  ['brandkits', brandKits],
  ['social', social],
  ['notifications', notifications],
  ['system', system],
];

const NAMES = new Map<ZodTypeAny, { name: string; module: string }>();
for (const [module, exports] of MODULES) {
  // Sorted: the order of a module namespace's keys depends on the loader.
  for (const [name, value] of Object.entries(exports).sort(([a], [b]) => a.localeCompare(b))) {
    if (value instanceof z.ZodType && !NAMES.has(value)) NAMES.set(value, { name, module });
  }
}

function cell(text: string): string {
  return text.replace(/\|/g, '\\|').replace(/\n/g, ' ');
}

/** Compact, readable description of a zod schema. Named schemas are referenced by name. */
export function describe(schema: ZodTypeAny, depth = 0, named = true): string {
  const known = named ? NAMES.get(schema) : undefined;
  if (known) return known.name;
  const def = schema._def as { typeName: string } & Record<string, unknown>;
  switch (def.typeName) {
    case 'ZodString':
      return 'string';
    case 'ZodNumber':
      return 'number';
    case 'ZodBoolean':
      return 'boolean';
    case 'ZodVoid':
      return '—';
    case 'ZodUnknown':
    case 'ZodAny':
      return 'unknown';
    case 'ZodLiteral':
      return JSON.stringify(def.value);
    case 'ZodEnum':
      return (def.values as string[]).map((value) => `"${value}"`).join(' \\| ');
    case 'ZodArray':
      return `${describe(def.type as ZodTypeAny, depth)}[]`;
    case 'ZodOptional':
      return describe(def.innerType as ZodTypeAny, depth);
    case 'ZodNullable':
      return `${describe(def.innerType as ZodTypeAny, depth)} \\| null`;
    case 'ZodDefault':
      return describe(def.innerType as ZodTypeAny, depth);
    case 'ZodEffects':
      return describe(def.schema as ZodTypeAny, depth);
    case 'ZodUnion':
      return (def.options as ZodTypeAny[]).map((option) => describe(option, depth)).join(' \\| ');
    case 'ZodIntersection':
      return `${describe(def.left as ZodTypeAny, depth)} & ${describe(def.right as ZodTypeAny, depth)}`;
    case 'ZodRecord':
      return `{ [clave]: ${describe(def.valueType as ZodTypeAny, depth)} }`;
    case 'ZodObject': {
      const shape = (schema as z.ZodObject<z.ZodRawShape>).shape;
      const keys = Object.keys(shape);
      if (keys.length === 0) return '{}';
      if (depth >= 2) return '{ … }';
      const fields = keys.map((key) => {
        const field = shape[key] as ZodTypeAny;
        return `${key}${field.isOptional() ? '?' : ''}: ${describe(field, depth + 1)}`;
      });
      return `{ ${fields.join(', ')} }`;
    }
    default:
      return def.typeName.replace(/^Zod/, '').toLowerCase();
  }
}

const STATUS_LABEL = { exists: '✅ existe', partial: '⚠️ existe en parte', proposed: '❌ falta (propuesto)' } as const;

function gaps(endpoint: AnyEndpoint): string {
  const gap = endpoint.backend.gap;
  if (gap === undefined) return '';
  return ` · hueco ${Array.isArray(gap) ? gap.join(', ') : gap}`;
}

function endpointSection(endpoint: AnyEndpoint): string {
  const lines: string[] = [];
  lines.push(`#### \`${endpoint.method} ${endpoint.path}\``);
  lines.push('');
  lines.push(`${endpoint.summary}`);
  lines.push('');
  lines.push('| | |');
  lines.push('|---|---|');
  lines.push(`| Id | \`${endpoint.id}\` |`);
  lines.push(`| Backend | ${STATUS_LABEL[endpoint.backend.status]}${gaps(endpoint)}${endpoint.backend.source ? ` · \`${cell(endpoint.backend.source)}\`` : ''} |`);
  if (endpoint.backend.note) lines.push(`| Nota | ${cell(endpoint.backend.note)} |`);
  lines.push(`| Sesión | ${endpoint.auth === false ? 'Pública' : endpoint.auth === 'optional' ? 'Pública (manda el token si hay sesión)' : 'JWT'}${endpoint.workspaceScoped === false ? '' : ' · `X-Workspace-Id`'}${endpoint.idempotencyKey ? ' · `Idempotency-Key` (una por envío; se repite al reintentar)' : ''} |`);
  if (endpoint.query) lines.push(`| Query | ${cell(describe(endpoint.query))} |`);
  if (endpoint.request) lines.push(`| Pedido${endpoint.bodyType === 'multipart' ? ' (multipart)' : ''} | ${cell(describe(endpoint.request))} |`);
  const responseType = endpoint.responseType === 'text' ? 'texto (CSV)' : cell(describe(endpoint.response));
  lines.push(`| Respuesta ${endpoint.successStatus ?? 200} | ${responseType} |`);
  if (endpoint.errors.length > 0) {
    lines.push(`| Errores | ${endpoint.errors.map((error) => `**${error.status}** ${cell(error.when)}`).join('<br>')} |`);
  }
  lines.push(`| Pantallas | ${endpoint.screens.join(', ')} |`);
  lines.push('');
  return lines.join('\n');
}

export function contractStats() {
  const all = CONTRACT.flatMap((group) => group.endpoints);
  return {
    total: all.length,
    exists: all.filter((endpoint) => endpoint.backend.status === 'exists').length,
    partial: all.filter((endpoint) => endpoint.backend.status === 'partial').length,
    proposed: all.filter((endpoint) => endpoint.backend.status === 'proposed').length,
  };
}

export function renderContract(): string {
  const stats = contractStats();
  const out: string[] = [];
  out.push('# Contrato de la API de OneCreator');
  out.push('');
  out.push('> Archivo generado desde `src/api/contracts/*.ts` con `npm run contract:doc`. No lo edites a mano: cambiá el contrato y volvé a generarlo. Un test falla si queda desactualizado.');
  out.push('');
  out.push('Este es el contrato de integración entre el frontend (`front/`) y el backend Django (`back/`). Cada endpoint está descripto una sola vez, con esquemas [zod](https://zod.dev); de ahí salen los tipos de TypeScript, los hooks (`src/api/hooks/`), los mocks (`src/api/mocks/`) y este documento.');
  out.push('');
  out.push(`**${stats.total} endpoints:** ${stats.exists} existen hoy en el backend, ${stats.partial} existen en parte y ${stats.proposed} son propuestos para los huecos de la [sección 8 del mapa de pantallas](../../../documents/producto/mapa-de-pantallas.md#8-huecos-de-backend).`);
  out.push('');
  out.push('## Cómo leerlo');
  out.push('');
  out.push('- **✅ existe:** los nombres de los campos están copiados del serializer o la vista de DRF que se cita.');
  out.push('- **⚠️ existe en parte:** el endpoint está, pero al frontend le falta algo (se dice qué en la nota).');
  out.push('- **❌ falta (propuesto):** no existe. La forma es una propuesta REST para el hueco indicado; si el backend la cambia, se cambia primero en `src/api/contracts/`.');
  out.push('- Un campo con `?` es opcional. Los tipos con nombre (`Project`, `Clip`…) están detallados en [Esquemas](#esquemas).');
  out.push('');
  out.push('## Convenciones');
  out.push('');
  out.push('- **Base:** `NEXT_PUBLIC_API_URL` (por defecto `http://localhost:8000`). Las rutas llevan la barra final de DRF.');
  out.push('- **Sesión:** `Authorization: Bearer <access>` con el JWT de `POST /api/token/`. Ante un 401 el cliente renueva una vez con `POST /api/token/refresh/` (el backend rota el refresh) y repite el pedido; si la renovación falla, abre el diálogo de sesión vencida. Cambiar la contraseña revoca todos los tokens emitidos antes (en todos los dispositivos) y responde un par nuevo, que el cliente guarda para seguir con sesión. El login, la renovación y los endpoints que piden `current_password` tienen límite de intentos: 429 con un `detail` en castellano que dice cuánto esperar.');
  out.push('- **Tiempo de espera:** un pedido sin respuesta completa en 30 s (`REQUEST_TIMEOUT_MS`) se corta y falla como error de red (`NetworkError`, con el mismo reintento y el mismo mensaje). Los que siguen trabajando en el servidor después de recibir el pedido esperan 180 s (`timeoutMs`): crear un proyecto (valida el video, lo mueve al storage, calcula el precio y reserva), guardar o duplicar un brand kit, armar el .zip y exportar los movimientos. En una subida multipart el reloj arranca cuando el archivo terminó de salir; mientras sube, se corta si pasa un minuto sin avanzar. Un pedido que cambia algo (todo lo que no es `GET`) y vence **puede haberse hecho**: `NetworkError.outcomeUnknown` lo marca, la UI dice «Puede que se haya hecho igual: revisá antes de probar de nuevo» en lugar de «No pudimos conectar», y vuelve a leer los datos.');
  out.push('- **Workspace activo:** el cliente manda `X-Workspace-Id` en cada pedido con sesión, y el backend lo respeta (PR #125): las listas traen solo lo de ese workspace, y un id mal formado, inexistente o de un workspace donde el usuario no es miembro **activo** (una invitación pendiente no cuenta) responde 404 «No encontramos ese espacio de trabajo.». Sin el header usa el workspace más antiguo del usuario. `GET /me` no lo lee: su `active_workspace_id` es el último workspace que la persona eligió (`PATCH /me`), mientras siga teniendo acceso; si no, el más antiguo. Al cambiar de workspace el cliente primero sale de la pantalla y recién después cambia el id, para que ninguna pida su dato en el workspace equivocado.');
  out.push('- **Roles:** el backend aplica la tabla 1.7 del mapa (PR #125). El Lector solo lee; el Editor crea y edita, y borra solo los proyectos que subió; redes, miembros, plan y nombre del workspace son del Dueño y los Admin. Un rol que no alcanza recibe 403 `{ detail }` con una frase en español («Con el rol Lector podés ver, pero no crear ni cambiar nada. Pedile acceso a un admin.»), sin `code`: la UI la muestra tal cual y nunca la confunde con un límite del plan (D3), que se reconoce por su `code` o por el texto de `PricingEngine`.');
  out.push('- **JSON en snake_case**, como el backend. Ids UUID. Fechas ISO 8601 en UTC.');
  out.push('- **Decimales:** un `DecimalField` de un serializer llega como string (`"4860.0000000000"`); un `Decimal` que la vista pone directo en la respuesta (`tokens_reserved`, `final_cost`, `required`) llega como número, porque el encoder de DRF lo pasa a float. El esquema `Decimal` acepta los dos y entrega un número.');
  out.push('- **Colores:** el backend los guarda como texto libre de hasta 7 caracteres. Los pedidos mandan siempre `#RRGGBB` (`HexColor`); las respuestas aceptan cualquier texto (`ColorValue`) y la UI los normaliza con `normalizeHex()`.');
  out.push('- **Booleanos en multipart:** DRF lee como `false` un booleano que no viene en un formulario multipart, sin mirar el default del modelo. En el backend eso ya no pasa con los booleanos del modelo: desde el PR #126, `VideoProjectSerializer` y `BrandKitSerializer` usan `_NOT_SENT_BOOLEANS`, que hace de cada uno un `_NotSentBooleanField` (el PR #121 lo había hecho solo para `add_subtitles`). «No enviado» es un caso propio: al crear toma el default del modelo y al editar no cambia lo que había. Al crear, un valor vacío (`""`) también cuenta como no enviado. `use_seo` no es columna: se lee solo al crear, y si no viene es `false`. `add_subtitles` tiene default `true`: en `POST /projects/` el cliente lo manda igual, con lo que muestra el formulario. En `from-prompt` lo deja afuera salvo que la persona lo haya elegido: un campo que viaja cuenta como explícito (`pte_explicit`) y le gana al texto, así que mandarlo siempre impediría que «sin subtítulos» en el prompt funcione.');
  out.push('- **Idioma:** el backend responde con `LANGUAGE_CODE = "en-us"`, así que los mensajes propios de DRF llegan en inglés. La UI no los muestra tal cual: `errorMessage()`, `fieldError()` y `translateFieldError()` (`src/lib/error-messages.ts`) traducen los conocidos y caen a un texto genérico en español.');
  out.push('- **Descargas:** los links firmados son de otro origen, y ahí el navegador ignora el atributo `download`. Para que el archivo se guarde en lugar de reproducirse, R2 tiene que firmar con `ResponseContentDisposition=attachment; filename="…"`. `downloadUrl()` abre esos links en una pestaña nueva para no reemplazar la app.');
  out.push('- **503:** cualquier respuesta 503 muestra la página de mantenimiento (K5) hasta que la API vuelve a responder.');
  out.push('- **Listas paginadas (propuesto, hueco 14):** `{ count, next, previous, results }` con `page` y `page_size` (25 por defecto), como `PageNumberPagination`.');
  out.push('- **Errores:** validación de DRF `{ "campo": ["mensaje"] }`; vistas propias `{ "error": "mensaje", "required"?: "117.00" }`; permisos y 404 `{ "detail": "mensaje" }`. Propuesto: sumar `code` legible por máquina (`duration`, `resolution`, `seo`, `scheduling`, `crossposting`, `seats_exceeded`, `email_not_verified`, `account_not_connected`, `permission_denied`) y, en los errores de campo, ítems `{ "message", "code" }`.');
  out.push('- **402** siempre abre el diálogo de saldo insuficiente y **403 de precios** el de límite del plan.');
  out.push('- **La tabla de roles, en los dos lados:** la tabla 1.7 del mapa está en `src/lib/permissions.ts` (`MATRIX`) y el backend la copia en `ROLE_MATRIX` (`back/apps/core/permissions.py`, PR #125). Que las dos digan lo mismo lo cuida un test de cada lado contra `src/lib/role-matrix.json`, que escribe `python manage.py export_role_matrix` desde `ROLE_MATRIX`: si una tabla cambia sin la otra, falla nombrando el rol y la acción. Lo que la UI deshabilita, la API lo rechaza con el 403 de **Roles**: `{ detail }` en español, sin `code`. Sumar `code: "permission_denied"` sigue propuesto (diferencia 17).');
  out.push('');
  out.push('## Diferencias encontradas con el backend de hoy');
  out.push('');
  out.push('| # | Qué pasa hoy | Qué espera el frontend |');
  out.push('|---|---|---|');
  out.push('| 1 | `GET /api/v1/projects/` devuelve un array sin paginar, sin filtros, y cada ítem trae `transcript_data`, `ai_rationale_log` y `metadata` completos. | Paginado, con `status_group`, `search` y `status_counts`. La lista no necesita la transcripción: conviene un serializer liviano. El cliente acepta el array actual y lo envuelve. |');
  out.push('| 2 | El código de `InvalidUploadError` (`not_a_video`, `too_long`…) no llega en el 400: DRF solo manda el mensaje. | `{ "source_file": [{ "message", "code" }] }`. Mientras tanto el frontend reconoce el código por el texto (`uploadErrorCode()` en `src/lib/error-messages.ts`). |');
  out.push('| 3 | Los 403 de `PricingEngine` traen un mensaje en inglés sin código (`Video duration (…) exceeds your plan limit`). | `code` (`duration`, `resolution`, `seo`) y `limit`. Mientras tanto se reconoce por el texto (`planLimitKind()`). |');
  out.push('| 4 | El rol del modelo es `viewer` (`WorkspaceMember.Role.VIEWER`); el mapa lo llama «Lector». | El contrato usa los valores del backend (`admin`, `editor`, `viewer`) más `owner` derivado de `Workspace.owner`. «Lector» es solo la etiqueta. |');
  out.push('| 5 | ~~`POST/GET /api/v1/clips/{id}/preview/` (PTE-03) figuraba fuera del MVP en el mapa (§7).~~ Resuelto: existe y el mapa lo da dentro del MVP. | Está en el contrato como existente. |');
  out.push('| 6 | `VideoProjectSerializer.create` lee `subtitle_scale_factor` del pedido y lo asigna a un atributo que el modelo no tiene: se pierde. | El frontend no lo manda; el tamaño va en `subtitle_size`. |');
  out.push('| 7 | `subtitle_size` se guarda en `metadata.subtitle_size` al crear, además del campo del modelo. | El frontend lee el campo `subtitle_size` del proyecto. |');
  out.push('| 8 | `brand_kit_name` no viene cuando el proyecto no tiene brand kit (DRF omite el campo). | Opcional en el esquema. |');
  out.push('| 9 | Un `SPEND` devuelto queda `FAILED`; no hay estado «devuelto» ni vínculo proyecto ↔ transacción. | `Transaction.kind` (`released`, `refund`…), `Transaction.project` y `Project.cost { amount, state, transaction_id }`. |');
  out.push('| 10 | `BrandKit.is_default` no es único por workspace. | Al marcar uno, el backend desmarca el resto. |');
  out.push('| 11 | Los links no pasan por la validación de duración del plan. | `POST /projects/probe-link/` (propuesto) reconoce el link y responde 403 si supera el plan. |');
  out.push('| 12 | `error_log` de una publicación guarda el texto de la excepción. | `error_message` legible y `error_code`; `error_log` no se muestra. |');
  out.push('| 13 | `approve-paper-edit` responde 400 `{ error }` para un proyecto que ya no está en revisión («…no está en espera de aprobación. Estado actual: rendering») y para un límite del plan («Error en el motor de precios: …»). El 409 solo sale en la carrera de dos aprobaciones. | Propuesto: 409 con `code: "not_awaiting_approval"` y 403 con `code` (`duration`, `resolution`). Mientras tanto `approveErrorKind()` los reconoce por el texto: el primero avisa y lleva a Clips, el segundo abre el diálogo de límite del plan. |');
  out.push('| 14 | Resuelto en el backend (PR #125): CORS admite `X-Workspace-Id` y las vistas leen el workspace activo de ese header, con los roles aplicados. | Nada pendiente. |');
  out.push('| 15 | `LANGUAGE_CODE = "en-us"`: los errores propios de DRF llegan en inglés («This field is required.», «You do not have permission to perform this action.»). | `LANGUAGE_CODE = "es"` (o `es-ar`). Mientras tanto la UI traduce los conocidos y no muestra texto en inglés. |');
  out.push('| 16 | El 409 de `PATCH /projects/{id}/` mientras se renderiza trae solo `{ detail }`: el código `project_rendering` de la excepción no llega al cuerpo. | `{ detail, code }`. El frontend solo manda `title`, que siempre se puede cambiar. |');
  out.push('| 17 | Un 403 de permisos de DRF (`PermissionDenied`) tampoco trae código. | `code: "permission_denied"`. El frontend trata como límite del plan solo los 403 con un código de plan o con un mensaje de `PricingEngine`; el resto es un 403 de rol. |');
  out.push('| 18 | Resuelto en el backend (PR #121 para `add_subtitles`, PR #126 para el resto): un booleano del modelo que un formulario multipart no manda ya no se lee como `false`. Es «no enviado»: al crear toma el default del modelo (`true` en `add_subtitles`) y al editar no cambia. Lo aplican `VideoProjectSerializer` y `BrandKitSerializer` con `_NOT_SENT_BOOLEANS`. | Nada pendiente. El frontend manda `add_subtitles` en `POST /projects/` y lo deja afuera en `from-prompt` salvo que la persona lo haya elegido. |');
  out.push('| 19 | Los links de descarga firmados no fuerzan la descarga. | Firmar con `ResponseContentDisposition=attachment` (ver Convenciones). |');
  out.push('| 20 | «Renderizar sin revisar» que no se puede pagar deja `metadata.auto_render_skipped` (`insufficient_funds` o `plan_limit`) y el proyecto en revisión; al re-despachar sin saldo falla con `AutoRenderNotPaid`. | Está en el contrato (`ProjectMetadata`, `KNOWN_PIPELINE_ERROR_CODES`). Falta sumar el mensaje de `AutoRenderNotPaid` a la sección 4 del mapa. |');
  out.push('');
  out.push('## Resumen');
  out.push('');
  out.push('| Método | Ruta | Qué hace | Backend | Pantallas |');
  out.push('|---|---|---|---|---|');
  for (const group of CONTRACT) {
    for (const endpoint of group.endpoints) {
      out.push(`| ${endpoint.method} | \`${endpoint.path}\` | ${cell(endpoint.summary)} | ${STATUS_LABEL[endpoint.backend.status]}${gaps(endpoint)} | ${endpoint.screens.join(', ')} |`);
    }
  }
  out.push('');
  out.push('## Detalle por dominio');
  out.push('');
  for (const group of CONTRACT) {
    out.push(`### ${group.title}`);
    out.push('');
    out.push(`Contrato: \`src/api/contracts/${group.domain === 'brandKits' ? 'brandkits' : group.domain}.ts\` · Hooks: \`src/api/hooks/${group.domain === 'brandKits' ? 'brandkits' : group.domain}.ts\` · Mocks: \`src/api/mocks/handlers/${group.domain === 'brandKits' ? 'brandkits' : group.domain}.ts\``);
    out.push('');
    for (const endpoint of group.endpoints) out.push(endpointSection(endpoint));
  }
  out.push('## Esquemas');
  out.push('');
  for (const [module, exports] of MODULES) {
    const schemas = Object.entries(exports)
      .sort(([a], [b]) => a.localeCompare(b))
      .filter(([, value]) => value instanceof z.ZodType && NAMES.get(value as ZodTypeAny)?.module === module);
    if (schemas.length === 0) continue;
    out.push(`### \`${module}.ts\``);
    out.push('');
    for (const [name, value] of schemas) {
      const schema = value as ZodTypeAny;
      const inner = (() => {
        let current = schema;
        // Unwrap refinements and intersections down to something with a shape.
        while (current._def.typeName === 'ZodEffects') current = current._def.schema as ZodTypeAny;
        return current;
      })();
      if (inner instanceof z.ZodObject) {
        out.push(`**${name}**`);
        out.push('');
        out.push('| Campo | Tipo |');
        out.push('|---|---|');
        for (const [key, field] of Object.entries(inner.shape as z.ZodRawShape)) {
          out.push(`| \`${key}\`${(field as ZodTypeAny).isOptional() ? ' (opcional)' : ''} | ${cell(describe(field as ZodTypeAny, 1))} |`);
        }
        out.push('');
      } else {
        out.push(`**${name}:** ${describe(schema, 0, false)}`);
        out.push('');
      }
    }
  }
  return `${out.join('\n').trimEnd()}\n`;
}

const AREA = { public: 'Pública', app: 'App (con sesión)', onboarding: 'Primera vez (con sesión, sin barra)', system: 'Sistema' } as const;

export function renderRoutes(): string {
  const out: string[] = [];
  out.push('# Rutas de OneCreator');
  out.push('');
  out.push('> Archivo generado desde `src/dev/screens.ts` con `npm run contract:doc`. Para sumar una pantalla, agregala ahí y volvé a generarlo.');
  out.push('');
  out.push('Cada pantalla de [`documents/producto/mapa-de-pantallas.md`](../documents/producto/mapa-de-pantallas.md) tiene su ruta. Las que todavía no están construidas muestran un cartel con su código; el dominio dueño reemplaza el `page.tsx` y pone su código en `src/features/<dominio>/`.');
  out.push('');
  out.push('Los artboards están en el canvas de diseño, un archivo `<nombre>.dc.html` por pantalla. Los `M*` son la misma pantalla en el teléfono y los `R*` son variantes por rol.');
  out.push('');
  out.push('| Código | Pantalla | Ruta | Archivo | Artboards | Dominio | Zona | Roles |');
  out.push('|---|---|---|---|---|---|---|---|');
  const file = (route: string, area: string) => {
    const group = area === 'public' ? '(public)' : area === 'app' ? '(app)' : area === 'onboarding' ? '(onboarding)' : route === '/sin-permiso' ? '(app)' : '';
    const tabs = /^\/proyectos\/\[id\](\/(clips|transcripcion|actividad|resultado))?$/.test(route);
    const path = tabs ? route.replace('/proyectos/[id]', '/proyectos/[id]/(tabs)') : route;
    return `src/app/${group ? `${group}` : ''}${path}/page.tsx`.replace('//', '/');
  };
  for (const screen of Object.values(SCREENS)) {
    out.push(
      `| ${screen.code} | ${screen.name} | \`${screen.route}\` | \`${file(screen.route, screen.area)}\` | ${screen.artboards.map((name) => `\`${name}\``).join(', ')} | ${screen.owner} | ${AREA[screen.area]} | ${'roles' in screen && screen.roles ? screen.roles : 'Todos'} |`,
    );
  }
  out.push('');
  out.push('## Piezas sin ruta propia');
  out.push('');
  out.push('| Código | Pieza | Dónde vive | Artboards | Dominio |');
  out.push('|---|---|---|---|---|');
  for (const item of ROUTELESS) {
    out.push(`| ${item.code} | ${item.name} | ${item.where} | ${item.artboards.map((name) => `\`${name}\``).join(', ')} | ${item.owner} |`);
  }
  out.push('');
  out.push('## Rutas de desarrollo');
  out.push('');
  out.push('| Ruta | Para qué |');
  out.push('|---|---|');
  out.push('| `/dev/kit` | Todos los componentes del sistema visual en los dos temas, y el selector de escenarios de datos de prueba. |');
  out.push('| `/dev/checkout` | Solo con mocks (modos mock e hybrid): reemplaza al checkout del proveedor de pagos (aprueba o rechaza la compra y vuelve). Con `billing` en el backend decide con `billing.simulatePurchase`, el proveedor simulado (`PAYMENTS_PROVIDER=simulated`). |');
  out.push('| `/dev/conectar-red` | Solo con mocks: reemplaza a la ventana de autorización de la red social. |');
  out.push('');
  out.push('## Dominios');
  out.push('');
  out.push('| Dominio | Pantallas | Carpeta |');
  out.push('|---|---|---|');
  const owners = new Map<string, string[]>();
  for (const screen of Object.values(SCREENS)) owners.set(screen.owner, [...(owners.get(screen.owner) ?? []), screen.code]);
  for (const item of ROUTELESS) owners.set(item.owner, [...(owners.get(item.owner) ?? []), item.code]);
  for (const [owner, codes] of owners) {
    out.push(`| ${owner} | ${codes.join(', ')} | ${owner === 'base' ? '`src/ui`, `src/shell`, `src/dialogs`, `src/api`, `src/lib`' : `\`src/features/${owner}/\``} |`);
  }
  out.push('');
  out.push('## Protección de rutas');
  out.push('');
  out.push('- Todo lo de `(app)` y `(onboarding)` pasa por `<RequireSession>`: sin sesión va a `/login?siguiente=<ruta>`.');
  out.push('- Una cuenta nueva que entra a `/proyectos` va a `/bienvenida` (`needsWelcome()` en `src/shell/session.tsx`); al terminar o saltear, la pantalla llama a `markWelcomeSeen()`.');
  out.push('- Lo que el rol no permite se muestra deshabilitado con «Pedile acceso a un admin»: `usePermissions().gate(acción)` devuelve las props para el botón. Solo las secciones de admin de Ajustes se ocultan (`<RequireRole>` muestra K2 si se entra por URL).');
  return `${out.join('\n')}\n`;
}
