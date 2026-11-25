'use client';

import { usePathname, useRouter } from 'next/navigation';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

/**
 * The shell's leave guard (D8 · Cambios sin guardar).
 *
 * The App Router cannot block a navigation, so there is one place that knows whether the screen has
 * unsaved work, and everything that navigates asks it first:
 * - a screen registers with `useLeaveGuard(dirty)` while it has changes, and shows its own dialog;
 * - the shell (command menu, notifications, workspace switcher, sign out) navigates through
 *   `useGuardedNavigation()`;
 * - clicks on in-app links are caught at the document, before next/link starts the navigation;
 * - Back is caught with one extra history entry for the same page: going back lands on the real
 *   one, which is still this screen, and the question is asked there. Being the last entry, there
 *   is no Forward to guard;
 * - closing or reloading the tab gets the browser's own prompt.
 */

type RouterLike = Pick<ReturnType<typeof useRouter>, 'push' | 'replace'>;

/**
 * Something to do once the person agrees to lose the changes. A promise that rejects puts the guard
 * back; the error itself is the caller's to show.
 */
export type LeaveAction = () => void | Promise<unknown>;

/** Where the person was going when the guard stopped them. */
type Pending = { kind: 'href'; href: string } | { kind: 'back' } | { kind: 'action'; run: LeaveAction };

/** A screen with unsaved changes, as the guard sees it. */
export interface GuardEntry {
  /** Links that never ask (the work is kept somewhere else). */
  allow: (href: string) => boolean;
  /** The person chose "Descartar". */
  onDiscard: () => void;
  /** Opens or closes the screen's dialog. */
  setAsking: (asking: boolean) => void;
  /** The guard let the person go: the screen must not register again until it changes. */
  bypassed: () => void;
  /** What came after "Descartar" failed and the screen is still here: it is guarded again. */
  restore: () => void;
}

/** Marks the extra history entry, next to the router's own state. */
const TOKEN_KEY = '__ocLeaveGuard';

/**
 * Going back one entry is asynchronous. The guard waits for the browser to say it did it; this is
 * only so that a `popstate` that never comes cannot leave the app unable to navigate.
 */
export const POP_SAFETY_MS = 5000;

const pathOf = (href: string): string | null => {
  try {
    return new URL(href, window.location.href).pathname;
  } catch {
    return null;
  }
};

export class LeaveGuard {
  router: RouterLike | null = null;
  private readonly guards = new Set<GuardEntry>();
  private pending: Pending | null = null;
  private asker: GuardEntry | null = null;
  /** The extra history entry, while it is in place. */
  private armed: { token: string; pathname: string; length: number } | null = null;
  /** The guard is taking its own entry away and the browser has not confirmed it yet. */
  private pop: { promise: Promise<void>; resolve: () => void; timer: number } | null = null;
  /** The confirmation did not come in time. `navigated`: something navigated meanwhile. */
  private overdue: { navigated: boolean } | null = null;
  private tokens = 0;

  /** True while some screen has unsaved changes. */
  get active(): boolean {
    return this.guards.size > 0;
  }

  /* ── Screens ─────────────────────────────────────────────────────────── */

  register(entry: GuardEntry): () => void {
    this.guards.add(entry);
    this.arm();
    return () => {
      if (!this.drop(entry)) return;
      // Not right away: an effect that runs again registers in the same commit, and the entry stays.
      queueMicrotask(() => {
        if (this.guards.size === 0) void this.disarm();
      });
    };
  }

  /**
   * The screen saved (or leaves on purpose): it stops being guarded now. Resolves when the history
   * is as the person left it, so the navigation that follows lands where it should.
   */
  release(entry: GuardEntry): Promise<void> {
    this.drop(entry);
    return this.guards.size === 0 ? this.disarm() : this.settled();
  }

  private drop(entry: GuardEntry): boolean {
    if (!this.guards.delete(entry)) return false;
    if (this.asker === entry) {
      this.asker = null;
      this.pending = null;
      entry.setAsking(false);
    }
    return true;
  }

  /* ── Navigations ─────────────────────────────────────────────────────── */

  /** `router.push` through the guard: asks first when there are changes. */
  push(href: string): void {
    if (this.holds(href)) this.ask({ kind: 'href', href });
    else this.go(() => this.router?.push(href));
  }

  /** Runs something that leaves the screen without a URL of its own (switching workspace, signing out). */
  confirm(run: LeaveAction): void {
    if (this.guards.size > 0) this.ask({ kind: 'action', run });
    else this.go(run);
  }

  /** Navigates once the history is settled. Never asks. */
  go(run: LeaveAction): void {
    if (this.pop) {
      void this.pop.promise.then(() => this.go(run));
      return;
    }
    if (this.overdue) this.overdue.navigated = true;
    const result = run();
    // Whoever started it shows its own error; here a failure only means "did not leave".
    if (result instanceof Promise) result.catch(() => undefined);
  }

