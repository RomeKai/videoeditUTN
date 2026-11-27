import * as contract from '../../contracts/notifications';
import type { MockNotification } from '../db';
import { notFound, route } from '../http';

function strip({ workspace, user, ...notification }: MockNotification) {
  void workspace;
  void user;
  return notification;
}

export const notificationsHandlers = [
  route(contract.listNotifications, ({ db, user, workspace }) => {
    const mine = db.notifications
      .filter((item) => item.user === user.id && item.workspace === workspace.id)
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .slice(0, 20);
    return { data: { unread_count: mine.filter((item) => item.read_at === null).length, results: mine.map(strip) } };
  }),

  route(contract.markNotificationRead, ({ db, user, workspace, params, now }) => {
    // Like the backend: one of another workspace is a 404 too.
    const notification = db.notifications.find((item) => item.id === params.id && item.user === user.id && item.workspace === workspace.id);
    if (!notification) throw notFound();
    notification.read_at ??= new Date(now).toISOString();
    return { data: strip(notification) };
  }),

  route(contract.markAllNotificationsRead, ({ db, user, workspace, now }) => {
    for (const item of db.notifications) {
      if (item.user === user.id && item.workspace === workspace.id) item.read_at ??= new Date(now).toISOString();
    }
    return { data: undefined };
  }),
];
