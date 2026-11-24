import type { IconName } from '@/ui/Icon';

/** The five sections of the second row of the topbar (map 1.1). */
export const MAIN_NAV: Array<{ href: string; label: string; shortLabel?: string; icon: IconName }> = [
  { href: '/proyectos', label: 'Proyectos', icon: 'folder' },
  { href: '/publicaciones', label: 'Publicaciones', icon: 'calendar' },
  { href: '/brand-kits', label: 'Brand kits', icon: 'brand-kit' },
  { href: '/uso', label: 'Uso y facturación', shortLabel: 'Uso', icon: 'tokens' },
  { href: '/ajustes', label: 'Ajustes', icon: 'settings' },
];

/** Sub-tabs of Uso y facturación (G1–G4). */
export const USAGE_TABS = [
  { href: '/uso', label: 'Resumen', exact: true },
  { href: '/uso/movimientos', label: 'Movimientos' },
  { href: '/uso/planes', label: 'Planes' },
  { href: '/uso/comprar', label: 'Comprar tokens' },
];

/** Side navigation of Ajustes (H1–H3). Miembros and Redes are for Dueño and Admin only. */
export const SETTINGS_NAV: Array<{ href: string; label: string; icon: IconName; exact?: boolean; adminOnly?: boolean }> = [
  { href: '/ajustes', label: 'General', icon: 'settings', exact: true },
  { href: '/ajustes/miembros', label: 'Miembros', icon: 'users', adminOnly: true },
  { href: '/ajustes/redes', label: 'Redes sociales', icon: 'send', adminOnly: true },
];

/** Side navigation of Perfil (I1, I2). */
export const PROFILE_NAV: Array<{ href: string; label: string; icon: IconName; exact?: boolean }> = [
  { href: '/perfil', label: 'Perfil', icon: 'user', exact: true },
  { href: '/perfil/notificaciones', label: 'Notificaciones', icon: 'bell' },
];

/** Tabs of a project (D1). A whole-video project shows "Resultado" instead of "Clips" (D5). */
export function projectTabs(projectId: string, projectType: 'repurpose' | 'single_edit' = 'repurpose') {
  const base = `/proyectos/${projectId}`;
  return [
    { href: base, label: 'Resumen', exact: true },
    projectType === 'single_edit' ? { href: `${base}/resultado`, label: 'Resultado' } : { href: `${base}/clips`, label: 'Clips', exact: true },
    { href: `${base}/transcripcion`, label: 'Transcripción' },
    { href: `${base}/actividad`, label: 'Actividad' },
  ];
}

/** Where a notification leads (J2). */
export function notificationHref(notification: { type: string; project_id: string | null; post_id: string | null }): string {
  switch (notification.type) {
    case 'project_ready':
      return notification.project_id ? `/proyectos/${notification.project_id}/revision` : '/proyectos';
    case 'render_finished':
      return notification.project_id ? `/proyectos/${notification.project_id}/clips` : '/proyectos';
    case 'project_failed':
      return notification.project_id ? `/proyectos/${notification.project_id}` : '/proyectos';
    case 'post_failed':
      return notification.post_id ? `/publicaciones?publicacion=${notification.post_id}` : '/publicaciones';
    case 'low_balance':
      return '/uso/comprar';
    default:
      return '/proyectos';
  }
}
