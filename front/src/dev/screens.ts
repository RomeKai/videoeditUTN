/**
 * Registry of every screen of `documents/producto/mapa-de-pantallas.md`: its
 * route, the artboards that draw it and the domain that owns it. The route
 * placeholders, ROUTES.md (generated: `npm run contract:doc`) and the Playwright
 * smoke test all read it, so a new screen is added here first.
 */
export interface ScreenInfo {
  /** Code of the map. */
  code: string;
  name: string;
  /** Next.js route pattern, with [param] segments. */
  route: string;
  /** A concrete URL that works with the default mock scenario (for the smoke test and for links). */
  example: string;
  /** Artboard files of the design canvas (`*.dc.html`, without extension). */
  artboards: string[];
  /** Owner domain: the folder under `src/features/` where its code goes. */
  owner: string;
  purpose: string;
  /** public: no session. app: session + app bar. onboarding: session, no app bar. system: no session needed. */
  area: 'public' | 'app' | 'onboarding' | 'system';
  /** Who can open it. Empty: every role. */
  roles?: string;
}

/** Ids of the default mock scenario (see src/api/mocks/seed.ts): projects 3 (to review), 5 (ready), 7 (whole video). */
export const EXAMPLE_IDS = {
  projectToReview: '00000007-0000-4000-8000-000000000003',
  projectReady: '00000007-0000-4000-8000-000000000005',
  projectSingleEdit: '00000007-0000-4000-8000-000000000007',
  clip: '00000008-0000-4000-8000-0000000001f5',
  brandKit: '00000009-0000-4000-8000-000000000001',
} as const;

const P = EXAMPLE_IDS;

