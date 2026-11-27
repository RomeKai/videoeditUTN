'use client';

import { useQueryClient } from '@tanstack/react-query';
import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useLayoutEffect, useMemo, useRef, type ReactNode } from 'react';
import type { Plan } from '@/api/contracts/billing';
import type { Role } from '@/api/contracts/common';
import type { Me, WorkspaceSummary } from '@/api/contracts/users';
import { isScopedTo, isUnscoped, useActiveWorkspaceId, useMe, useSessionPresence, useSubscription, useUpdateMe } from '@/api/hooks';
import { SessionExpiredError } from '@/api/errors';
import { getActiveWorkspaceId, setActiveWorkspaceId } from '@/api/session';
import { useGuardedNavigation } from './leaveGuard';
import { can as canDo, NO_ACCESS_REASON, type Action, type PermissionContext } from '@/lib/permissions';

export type SessionStatus = 'loading' | 'authenticated' | 'anonymous' | 'error';

export interface SwitchOptions {
  /** Where the person lands. Null: the caller navigates (or the screen works in any workspace). Default `/proyectos`. */
  to?: string | null;
}

/** How long a switch waits to be on its destination before changing the workspace anyway. */
const ARRIVAL_TIMEOUT_MS = 5000;

export interface SessionValue {
  /** loading: not known yet (hydration), or there are tokens but `/me` has not answered. Never redirect on it. error: `/me` failed (network or server). */
  status: SessionStatus;
  me: Me | null;
  /** Active workspace, with the user's role in it. */
  workspace: WorkspaceSummary | null;
  role: Role | null;
  /**
   * Changes the active workspace (J3). It asks first when the screen has unsaved changes, takes
   * the person to `to` (Proyectos unless told otherwise) and changes the workspace on arrival, so
   * no screen is left asking for its data in a workspace that does not have it. The UI follows at
   * once, also when `/me` comes back unchanged. Use it too after accepting an invitation or
   * creating a workspace (the id may not be in `me` yet).
   * Resolves to false when nothing changed: the person chose to stay, or a newer switch took over.
   */
  switchWorkspace: (workspaceId: string, options?: SwitchOptions) => Promise<boolean>;
  /** Error of `/me` (network or server), so the shell can offer to retry. */
  error: unknown;
  refetch: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  // undefined until the browser storage can be read (server render and hydration).
  const hasSession = useSessionPresence();
  const meQuery = useMe();
  const updateMe = useUpdateMe();
  const queryClient = useQueryClient();
  const me = meQuery.data ?? null;

  // The id the client sends in X-Workspace-Id. Subscribed, not read once: switching (or accepting an
  // invitation) changes it before `/me` is refetched, and `/me` may come back identical.
  const stored = useActiveWorkspaceId();

  const workspace = useMemo(() => {
    if (!me) return null;
    return (
      me.workspaces.find((item) => item.id === stored) ??
      me.workspaces.find((item) => item.id === me.active_workspace_id) ??
      me.workspaces[0] ??
      null
    );
  }, [me, stored]);

  // Keep the header the client sends in line with the workspace the UI shows.
  // A stored id that `/me` does not have (the workspace was deleted, or the person was removed from it)
  // is replaced by the one shown; with no workspace at all it is cleared, so no request names a
  // workspace that answers 404.
  useEffect(() => {
    if (workspace && getActiveWorkspaceId() !== workspace.id) setActiveWorkspaceId(workspace.id);
    else if (me && me.workspaces.length === 0 && getActiveWorkspaceId() !== null) setActiveWorkspaceId(null);
  }, [me, workspace]);

  const { mutateAsync: saveMe } = updateMe;
  const navigation = useGuardedNavigation();
  const router = useRouter();
  const pathname = usePathname();
  const pathRef = useRef(pathname);
  /** Switches waiting to be on their destination: each one changes the workspace as it gets there. */
  const arrivals = useRef(new Set<{ path: string; arrive: () => void }>());
  // Layout, not passive: the workspace changes in the same frame the destination appears, before its
  // queries start. The screen never asks for (or paints) the previous workspace's data.
  useLayoutEffect(() => {
    pathRef.current = pathname;
    for (const waiting of [...arrivals.current]) {
      if (waiting.path !== pathname) continue;
      arrivals.current.delete(waiting);
      waiting.arrive();
    }
  }, [pathname]);
  const workspaceIds = useRef<string[]>([]);
  workspaceIds.current = me?.workspaces.map((item) => item.id) ?? [];

