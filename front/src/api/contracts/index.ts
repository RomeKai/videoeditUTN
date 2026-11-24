import type { AnyEndpoint } from '../endpoint';
import { authEndpoints } from './auth';
import { billingEndpoints } from './billing';
import { brandKitsEndpoints } from './brandkits';
import { clipsEndpoints } from './clips';
import { notificationsEndpoints } from './notifications';
import { projectsEndpoints } from './projects';
import { socialEndpoints } from './social';
import { systemEndpoints } from './system';
import { usersEndpoints } from './users';
import { workspacesEndpoints } from './workspaces';

export * as auth from './auth';
export * as billing from './billing';
export * as brandKits from './brandkits';
export * as clips from './clips';
export * as common from './common';
export * as notifications from './notifications';
export * as projects from './projects';
export * as social from './social';
export * as system from './system';
export * as users from './users';
export * as workspaces from './workspaces';

/** Every endpoint of the contract, grouped by domain. CONTRACT.md and the mock coverage test are built from this. */
export const CONTRACT: ReadonlyArray<{ domain: string; title: string; endpoints: ReadonlyArray<AnyEndpoint> }> = [
  { domain: 'auth', title: 'Acceso', endpoints: authEndpoints },
  { domain: 'users', title: 'Usuario y perfil', endpoints: usersEndpoints },
  { domain: 'workspaces', title: 'Workspaces, miembros e invitaciones', endpoints: workspacesEndpoints },
  { domain: 'billing', title: 'Planes, billetera, movimientos, compras y precios', endpoints: billingEndpoints },
  { domain: 'projects', title: 'Proyectos', endpoints: projectsEndpoints },
  { domain: 'clips', title: 'Clips', endpoints: clipsEndpoints },
  { domain: 'brandKits', title: 'Brand kits', endpoints: brandKitsEndpoints },
  { domain: 'social', title: 'Redes sociales y publicaciones', endpoints: socialEndpoints },
  { domain: 'notifications', title: 'Notificaciones', endpoints: notificationsEndpoints },
  { domain: 'system', title: 'Sistema', endpoints: systemEndpoints },
];

export const ALL_ENDPOINTS: ReadonlyArray<AnyEndpoint> = CONTRACT.flatMap((group) => group.endpoints);
