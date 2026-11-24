import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { Id, IsoDateTime, NoContent } from './common';

/** The five cases of map 1.11. */
export const NotificationType = z.enum(['project_ready', 'render_finished', 'project_failed', 'post_failed', 'low_balance']);
export type NotificationType = z.infer<typeof NotificationType>;

export const Notification = z.object({
  id: Id,
  type: NotificationType,
  /** "Podcast Ep. 42 está listo para revisar" */
  title: z.string(),
  /** "Encontramos 9 clips." */
  body: z.string(),
  created_at: IsoDateTime,
  read_at: IsoDateTime.nullable(),
  /** Where it leads. */
  project_id: Id.nullable(),
  post_id: Id.nullable(),
});
export type Notification = z.infer<typeof Notification>;

export const NotificationsResponse = z.object({
  unread_count: z.number().int().nonnegative(),
  /** The last 20. */
  results: z.array(Notification),
});
export type NotificationsResponse = z.infer<typeof NotificationsResponse>;

export const listNotifications = defineEndpoint({
  id: 'notifications.list',
  method: 'GET',
  path: '/api/v1/notifications/',
  summary: 'Las últimas 20 notificaciones del usuario en el workspace y cuántas están sin leer.',
  response: NotificationsResponse,
  errors: [],
  backend: { status: 'proposed', gap: 16 },
  screens: ['J2', 'Barra'],
});

export const markNotificationRead = defineEndpoint({
  id: 'notifications.markRead',
  method: 'POST',
  path: '/api/v1/notifications/:id/read/',
  summary: 'Marca una notificación como leída.',
  response: Notification,
  errors: [{ status: 404, when: 'No existe.' }],
  backend: { status: 'proposed', gap: 16 },
  screens: ['J2'],
});

export const markAllNotificationsRead = defineEndpoint({
  id: 'notifications.markAllRead',
  method: 'POST',
  path: '/api/v1/notifications/read-all/',
  summary: 'Marca todas como leídas.',
  response: NoContent,
  successStatus: 204,
  errors: [],
  backend: { status: 'proposed', gap: 16 },
  screens: ['J2'],
});

export const notificationsEndpoints = [listNotifications, markNotificationRead, markAllNotificationsRead] as const;