  private holds(href: string): boolean {
    if (this.guards.size === 0) return false;
    // Same page (hash or query only): nothing is lost.
    if (pathOf(href) === window.location.pathname) return false;
    for (const guard of this.guards) if (!guard.allow(href)) return true;
    return false;
  }

  private ask(pending: Pending): void {
    this.pending = pending;
    if (this.asker) return;
    // The last screen to register is the one on top.
    this.asker = [...this.guards].pop() ?? null;
    this.asker?.setAsking(true);
  }

  /** "Seguir editando". */
  stay = (): void => {
    this.pending = null;
    this.asker?.setAsking(false);
    this.asker = null;
  };

  /** "Descartar": goes where the person was going. */
  discard = (): void => {
    const target = this.pending;
    this.stay();
    if (!target) return;
    const guards = [...this.guards];
    this.guards.clear();
    for (const guard of guards) {
      guard.onDiscard();
      guard.bypassed();
    }
    if (target.kind === 'back') {
      // Back was held on the real entry and the extra one is on top again: two steps leave the screen.
      const steps = this.atArmedEntry() ? -2 : -1;
      this.armed = null;
      window.history.go(steps);
      return;
    }
    // The extra entry goes first, so Back from the next screen returns here once, not twice.
    void this.disarm().then(() =>
      this.go(() => {
        if (target.kind === 'href') return void this.router?.push(target.href);
        const result = target.run();
        if (result instanceof Promise) {
          // Still here and it failed (the workspace did not change): the changes are guarded again.
          result.catch(() => guards.forEach((guard) => guard.restore()));
        }
        return result;
      }),
    );
  };

  /* ── The extra history entry ─────────────────────────────────────────── */

  private atArmedEntry(): boolean {
    const armed = this.armed;
    if (!armed) return false;
    const state = window.history.state as Record<string, unknown> | null;
    if (state?.[TOKEN_KEY] === armed.token) return true;
    // The router rewrote the entry's state (a refresh, a query change): it is still the last one of this page.
    return window.location.pathname === armed.pathname && window.history.length === armed.length;
  }

  private arm(): void {
    if (this.pop) {
      // The previous entry is still on its way out: one thing at a time.
      void this.pop.promise.then(() => {
        if (this.guards.size > 0) this.arm();
      });
      return;
    }
    if (this.atArmedEntry()) return;
    const token = `${Date.now()}-${++this.tokens}`;
    // The entry carries the router's own state, so the router sees the same page.
    window.history.pushState({ ...(window.history.state as object | null), [TOKEN_KEY]: token }, '', window.location.href);
    this.armed = { token, pathname: window.location.pathname, length: window.history.length };
  }

  /** Takes the extra entry away, and resolves once the browser is back on the real one. */
  private disarm(): Promise<void> {
    if (this.pop) return this.pop.promise;
    // Something navigated on top of it (a link that never asks): going back now would undo that.
    if (!this.atArmedEntry()) {
      this.armed = null;
      return Promise.resolve();
    }
    this.armed = null;
    let resolve: () => void = () => undefined;
    const promise = new Promise<void>((done) => (resolve = done));
    const timer = window.setTimeout(() => {
      this.pop = null;
      this.overdue = { navigated: false };
      resolve();
    }, POP_SAFETY_MS);
    this.pop = { promise, resolve, timer };
    window.history.back();
    return promise;
  }

  private settled(): Promise<void> {
    return this.pop?.promise ?? Promise.resolve();
  }

  /* ── Browser events ──────────────────────────────────────────────────── */

  private readonly onPopState = (): void => {
    if (this.pop) {
      const { resolve, timer } = this.pop;
      window.clearTimeout(timer);
      this.pop = null;
      resolve();
      return;
    }
    if (this.overdue) {
      // The step back came after all, on top of a navigation that had stopped waiting for it: it is undone.
      const { navigated } = this.overdue;
      this.overdue = null;
      if (navigated) window.history.forward();
      return;
    }
    const armed = this.armed;
    if (!armed || this.guards.size === 0) return;
    const state = window.history.state as Record<string, unknown> | null;
    // Still on the extra entry (the person came back to it from a page opened on top).
    if (state?.[TOKEN_KEY] === armed.token) return;
    this.armed = null;
    // Somewhere else: nothing to hold on to.
    if (window.location.pathname !== armed.pathname) return;
    // Back, onto the real entry of this screen. The extra one goes in again, so another Back is held too.
    this.arm();
    this.ask({ kind: 'back' });
  };

