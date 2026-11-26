import * as contract from '../../contracts/users';
import { badRequest, route } from '../http';
import { serializeMe, workspacesOf } from '../serialize';
import { mockTokens } from '../state';

export const usersHandlers = [
  route(contract.getMe, ({ db, user }) => ({ data: serializeMe(db, user) })),

  route(contract.updateMe, ({ db, user, body }) => {
    if (body.email === null || (body.email !== undefined && body.email.toLowerCase() === user.email.toLowerCase())) {
      // Back to the current email: the pending change and its link are cancelled.
      user.pending_email = null;
    } else if (body.email !== undefined) {
      if (!body.current_password) throw badRequest({ current_password: ['Escribí tu contraseña para cambiar el email.'] });
      if (body.current_password === 'incorrecta') throw badRequest({ current_password: ['La contraseña no coincide.'] });
      if (db.users.some((item) => item.id !== user.id && item.email.toLowerCase() === body.email?.toLowerCase())) {
        throw badRequest({ email: ['Ya existe una cuenta con este email.'] });
      }
      // The new address has to be verified before it replaces the current one.
      user.pending_email = body.email;
    }
    if (body.name !== undefined) user.name = body.name;
    if (body.tour_completed !== undefined) user.tour_completed = body.tour_completed;
    if (body.onboarding_use_case !== undefined) user.onboarding_use_case = body.onboarding_use_case;
    if (body.active_workspace_id !== undefined) {
      if (!workspacesOf(db, user.id).some((item) => item.workspace.id === body.active_workspace_id)) {
        throw badRequest({ active_workspace_id: ['No sos miembro de ese workspace.'] });
      }
      user.active_workspace_id = body.active_workspace_id;
    }
    return { data: serializeMe(db, user) };
  }),

  // The other sessions end; this one gets a new pair (the mock cannot revoke the old tokens).
  route(contract.changePassword, ({ user, body }) => {
    if (body.current_password === 'incorrecta') throw badRequest({ current_password: ['La contraseña actual no coincide.'] });
    if (/^\d+$/.test(body.new_password)) throw badRequest({ new_password: ['La contraseña no puede ser solo números.'] });
    return { data: mockTokens(user.id) };
  }),

  route(contract.getNotificationPreferences, ({ user }) => ({ data: user.notification_preferences })),

  route(contract.updateNotificationPreferences, ({ user, body }) => {
    user.notification_preferences = { ...user.notification_preferences, ...body };
    return { data: user.notification_preferences };
  }),
];
