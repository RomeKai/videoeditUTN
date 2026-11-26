import type { SeedOptions } from './seed';

export interface Scenario {
  id: string;
  /** Short name for the switcher. */
  label: string;
  /** What it lets you see. */
  description: string;
  group: 'Datos' | 'Plan' | 'Rol' | 'Sesión' | 'Sistema';
  options: Omit<SeedOptions, 'scenario'>;
  /**
   * Also works in hybrid mode, where the user and part of the data come from the real backend: only the
   * scenarios about what a mocked domain answers (the plan, the balance, payments, a server that fails).
   */
  hybrid?: boolean;
}

/** Why the other scenarios are disabled in hybrid mode (shown in the picker). */
export const HYBRID_ONLY_MOCK_REASON = 'Solo en modo mock: en híbrido la sesión, el usuario y sus datos vienen del backend real.';
export const HYBRID_MAINTENANCE_REASON = 'Solo en modo mock: en híbrido el backend real responde normalmente y la página de mantenimiento se cerraría sola.';

export function hybridBlockReason(scenario: Scenario): string | null {
  if (scenario.hybrid) return null;
  return scenario.id === 'mantenimiento' ? HYBRID_MAINTENANCE_REASON : HYBRID_ONLY_MOCK_REASON;
}

export const DEFAULT_SCENARIO = 'estudio-norte';

/**
 * Mock scenarios. Each one seeds a whole database; the switcher (dev panel and
 * /dev/kit) stores the choice and reloads. Add one here when a screen needs a
 * state that none covers.
 */
export const SCENARIOS: Scenario[] = [
  {
    id: 'estudio-norte',
    label: 'Estudio Norte',
    description: 'Lucía, dueña, plan Pro, 4.860 tokens. Proyectos en cada estado principal, 3 brand kits, 2 redes conectadas y publicaciones en todos los estados.',
    group: 'Datos',
    options: {},
    hybrid: true,
  },
  {
    id: 'usuario-nuevo',
    label: 'Usuario nuevo',
    description: 'Sofía recién verificó su email: sin tour, workspace «Mi Espacio» en plan Gratis (2 lugares: ella y una persona más) con 300 tokens. Entra por la Bienvenida.',
    group: 'Datos',
    options: { as: 'nuevo', plan: 'free' },
  },
  {
    id: 'workspace-vacio',
    label: 'Workspace vacío',
    description: 'Estudio Norte sin proyectos, sin brand kits, sin publicaciones y sin redes conectadas: todos los estados vacíos.',
    group: 'Datos',
    options: { projects: 'none', brandKits: false, social: {} },
  },
  {
    id: 'todos-los-estados',
    label: 'Todos los estados',
    description: 'Un proyecto por cada estado y etapa del pipeline, en espera, sin sugerencias, video completo, prompt fallido y un error por cada pipeline_error_code.',
    group: 'Datos',
    options: { projects: 'all-states' },
  },
  {
    id: 'muchos-proyectos',
    label: 'Muchos proyectos',
    description: '64 proyectos para ver la paginación de 25 y el buscador.',
    group: 'Datos',
    options: { projects: 'many' },
  },
  {
    id: 'saldo-bajo',
    label: 'Saldo bajo',
    description: '120 tokens: aviso de saldo bajo, el resumen de Nuevo proyecto cambia a «Comprar tokens» y crear o renderizar responde 402.',
    group: 'Datos',
    options: { balance: 120 },
    hybrid: true,
  },
  {
    id: 'miembros-al-limite',
    label: 'Miembros al límite',
    description: '4 miembros y 1 invitación pendiente en el plan Pro (5 lugares): «Invitar» queda deshabilitado.',
    group: 'Datos',
    options: { seatsFull: true },
  },
  {
    id: 'redes-vencidas',
    label: 'Redes vencidas',
    description: 'TikTok con la conexión vencida e Instagram y YouTube sin conectar: pedir reconectar al publicar.',
    group: 'Datos',
    options: { social: { TIKTOK: 'expired' } },
  },
  {
    id: 'plan-gratis',
    label: 'Plan Gratis',
    description: 'Estudio Norte en plan Gratis: candados (1080p, 4K, SEO, programar, varias redes), marca de agua, videos de hasta 10 min y 300 tokens.',
    group: 'Plan',
    options: { plan: 'free', balance: 300 },
    hybrid: true,
  },
  {
    id: 'plan-creador',
    label: 'Plan Creador',
    description: 'Hasta 1080p, programa publicaciones, una sola red por publicación, 5 % de descuento.',
    group: 'Plan',
    options: { plan: 'creator' },
    hybrid: true,
  },
  {
    id: 'plan-estudio',
    label: 'Plan Estudio',
    description: 'El plan más alto: 4K, 180 min, 15 miembros, 20 % de descuento.',
    group: 'Plan',
    options: { plan: 'studio' },
    hybrid: true,
  },
  {
    id: 'rol-admin',
    label: 'Rol Admin',
    description: 'Martín, Admin de Estudio Norte: todo menos borrar o transferir el workspace.',
    group: 'Rol',
    options: { as: 'martin' },
  },
  {
    id: 'rol-editor',
    label: 'Rol Editor',
    description: 'Carla, Editora: crea, revisa y publica; no ve movimientos ni administra miembros, redes o plan. Borra solo sus proyectos.',
    group: 'Rol',
    options: { as: 'carla' },
  },
  {
    id: 'rol-lector',
    label: 'Rol Lector',
    description: 'Diego, Lector: ve y descarga. Todo lo demás aparece deshabilitado con «Pedile acceso a un admin».',
    group: 'Rol',
    options: { as: 'diego' },
  },
  {
    id: 'sin-sesion',
    label: 'Sin sesión',
    description: 'Arranca sin sesión: login, registro, recuperar e invitación.',
    group: 'Sesión',
    options: { as: null },
  },
  {
    id: 'email-sin-verificar',
    label: 'Email sin verificar',
    description: 'El login responde que la cuenta no verificó el email: lleva a Verificar email.',
    group: 'Sesión',
    options: { as: null, flags: { unverifiedLogin: true } },
  },
  {
    id: 'sesion-vencida',
    label: 'Sesión vencida',
    description: 'El token ya no sirve y no se puede renovar: se abre el diálogo «Tu sesión venció».',
    group: 'Sesión',
    options: { flags: { sessionExpired: true } },
  },
  {
    id: 'pago-rechazado',
    label: 'Pago rechazado',
    description: 'El proveedor de pagos rechaza las compras de tokens.',
    group: 'Sistema',
    options: { flags: { rejectPayments: true } },
    hybrid: true,
  },
  {
    id: 'plan-con-checkout',
    label: 'Plan con checkout',
    description: 'Subir de plan pide un pago: Planes manda al checkout y, al volver, avisa si el plan cambió o si el pago no salió.',
    group: 'Sistema',
    options: { flags: { planCheckout: true } },
    hybrid: true,
  },
  {
    id: 'servidor-caido',
    label: 'Servidor caído',
    description: 'Las lecturas responden 500: banners de error con Reintentar y la página de error.',
    group: 'Sistema',
    options: { flags: { serverDown: true } },
    hybrid: true,
  },
  {
    id: 'mantenimiento',
    label: 'Mantenimiento',
    description: 'El estado del sistema avisa mantenimiento: la app muestra la página K5.',
    group: 'Sistema',
    options: { flags: { maintenance: true } },
  },
];

export function findScenario(id: string | null | undefined): Scenario {
  return SCENARIOS.find((scenario) => scenario.id === id) ?? (SCENARIOS[0] as Scenario);
}