export const SCREENS = {
  A1: { code: 'A1', name: 'Iniciar sesión', route: '/login', example: '/login', artboards: ['A1-Login', 'M12-Movil-login'], owner: 'acceso', area: 'public', purpose: 'Entrar con email y contraseña.' },
  A2: { code: 'A2', name: 'Crear cuenta', route: '/registro', example: '/registro', artboards: ['A2-Registro'], owner: 'acceso', area: 'public', purpose: 'Nombre, email, contraseña y aceptación de términos. Después pasa a Verificar email.' },
  A3: { code: 'A3', name: 'Verificar email', route: '/verificar', example: '/verificar', artboards: ['A3-Verificar-email'], owner: 'acceso', area: 'public', purpose: '"Te mandamos un link a tu email", con Reenviar cada 60 s. Con #token= (el link del email) verifica y entra a la Bienvenida.' },
  A4: { code: 'A4', name: 'Recuperar contraseña', route: '/recuperar', example: '/recuperar', artboards: ['A4-Recuperar'], owner: 'acceso', area: 'public', purpose: 'Paso 1: pedir el email. Paso 2 (con ?token=): elegir una contraseña nueva.' },
  A5: { code: 'A5', name: 'Aceptar invitación', route: '/invitacion', example: '/invitacion#token=valida', artboards: ['A5-Invitacion'], owner: 'acceso', area: 'public', purpose: '"Juan te invitó a Estudio Norte como Editor": crear la cuenta ahí mismo o iniciar sesión. Estados: vencida o ya usada.' },
  A6: { code: 'A6', name: 'Precios', route: '/precios', example: '/precios', artboards: ['A6-Precios'], owner: 'uso', area: 'public', purpose: 'Comparar planes antes y después de registrarse. Misma tarjeta y textos que Uso › Planes (G3).' },
  B1: { code: 'B1', name: 'Bienvenida', route: '/bienvenida', example: '/bienvenida', artboards: ['B1b-Bienvenida-paso1', 'B1-Bienvenida', 'B1c-Bienvenida-paso3'], owner: 'acceso', area: 'onboarding', purpose: 'Primera vez: nombre del workspace, para qué lo vas a usar e ir a Nuevo proyecto. Se puede saltear.' },
  B2: { code: 'B2', name: 'Proyectos', route: '/proyectos', example: '/proyectos', artboards: ['B2-Proyectos', 'B2b-Proyectos-vacio', 'B2c-Proyectos-grilla', 'M1-Movil-proyectos'], owner: 'proyectos', area: 'app', purpose: 'Lista (o grilla) de proyectos con buscador, filtro por estado, lista de primeros pasos y acciones por fila.' },
  C1: { code: 'C1', name: 'Nuevo proyecto', route: '/proyectos/nuevo', example: '/proyectos/nuevo', artboards: ['C1-Nuevo-proyecto', 'C1b-Nuevo-con-IA', 'C1c-Nuevo-errores', 'C1d-Nuevo-link', 'C1e-Nuevo-plan-gratis', 'M4-Movil-nuevo'], owner: 'proyectos', area: 'app', purpose: 'Crear un proyecto en una sola pantalla: origen, qué hacer, formato, clips, estilo, subtítulos, inteligencia y resumen con el costo.', roles: 'Dueño, Admin, Editor' },
  D1: { code: 'D1', name: 'Proyecto · Resumen', route: '/proyectos/[id]', example: `/proyectos/${P.projectReady}`, artboards: ['D1-Proyecto-procesando', 'D1b-Proyecto-en-espera', 'D1c-Proyecto-error', 'D1d-Proyecto-listo', 'M2-Movil-proyecto'], owner: 'proyecto', area: 'app', purpose: 'Seguir el proceso (línea de tiempo por etapa) y ver la configuración aplicada. Cabecera única del proyecto.' },
  D1e: { code: 'D1e', name: 'Proyecto · Transcripción', route: '/proyectos/[id]/transcripcion', example: `/proyectos/${P.projectReady}/transcripcion`, artboards: ['D1e-Proyecto-transcripcion'], owner: 'proyecto', area: 'app', purpose: 'Texto completo con marcas de tiempo, búsqueda, tramos que se volvieron clips y descarga .txt/.srt.' },
  D1f: { code: 'D1f', name: 'Proyecto · Actividad', route: '/proyectos/[id]/actividad', example: `/proyectos/${P.projectReady}/actividad`, artboards: ['D1f-Proyecto-actividad'], owner: 'proyecto', area: 'app', purpose: 'Quién hizo qué y los movimientos de tokens del proyecto.' },
  D2: { code: 'D2', name: 'Revisión de clips', route: '/proyectos/[id]/revision', example: `/proyectos/${P.projectToReview}/revision`, artboards: ['D2-Revision', 'D2b-Revision-sin-sugerencias', 'M3-Movil-revision'], owner: 'revision', area: 'app', purpose: 'Elegir y ajustar los clips antes de pagar el render: lista de sugeridos, reproductor y transcripción. Barra fija con Renderizar.', roles: 'Dueño, Admin, Editor (Lector: solo lectura)' },
  D3: { code: 'D3', name: 'Proyecto · Clips', route: '/proyectos/[id]/clips', example: `/proyectos/${P.projectReady}/clips`, artboards: ['D3-Clips', 'M6-Movil-clips', 'R2-Rol-lector-clips'], owner: 'proyecto', area: 'app', purpose: 'Ver, descargar y publicar los resultados. Grilla de tarjetas, modo selección, estados Renderizando y Error.' },
  D4: { code: 'D4', name: 'Editor de clip', route: '/proyectos/[id]/clips/[clipId]', example: `/proyectos/${P.projectReady}/clips/${P.clip}`, artboards: ['D4-Editor-clip', 'M7-Movil-editor'], owner: 'revision', area: 'app', purpose: 'Cambiar recorte y título del clip y volver a renderizar con la configuración del proyecto.', roles: 'Dueño, Admin, Editor' },
  D5: { code: 'D5', name: 'Proyecto · Resultado (video completo)', route: '/proyectos/[id]/resultado', example: `/proyectos/${P.projectSingleEdit}/resultado`, artboards: ['D5-Resultado-video-completo'], owner: 'proyecto', area: 'app', purpose: 'El único video que devuelve un proyecto «Editar el video completo»: reproductor, datos, Descargar, Publicar y volver a renderizar.' },
  E2: { code: 'E2', name: 'Publicaciones', route: '/publicaciones', example: '/publicaciones', artboards: ['E2-Publicaciones-calendario', 'E2b-Publicaciones-lista', 'E2c-Publicaciones-mes', 'E2d-Publicaciones-vacio', 'M8-Movil-publicaciones'], owner: 'publicar', area: 'app', purpose: 'Todo lo publicado y lo que va a salir: calendario (semana o mes) y lista con filtros; detalle en panel lateral.' },
  F1: { code: 'F1', name: 'Brand kits', route: '/brand-kits', example: '/brand-kits', artboards: ['F1-Brand-kits', 'F1b-Brand-kits-vacio', 'M11-Movil-brand-kits'], owner: 'brand-kits', area: 'app', purpose: 'Tarjetas con colores, logo y nombre de cada kit. Crear, duplicar, marcar como default y borrar.' },
  F2: { code: 'F2', name: 'Editar brand kit', route: '/brand-kits/[id]', example: `/brand-kits/${P.brandKit}`, artboards: ['F2-Brand-kit-editar'], owner: 'brand-kits', area: 'app', purpose: 'Nombre, colores, fuentes, logo, intro y outro, con vista previa. `/brand-kits/nuevo` crea uno.', roles: 'Dueño, Admin, Editor' },
  G1: { code: 'G1', name: 'Uso · Resumen', route: '/uso', example: '/uso', artboards: ['G1-Uso', 'M9-Movil-uso', 'R1-Rol-editor-uso'], owner: 'uso', area: 'app', purpose: 'Saldo disponible y reservado (con detalle por proyecto), gastado este mes, plan actual y gráfico de consumo.', roles: 'Dueño y Admin (Editor y Lector ven solo el saldo: R1)' },
  G2: { code: 'G2', name: 'Uso · Movimientos', route: '/uso/movimientos', example: '/uso/movimientos', artboards: ['G2-Movimientos'], owner: 'uso', area: 'app', purpose: 'Tabla de movimientos con filtros por tipo y mes, y exportar a CSV.', roles: 'Dueño y Admin' },
  G3: { code: 'G3', name: 'Uso · Planes', route: '/uso/planes', example: '/uso/planes', artboards: ['G3-Planes'], owner: 'uso', area: 'app', purpose: 'Igual que Precios, con el plan actual marcado. Subir aplica al momento; bajar, al fin del período.', roles: 'Dueño y Admin cambian el plan' },
  G4: { code: 'G4', name: 'Uso · Comprar tokens', route: '/uso/comprar', example: '/uso/comprar', artboards: ['G4-Comprar-tokens'], owner: 'uso', area: 'app', purpose: '3 paquetes fijos y una cantidad libre; va al checkout del proveedor de pagos.', roles: 'Dueño y Admin' },
  G5: { code: 'G5', name: 'Uso · Resultado de la compra', route: '/uso/comprar/resultado', example: '/uso/comprar/resultado', artboards: ['G5-Compra-resultado'], owner: 'uso', area: 'app', purpose: 'Vuelta del checkout (?compra=<id>): compra acreditada o pago rechazado.', roles: 'Dueño y Admin' },
  H1: { code: 'H1', name: 'Ajustes · General', route: '/ajustes', example: '/ajustes', artboards: ['H1-Ajustes-general', 'M10-Movil-ajustes'], owner: 'ajustes', area: 'app', purpose: 'Nombre del workspace y zona de peligro (transferir, borrar).', roles: 'Renombrar: Dueño y Admin. Zona de peligro: Dueño' },
  H2: { code: 'H2', name: 'Ajustes · Miembros', route: '/ajustes/miembros', example: '/ajustes/miembros', artboards: ['H2-Miembros'], owner: 'ajustes', area: 'app', purpose: 'Tabla de miembros e invitaciones, cambio de rol, invitar (D7), reenviar y quitar. Contador de lugares del plan.', roles: 'Dueño y Admin' },
  H3: { code: 'H3', name: 'Ajustes · Redes sociales', route: '/ajustes/redes', example: '/ajustes/redes', artboards: ['H3-Redes'], owner: 'ajustes', area: 'app', purpose: 'Una fila por red con su estado y Conectar, Reconectar o Desconectar.', roles: 'Dueño y Admin' },
  I1: { code: 'I1', name: 'Perfil', route: '/perfil', example: '/perfil', artboards: ['I1-Perfil'], owner: 'ajustes', area: 'app', purpose: 'Nombre, email, contraseña, apariencia (Sistema, Oscuro, Claro) y cerrar sesión.' },
  I2: { code: 'I2', name: 'Perfil · Notificaciones', route: '/perfil/notificaciones', example: '/perfil/notificaciones', artboards: ['I2-Notificaciones'], owner: 'ajustes', area: 'app', purpose: 'Un interruptor de email por cada tipo de notificación.' },
  K2: { code: 'K2', name: 'Sin permiso', route: '/sin-permiso', example: '/sin-permiso', artboards: ['04-Sistema'], owner: 'base', area: 'system', purpose: '"No tenés acceso a esto. Pedile a un admin del workspace."' },
  K5: { code: 'K5', name: 'Mantenimiento', route: '/mantenimiento', example: '/mantenimiento', artboards: ['04-Sistema'], owner: 'base', area: 'system', purpose: 'Página simple con el horario estimado.' },
} as const satisfies Record<string, ScreenInfo>;

