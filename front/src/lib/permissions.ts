import type { Role } from '@/api/contracts/common';

/**
 * Permission matrix of map 1.7. The API does not enforce roles yet (gap 3): this
 * is what the UI uses to disable actions. What a role cannot do stays visible,
 * disabled, with the tooltip "Pedile acceso a un admin" (except the admin
 * sections of Ajustes, which are not shown to Editor and Lector).
 */
export type Action =
  | 'project.view'
  | 'clip.download'
  | 'project.create'
  | 'project.review'
  | 'project.rename'
  | 'project.delete'
  | 'clip.edit'
  | 'post.publish'
  | 'brandkit.manage'
  | 'social.manage'
  | 'members.manage'
  | 'wallet.view'
  | 'billing.manage'
  | 'workspace.rename'
  | 'workspace.delete'
  | 'workspace.transfer';

const ALL: Role[] = ['owner', 'admin', 'editor', 'viewer'];
const MAKERS: Role[] = ['owner', 'admin', 'editor'];
const ADMINS: Role[] = ['owner', 'admin'];
const OWNER: Role[] = ['owner'];

const MATRIX: Record<Action, Role[]> = {
  'project.view': ALL,
  'clip.download': ALL,
  'project.create': MAKERS,
  'project.review': MAKERS,
  'project.rename': MAKERS,
  'project.delete': MAKERS,
  'clip.edit': MAKERS,
  'post.publish': MAKERS,
  'brandkit.manage': MAKERS,
  'social.manage': ADMINS,
  'members.manage': ADMINS,
  'wallet.view': ALL,
  'billing.manage': ADMINS,
  'workspace.rename': ADMINS,
  'workspace.delete': OWNER,
  'workspace.transfer': OWNER,
};

export interface PermissionContext {
  /** Id of the current user, for "Editor: solo los propios". */
  userId?: string | null;
  /** Who created the resource (`uploaded_by`). */
  ownerId?: string | null;
}

export const NO_ACCESS_REASON = 'Pedile acceso a un admin';

export function can(role: Role | null | undefined, action: Action, context: PermissionContext = {}): boolean {
  if (!role) return false;
  if (!MATRIX[action].includes(role)) return false;
  // An Editor deletes only the projects they created.
  if (action === 'project.delete' && role === 'editor') {
    return Boolean(context.userId && context.ownerId && context.userId === context.ownerId);
  }
  return true;
}

export const ROLE_LABEL: Record<Role, string> = { owner: 'Dueño', admin: 'Admin', editor: 'Editor', viewer: 'Lector' };

export const ROLE_DESCRIPTION: Record<Exclude<Role, 'owner'>, string> = {
  admin: 'Admin: todo lo del Editor, más miembros, redes, plan y compras.',
  editor: 'Editor: crea proyectos, revisa, renderiza y publica. No administra miembros, redes ni facturación.',
  viewer: 'Lector: ve proyectos, clips y publicaciones, y descarga clips. No gasta tokens.',
};