  private readonly onClick = (event: MouseEvent): void => {
    if (this.guards.size === 0 && !this.pop) return;
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const anchor = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
    if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin || url.pathname === window.location.pathname) return;
    const href = `${url.pathname}${url.search}${url.hash}`;
    const held = this.holds(href);
    // Nothing to ask, and the history is settled: the link goes on as usual.
    if (!held && !this.pop) return;
    event.preventDefault();
    event.stopPropagation();
    if (held) this.ask({ kind: 'href', href });
    else this.go(() => this.router?.push(href));
  };

  private readonly onBeforeUnload = (event: BeforeUnloadEvent): void => {
    if (this.guards.size === 0) return;
    event.preventDefault();
    // Chrome needs returnValue set to show the prompt.
    event.returnValue = '';
  };

  attach(): () => void {
    // Capture: before next/link starts the navigation.
    document.addEventListener('click', this.onClick, true);
    window.addEventListener('popstate', this.onPopState);
    window.addEventListener('beforeunload', this.onBeforeUnload);
    return () => {
      document.removeEventListener('click', this.onClick, true);
      window.removeEventListener('popstate', this.onPopState);
      window.removeEventListener('beforeunload', this.onBeforeUnload);
      if (this.pop) window.clearTimeout(this.pop.timer);
    };
  }
}

const LeaveGuardContext = createContext<LeaveGuard | null>(null);

/** One guard for the whole app. In the root providers, above everything that navigates. */
export function LeaveGuardProvider({ children }: { children: ReactNode }) {
  const [guard] = useState(() => new LeaveGuard());
  const router = useRouter();
  guard.router = router;
  useEffect(() => guard.attach(), [guard]);
  return <LeaveGuardContext.Provider value={guard}>{children}</LeaveGuardContext.Provider>;
}

export interface GuardedNavigation {
  /** `router.push`, asking first when the screen has unsaved changes. */
  push: (href: string) => void;
  /** Runs an action that leaves the screen (switch workspace, sign out), asking first when there are changes. */
  confirm: (run: LeaveAction) => void;
}

/** How the shell navigates from code. Outside the provider (a component rendered alone) it is the plain router. */
export function useGuardedNavigation(): GuardedNavigation {
  const guard = useContext(LeaveGuardContext);
  const router = useRouter();
  return useMemo(
    () =>
      guard
        ? { push: (href) => guard.push(href), confirm: (run) => guard.confirm(run) }
        : { push: (href) => router.push(href), confirm: (run) => void run() },
    [guard, router],
  );
}

export interface LeaveGuardOptions {
  /** Links that never ask: the round trip to buy tokens keeps the work. */
  allow?: (href: string) => boolean;
  /** Runs when the person chooses "Descartar", before leaving. */
  onDiscard?: () => void;
}

export interface ScreenLeaveGuard {
  /** Props for <UnsavedChangesDialog>. */
  dialog: { open: boolean; onStay: () => void; onDiscard: () => void };
  /** Leaves through the guard: asks first when there are changes. */
  leave: (href: string) => void;
  /** Leaves without asking (after saving, or "Descartar cambios"). `replace` keeps this screen out of the history. */
  leaveNow: (href: string, options?: { replace?: boolean }) => void;
  /**
   * After saving or deleting: stops guarding and takes the extra history entry away. Resolves when
   * the history is as the person left it, so the navigation that follows lands where it should.
   */
  release: () => Promise<void>;
}

/** D8 for a screen with unsaved work: registers it in the shell's guard while `dirty`. */
export function useLeaveGuard(dirty: boolean, { allow, onDiscard }: LeaveGuardOptions = {}): ScreenLeaveGuard {
  const guard = useContext(LeaveGuardContext);
  if (!guard) throw new Error('useLeaveGuard necesita <LeaveGuardProvider> (ya está en el layout raíz).');
  const router = useRouter();
  const pathname = usePathname();
  const [asking, setAsking] = useState(false);
  /** Bumped when the guard comes back after a failed leave. */
  const [epoch, setEpoch] = useState(0);
  const allowRef = useRef(allow);
  allowRef.current = allow;
  const onDiscardRef = useRef(onDiscard);
  onDiscardRef.current = onDiscard;
  /** Set while leaving on purpose. Lasts until the screen changes: the next one (the kit just created) is guarded again. */
  const bypass = useRef(false);
  const lastPath = useRef(pathname);
  if (lastPath.current !== pathname) {
    lastPath.current = pathname;
    bypass.current = false;
  }

  const [entry] = useState<GuardEntry>(() => ({
    allow: (href) => allowRef.current?.(href) ?? false,
    onDiscard: () => onDiscardRef.current?.(),
    setAsking,
    bypassed: () => {
      bypass.current = true;
    },
    restore: () => {
      bypass.current = false;
      setEpoch((value) => value + 1);
    },
  }));

  useEffect(() => {
    if (!dirty || bypass.current) return;
    return guard.register(entry);
  }, [dirty, entry, guard, pathname, epoch]);

  const release = useCallback(() => {
    bypass.current = true;
    return guard.release(entry);
  }, [entry, guard]);
  const leaveNow = useCallback(
    (href: string, options: { replace?: boolean } = {}) => {
      void release().then(() => guard.go(() => (options.replace ? router.replace(href) : router.push(href))));
    },
    [guard, release, router],
  );
  const leave = useCallback((href: string) => guard.push(href), [guard]);

  return useMemo(
    () => ({ dialog: { open: asking, onStay: guard.stay, onDiscard: guard.discard }, leave, leaveNow, release }),
    [asking, guard, leave, leaveNow, release],
  );
}
