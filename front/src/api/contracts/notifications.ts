import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { Id, IsoDateTime, NoContent } from './common';

/**
 * The five cases of map 1.11, plus the result of a payment (only in the app, no email).
 * The backend writes all of them (back/apps/notifications/services.py).
 */
export const NotificationType = z.enum([
  'project_ready',
  'render_finished',
  'project_failed',
  'post_failed',
  'low_balance',
  'payment_approved',
  'payment_rejected',
]);
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
  /** Where it leads. A `post_failed` has its post (and the post's project); the one of a disconnected network with several posts has neither. */
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
  errors: [{ status: 404, when: 'El `X-Workspace-Id` no es un workspace del usuario.' }],
  backend: {
    status: 'exists',
    source: 'back/apps/notifications/views.py · NotificationListView + NotificationSerializer (apps/notifications/serializers.py)',
    note: 'Solo las del usuario en el workspace activo, para cualquier rol, de la más nueva a la más vieja. `unread_count` cuenta todas las no leídas. Las escribe `apps/notifications/services.py` cuando el cambio hace commit: listo para revisar, render terminado (completo o parcial) y proyecto fallido (con lo devuelto) para el dueño y los miembros activos del workspace; el render de un solo clip (desde su editor o un reintento) no avisa si sale bien y, si se devolvió entero, escribe un `render_finished` «<proyecto>: no se pudo renderizar un clip» con el texto de Actividad para ese render; saldo bajo (`low_balance`: una reserva dejó el saldo disponible por debajo de `low_balance_threshold`; una sola vez hasta que el saldo vuelve al umbral) para el dueño y los admins; pago aprobado o rechazado (`payment_approved`, `payment_rejected`) para quien lo inició. Publicación fallida (`post_failed`): una publicación programada terminó `FAILED` (la red la rechazó, no se sabe si salió, se agotaron los reintentos, no había cuenta, o falló el texto con IA de una que iba a salir sin texto), para el dueño y los miembros activos, una vez por falla (si se reintenta y vuelve a fallar, avisa de nuevo); el título nombra el clip y la red («<clip>: no se pudo publicar en TikTok», o «<clip>: no sabemos si se publicó en TikTok» para `outcome_unknown` y `duplicate_content`), el cuerpo es el `error_message` de la publicación y `post_id` lleva a ella en Publicaciones (`/publicaciones?detalle=<id>`). Al desconectar una red, si fallan varias publicaciones se escribe una sola («Se desconectó TikTok: 3 publicaciones no van a salir», sin `post_id`). Si fallan muchas seguidas en el workspace, después de la tercera en 10 minutos se escribe una sola más («Fallaron varias publicaciones en pocos minutos», sin `post_id`) y el resto de esos 10 minutos no avisa. Cada una, menos las de pagos, manda además su email a quien tiene prendido ese tipo en `/me/notification-preferences/` (`apps/notifications/emails.py`). Los links de esos emails llevan el workspace en `?espacio=<id>`: `RequireSession` cambia a ese workspace antes de mostrar la página. Se guardan las últimas 20 por usuario y workspace. `project_id` y `post_id` quedan en null si el proyecto o la publicación se borran. Sin workspace, la lista vacía.',
  },
  screens: ['J2', 'Barra'],
});

export const markNotificationRead = defineEndpoint({
  id: 'notifications.markRead',
  method: 'POST',
  path: '/api/v1/notifications/:id/read/',
  summary: 'Marca una notificación como leída.',
  response: Notification,
  errors: [{ status: 404, when: 'No existe, es de otra persona o de otro workspace.' }],
  backend: {
    status: 'exists',
    source: 'back/apps/notifications/views.py · NotificationReadView',
    note: 'Sin cuerpo. Solo la primera vez escribe `read_at`; repetirlo responde la misma notificación. Cualquier rol.',
  },
  screens: ['J2'],
});

export const markAllNotificationsRead = defineEndpoint({
  id: 'notifications.markAllRead',
  method: 'POST',
  path: '/api/v1/notifications/read-all/',
  summary: 'Marca todas como leídas.',
  response: NoContent,
  successStatus: 204,
  errors: [{ status: 404, when: 'El `X-Workspace-Id` no es un workspace del usuario.' }],
  backend: {
    status: 'exists',
    source: 'back/apps/notifications/views.py · NotificationReadAllView',
    note: 'Sin cuerpo. Solo toca las del usuario en el workspace activo que estaban sin leer. Cualquier rol.',
  },
  screens: ['J2'],
});

export const notificationsEndpoints = [listNotifications, markNotificationRead, markAllNotificationsRead] as const;