  // One number per switch: only the latest one writes when its request ends.
  const switchGeneration = useRef(0);
  const switchWorkspace = useCallback(
    async (workspaceId: string, { to = '/proyectos' }: SwitchOptions = {}) => {
      // With unsaved changes on screen the person is asked first: after the switch that screen is gone.
      if (!(await navigation.request())) return false;
      const generation = ++switchGeneration.current;
      const latest = () => generation === switchGeneration.current;
      const previous = getActiveWorkspaceId();
      const apply = () => {
        if (!latest()) return;
        // What the cache has of the new workspace is from an earlier visit: it is read again on arrival.
        void queryClient.invalidateQueries({ predicate: isScopedTo(workspaceId), refetchType: 'none' });
        setActiveWorkspaceId(workspaceId);
      };

      // The screen goes first. Changing the workspace under a detail screen makes it ask for its
      // record in the new workspace, which answers 404: the person would see "Esta página no existe"
      // on the way out.
      const target = to === null ? null : new URL(to, window.location.href);
      const destination = target?.pathname ?? null;
      if (to !== null && target !== null && destination === pathRef.current && target.search !== window.location.search) {
        // Same page, other query (`/proyectos?publicar=<clip>`): what the query opened belongs to the
        // previous workspace too. The URL changes when the router commits, with that panel already closed.
        router.push(to);
        const deadline = Date.now() + ARRIVAL_TIMEOUT_MS;
        while (window.location.search !== target.search && Date.now() < deadline) await new Promise((resolve) => window.setTimeout(resolve, 16));
        apply();
      } else if (to !== null && destination !== pathRef.current) {
        await new Promise<void>((resolve) => {
          const waiting = {
            path: destination as string,
            arrive: () => {
              window.clearTimeout(timer);
              apply();
              resolve();
            },
          };
          // A navigation that never lands (something else took over) must not leave the switch undone.
          const timer = window.setTimeout(() => {
            arrivals.current.delete(waiting);
            waiting.arrive();
          }, ARRIVAL_TIMEOUT_MS);
          arrivals.current.add(waiting);
          router.push(to);
        });
      } else {
        apply();
      }
      if (!latest()) return false;

      try {
        await saveMe({ active_workspace_id: workspaceId });
      } catch {
        // The switch already happened for this browser: every request says its workspace in
        // X-Workspace-Id. Only the "last workspace" kept in the profile is not up to date.
      }
      // An answer that comes after a newer switch must not take the person back to this workspace.
      if (!latest()) return false;
      // Again: while the workspace was not in `/me` yet (just created, invitation just accepted)
      // the effect above put the previous one back.
      setActiveWorkspaceId(workspaceId);
      // Nothing on screen reads the previous workspace any more: its entries go, so coming back reads it fresh.
      if (previous && previous !== workspaceId) queryClient.removeQueries({ predicate: isScopedTo(previous) });
      // What belongs to no workspace (the session, with the list of workspaces and the balance of each)
      // is read again. The new workspace's own queries started when the id changed: not twice.
      await queryClient.invalidateQueries({ predicate: isUnscoped([...workspaceIds.current, workspaceId, ...(previous ? [previous] : [])]) });
      return latest();
    },
    [navigation, queryClient, router, saveMe],
  );

  const value = useMemo<SessionValue>(() => {
    const status: SessionStatus =
      hasSession === undefined
        ? 'loading'
        : !hasSession
          ? 'anonymous'
          : me
            ? 'authenticated'
            : // The session expired before `/me` ever answered: the "Sesión vencida" dialog is up, not a server failure.
              meQuery.isError && !(meQuery.error instanceof SessionExpiredError)
              ? 'error'
              : 'loading';
    return {
      status,
      // A stored session that turned out to be someone else's cache is never shown as signed in.
      me: hasSession ? me : null,
      workspace: hasSession ? workspace : null,
      role: hasSession ? (workspace?.role ?? null) : null,
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
  /** Feature flags of the plan, false while loading. A flag the backend does not send yet counts as allowed (it decides). */
  allows: (feature: PlanFeature) => boolean;
}

export type PlanFeature = 'scheduling' | 'crossposting' | 'seo' | 'social';
const PLAN_FLAG = {
  scheduling: 'allow_scheduling',
  crossposting: 'allow_crossposting',
  seo: 'has_seo_optimization',
  social: 'allow_social_accounts',
} as const satisfies Record<PlanFeature, keyof Plan>;

/** Plan of the active workspace (map 1.6). What it does not include is shown locked with <PlanLock>, never hidden. */
export function usePlan(): PlanInfo {
  const { status } = useSession();
  const subscription = useSubscription({ enabled: status === 'authenticated' });
  const plan = subscription.data?.plan ?? null;
  return useMemo(
    () => ({
      plan,
      isLoading: subscription.isLoading,
      allows: (feature) => (plan ? plan[PLAN_FLAG[feature]] !== false : false),
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
