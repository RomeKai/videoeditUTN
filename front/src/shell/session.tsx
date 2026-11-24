'use client';

import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from 'react';
import type { Plan } from '@/api/contracts/billing';
import type { Role } from '@/api/contracts/common';
import type { Me, WorkspaceSummary } from '@/api/contracts/users';
import { useHasSession, useMe, useSubscription, useUpdateMe } from '@/api/hooks';
import { getActiveWorkspaceId, setActiveWorkspaceId } from '@/api/session';
import { can as canDo, NO_ACCESS_REASON, type Action, type PermissionContext } from '@/lib/permissions';

export type SessionStatus = 'loading' | 'authenticated' | 'anonymous' | 'error';

export interface SessionValue {
  /** loading: there are tokens but `/me` has not answered yet. error: `/me` failed (network or server). */
  status: SessionStatus;
  me: Me | null;
  /** Active workspace, with the user's role in it. */
  workspace: WorkspaceSummary | null;
  role: Role | null;
  /** Changes the active workspace and reloads every workspace-scoped query (J3). */
  switchWorkspace: (workspaceId: string) => Promise<void>;
  /** Error of `/me` (network or server), so the shell can offer to retry. */
  error: unknown;
  refetch: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const hasSession = useHasSession();
  const meQuery = useMe();
  const updateMe = useUpdateMe();
  const queryClient = useQueryClient();
  const me = meQuery.data ?? null;

  const workspace = useMemo(() => {
    if (!me) return null;
    const stored = getActiveWorkspaceId();
    return (
      me.workspaces.find((item) => item.id === stored) ??
      me.workspaces.find((item) => item.id === me.active_workspace_id) ??
      me.workspaces[0] ??
      null
    );
  }, [me]);

  // Keep the header the client sends in line with the workspace the UI shows.
  useEffect(() => {
    if (workspace && getActiveWorkspaceId() !== workspace.id) setActiveWorkspaceId(workspace.id);
  }, [workspace]);

  const { mutateAsync: saveMe } = updateMe;
  const switchWorkspace = useCallback(
    async (workspaceId: string) => {
      setActiveWorkspaceId(workspaceId);
      try {
        await saveMe({ active_workspace_id: workspaceId });
      } finally {
        // Everything on screen belongs to the previous workspace.
        await queryClient.invalidateQueries();
      }
    },
    [queryClient, saveMe],
  );

  const value = useMemo<SessionValue>(() => {
    const status: SessionStatus = !hasSession ? 'anonymous' : me ? 'authenticated' : meQuery.isError ? 'error' : 'loading';
    return {
      status,
      me,
      workspace,
      role: workspace?.role ?? null,
      switchWorkspace,
      error: meQuery.error,
      refetch: () => void meQuery.refetch(),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasSession, me, workspace, switchWorkspace, meQuery.isError, meQuery.error]);

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error('useSession necesita <SessionProvider> (ya está en el layout raíz).');
  return value;
}

export interface Permissions {
  role: Role | null;
  /** Whether the current role may do the action (matrix of map 1.7). */
  can: (action: Action, context?: Omit<PermissionContext, 'userId'>) => boolean;
  /**
   * Props for a <Button>: `{ disabledReason: 'Pedile acceso a un admin' }` when
   * the role may not do the action, `{}` when it may. The button stays visible.
   */
  gate: (action: Action, context?: Omit<PermissionContext, 'userId'>) => { disabledReason?: string };
  /** The tooltip text of a blocked action. */
  reason: string;
}

/** Role of the user in the active workspace and what it allows. Use it to disable actions, never to hide them. */
export function usePermissions(): Permissions {
  const { role, me } = useSession();
  return useMemo(() => {
    const can: Permissions['can'] = (action, context = {}) => canDo(role, action, { ...context, userId: me?.id });
    return {
      role,
      can,
      gate: (action, context) => (can(action, context) ? {} : { disabledReason: NO_ACCESS_REASON }),
      reason: NO_ACCESS_REASON,
    };
  }, [role, me?.id]);
}

export interface PlanInfo {
  plan: Plan | null;
  isLoading: boolean;
  /** Feature flags of the plan, false while loading. */
  allows: (feature: 'scheduling' | 'crossposting' | 'seo') => boolean;
}

/** Plan of the active workspace (map 1.6). What it does not include is shown locked with <PlanLock>, never hidden. */
export function usePlan(): PlanInfo {
  const { status } = useSession();
  const subscription = useSubscription({ enabled: status === 'authenticated' });
  const plan = subscription.data?.plan ?? null;
  return useMemo(
    () => ({
      plan,
      isLoading: subscription.isLoading,
      allows: (feature) =>
        plan ? (feature === 'scheduling' ? plan.allow_scheduling : feature === 'crossposting' ? plan.allow_crossposting : plan.has_seo_optimization) : false,
    }),
    [plan, subscription.isLoading],
  );
}

const WELCOME_KEY = 'oc.bienvenida.vista';

/**
 * First visit (map B1): the account has not finished the tour, has no project
 * and has not gone through (or skipped) the welcome in this browser.
 */
export function needsWelcome(me: Me): boolean {
  if (me.tour_completed || me.onboarding?.has_project || me.onboarding_use_case) return false;
  try {
    return window.localStorage.getItem(WELCOME_KEY) !== me.id;
  } catch {
    return false;
  }
}

/** Call when the welcome is finished or skipped, so the app stops sending the user there. */
export function markWelcomeSeen(userId: string): void {
  try {
    window.localStorage.setItem(WELCOME_KEY, userId);
  } catch {
    // Private mode: nothing to remember.
  }
}
