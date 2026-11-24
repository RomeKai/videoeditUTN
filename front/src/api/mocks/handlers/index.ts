import type { HttpHandler } from 'msw';
import { authHandlers } from './auth';
import { billingHandlers } from './billing';
import { brandKitsHandlers } from './brandkits';
import { clipsHandlers } from './clips';
import { notificationsHandlers } from './notifications';
import { projectsHandlers } from './projects';
import { socialHandlers } from './social';
import { systemHandlers } from './system';
import { usersHandlers } from './users';
import { workspacesHandlers } from './workspaces';

/** One handler per endpoint of the contract (checked by handlers.test.ts). */
export const handlers: HttpHandler[] = [
  ...authHandlers,
  ...usersHandlers,
  ...workspacesHandlers,
  ...billingHandlers,
  ...projectsHandlers,
  ...clipsHandlers,
  ...brandKitsHandlers,
  ...socialHandlers,
  ...notificationsHandlers,
  ...systemHandlers,
];