export type ScreenCode = keyof typeof SCREENS;

/** Pieces that have no route of their own: where they live and who owns them. */
export const ROUTELESS: Array<{ code: string; name: string; where: string; artboards: string[]; owner: string }> = [
  { code: 'E1', name: 'Publicar (panel lateral)', where: 'Panel sobre Clips, Resultado y Publicaciones. Convención: `?publicar=<clipId>[,<clipId>…]` en la URL de la pantalla que lo abre.', artboards: ['E1-Publicar', 'E1b-Publicar-instagram', 'E1c-Publicar-varios', 'M5-Movil-publicar'], owner: 'publicar' },
  { code: 'J1', name: 'Menú de comandos (⌘K)', where: 'App shell: `src/shell/CommandMenu.tsx`.', artboards: ['03-Transversales'], owner: 'base' },
  { code: 'J2', name: 'Notificaciones (campana)', where: 'App shell: `src/shell/NotificationsPanel.tsx`.', artboards: ['03-Transversales'], owner: 'base' },
  { code: 'J3', name: 'Selector de workspace', where: 'App shell: `src/shell/WorkspaceSwitcher.tsx`.', artboards: ['03-Transversales'], owner: 'base' },
  { code: 'J4', name: 'Toasts', where: '`useToast()` de `@/ui`.', artboards: ['03-Transversales', '01-Sistema-visual'], owner: 'base' },
  { code: 'K1', name: 'No encontrado (404)', where: '`src/app/not-found.tsx` y `<NotFoundMessage>`.', artboards: ['04-Sistema'], owner: 'base' },
  { code: 'K3', name: 'Error del servidor (500)', where: '`src/app/error.tsx`, `src/app/global-error.tsx` y `<ServerErrorMessage>`.', artboards: ['04-Sistema'], owner: 'base' },
  { code: 'K4', name: 'Sin conexión', where: 'Banner fijo: `src/shell/OfflineBanner.tsx`.', artboards: ['04-Sistema'], owner: 'base' },
  { code: 'D1 (diálogo)', name: 'Confirmar costo', where: '`<ConfirmCostDialog>` de `@/dialogs`.', artboards: ['02-Dialogos'], owner: 'base' },
  { code: 'D2 (diálogo)', name: 'Saldo insuficiente', where: '`useBillingDialogs()` de `@/dialogs`.', artboards: ['02-Dialogos'], owner: 'base' },
  { code: 'D3 (diálogo)', name: 'Límite del plan', where: '`useBillingDialogs()` de `@/dialogs`.', artboards: ['02-Dialogos'], owner: 'base' },
  { code: 'D4 (diálogo)', name: 'Borrar proyecto', where: '`<DeleteProjectDialog>` de `@/dialogs`.', artboards: ['02-Dialogos'], owner: 'base' },
  { code: 'D5 (diálogo)', name: 'Contenido bloqueado', where: '`<ContentBlockedDialog>` de `@/dialogs`.', artboards: ['02-Dialogos'], owner: 'base' },
  { code: 'D6 (diálogo)', name: 'Conectar red social', where: '`<ConnectNetworkDialog>` de `@/dialogs`.', artboards: ['02-Dialogos'], owner: 'base' },
  { code: 'D7 (diálogo)', name: 'Invitar miembro', where: 'A construir en `src/features/ajustes/` con `<Dialog>`; hooks `useMembers` y `useInviteMembers`.', artboards: ['02-Dialogos'], owner: 'ajustes' },
  { code: 'D8 (diálogo)', name: 'Cambios sin guardar', where: '`<UnsavedChangesDialog>` de `@/dialogs` y `useBeforeUnload()`.', artboards: ['02-Dialogos'], owner: 'base' },
  { code: 'D9 (diálogo)', name: 'Sesión vencida', where: '`<SessionExpiredDialog>`, montado en el layout raíz.', artboards: ['02-Dialogos'], owner: 'base' },
];
