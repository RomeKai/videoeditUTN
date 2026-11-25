'use client';

import { usePathname, useRouter } from 'next/navigation';
import { setWorkspaceHold } from '@/api/session';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

/**
 * The shell's leave guard (D8 · Cambios sin guardar).
 *
 * The App Router cannot block a navigation, so there is one place that knows whether the screen has
 * unsaved work, and everything that navigates asks it first:
 * - a screen registers with `useLeaveGuard(dirty)` while it has changes, and shows its own dialog;
 * - the shell (command menu, notifications, workspace switcher, sign out) navigates through
 *   `useGuardedNavigation()`;
 * - while a screen is registered this tab keeps its workspace, whatever another tab switches to;
 * - clicks on in-app links are caught at the document, before next/link starts the navigation;
 * - Back is caught with one extra history entry for the same page: going back lands on the real
 *   one, which is still this screen, and the question is asked there. An extra entry left from
 *   before (a reload, a round trip through a link that never asks) is taken again instead of
 *   stacking another, and a tab that starts on the screen gets none: Back has nowhere to go;
 * - closing or reloading the tab gets the browser's own prompt.
 */

type RouterLike = Pick<ReturnType<typeof useRouter>, 'push' | 'replace'>;

/**
 * Something to do once the person agrees to lose the changes. A promise that rejects puts the guard
 * back; the error itself is the caller's to show.
 */
export type LeaveAction = () => void | Promise<unknown>;

/** Where the person was going when the guard stopped them. */
type Pending =
  /** `anchor`: the link that was clicked, to follow it as its own component would (its `replace`, its `scroll`). */
  | { kind: 'href'; href: string; anchor?: HTMLAnchorElement }
  | { kind: 'back' }
  /** `cancel`: the person chose to stay. */
  | { kind: 'action'; run: LeaveAction; cancel?: () => void };

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
/** After "Descartar" on Back: how long the browser gets to leave the screen before it is guarded again. */
export const LEAVE_SAFETY_MS = 1500;

const pathOf = (href: string): string | null => {
  try {
    return new URL(href, window.location.href).pathname;
  } catch {
    return null;
  }
};

/** The token of the entry the browser is on, when it is one this guard added (now, or before a reload). */
const tokenHere = (): string | null => {
  const token = (window.history.state as Record<string, unknown> | null)?.[TOKEN_KEY];
  return typeof token === 'string' ? token : null;
};

/**
 * How many entries are before this one in the tab's history. Exact where the browser tells
 * (Navigation API); elsewhere only an upper bound, from the number of entries.
 */
function entriesBefore(): { count: number; exact: boolean } {
  const index = (window as { navigation?: { currentEntry?: { index?: number } | null } }).navigation?.currentEntry?.index;
  return typeof index === 'number' && index >= 0 ? { count: index, exact: true } : { count: window.history.length - 1, exact: false };
}

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
  private overdue: { navigated: boolean; timer: number } | null = null;
  /** "Descartar" after Back: the browser is stepping out of the screen at `pathname`. */
  private leaving: { pathname: string; guards: GuardEntry[]; timer: number } | null = null;
  /** Counts what went through `go()`: a failure of an older one must not put the guard back over a newer one. */
  private navigations = 0;
  private tokens = 0;

  /** True while some screen has unsaved changes. */
  get active(): boolean {
    return this.guards.size > 0;
  }

  /* ── Screens ─────────────────────────────────────────────────────────── */

  register(entry: GuardEntry): () => void {
    this.guards.add(entry);
    this.holdWorkspace();
    this.arm();
    return () => {
      if (!this.drop(entry)) return;
      // Not right away: an effect that runs again registers in the same commit, and the entry stays.
      queueMicrotask(() => this.afterDrop());
    };
  }

  /**
   * The screen saved (or leaves on purpose): it stops being guarded now. Resolves when the history
   * is as the person left it, so the navigation that follows lands where it should.
   */
  release(entry: GuardEntry): Promise<void> {
    this.drop(entry);
    return this.afterDrop();
  }

  private drop(entry: GuardEntry): boolean {
    if (!this.guards.delete(entry)) return false;
    if (this.asker === entry) {
      // The question stays open: `afterDrop` hands it to another screen with changes, if there is one.
      this.asker = null;
      entry.setAsking(false);
    }
    this.holdWorkspace();
    return true;
  }

  private afterDrop(): Promise<void> {
    if (this.guards.size === 0) {
      // Nobody is left to lose anything, and nobody to answer: the held navigation is dropped with the question.
      this.cancelPending();
      return this.disarm();
    }
    if (this.pending && !this.asker) this.openQuestion();
    return this.settled();
  }

  /** While a screen has changes this tab keeps its workspace, whatever another tab switches to. */
  private holdWorkspace(): void {
    setWorkspaceHold(this.guards.size > 0);
  }

  /* ── Navigations ─────────────────────────────────────────────────────── */

  /** `router.push` through the guard: asks first when there are changes. */
  push(href: string): void {
    if (this.holds(href)) this.ask({ kind: 'href', href });
    else this.go(() => this.router?.push(href));
  }

  /** Runs something that leaves the screen without a URL of its own (signing out), asking first when there are changes. */
  confirm(run: LeaveAction): void {
    if (this.guards.size > 0) this.ask({ kind: 'action', run });
    else this.go(run);
  }

  /**
   * Asks to leave the screen. Resolves to true when the way is clear (at once without changes;
   * after "Descartar", with the history settled) and to false when the person stays.
   */
  request(): Promise<boolean> {
    return new Promise((resolve) => {
      const run = () => resolve(true);
      if (this.guards.size > 0) this.ask({ kind: 'action', run, cancel: () => resolve(false) });
      else this.go(run);
    });
  }

  /** Navigates once the history is settled. Never asks. */
  go(run: LeaveAction): void {
    if (this.pop) {
      void this.pop.promise.then(() => this.go(run));
      return;
    }
    if (this.overdue) this.overdue.navigated = true;
    this.navigations += 1;
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
    // One question at a time: the newer one takes the place of the one still open.
    this.cancelPending();
    this.pending = pending;
    if (!this.asker) this.openQuestion();
  }

  private openQuestion(): void {
    // The last screen to register is the one on top.
    this.asker = [...this.guards].pop() ?? null;
    this.asker?.setAsking(true);
  }

  private cancelPending(): void {
    const pending = this.pending;
    this.pending = null;
    if (pending?.kind === 'action') pending.cancel?.();
  }

  /** "Seguir editando". */
  stay = (): void => {
    this.cancelPending();
    this.asker?.setAsking(false);
    this.asker = null;
  };

  /** "Descartar": goes where the person was going. */
  discard = (): void => {
    const target = this.pending;
    this.pending = null;
    this.asker?.setAsking(false);
    this.asker = null;
    if (!target) return;
    const guards = [...this.guards];
    this.guards.clear();
    for (const guard of guards) {
      guard.onDiscard();
      guard.bypassed();
    }
    this.holdWorkspace();
    if (target.kind === 'back') return this.leaveByHistory(guards);
    // The extra entry goes first, so Back from the next screen returns here once, not twice.
    void this.disarm().then(() =>
      this.go(() => {
        if (target.kind === 'href') return this.follow(target);
        const mine = this.navigations;
        const result = target.run();
        if (result instanceof Promise) {
          // Still here and it failed: the changes are guarded again. Unless something else navigated
          // since: that screen is on its way out, and guarding it again would stop the newer navigation.
          result.catch(() => {
            if (this.navigations === mine) guards.forEach((guard) => guard.restore());
          });
        }
        return result;
      }),
    );
  };

  /** Follows a held link the way its component would have: clicking it again, now that nothing holds it. */
  private follow(target: { href: string; anchor?: HTMLAnchorElement }): void {
    // Its own handler may have taken it off the page meanwhile (a menu that closes on click): then it is a plain push.
    if (target.anchor?.isConnected) target.anchor.click();
    else this.router?.push(target.href);
  }

  /**
   * "Descartar" after Back. The browser is on the extra entry again (it was put back when Back was
   * held), with the screen's real entry under it: two steps back leave the screen.
   */
  private leaveByHistory(guards: GuardEntry[]): void {
    const steps = this.atArmedEntry() ? 2 : 1;
    this.armed = null;
    const before = entriesBefore();
    const restore = () => {
      this.leaving = null;
      // Nowhere to go back to after all: the screen is still on display, so it is guarded again.
      guards.forEach((guard) => guard.restore());
    };
    if (before.exact && before.count < steps) return restore();
    this.leaving = { pathname: window.location.pathname, guards, timer: window.setTimeout(restore, LEAVE_SAFETY_MS) };
    window.history.go(-steps);
  }

  /* ── The extra history entry ─────────────────────────────────────────── */

  private atArmedEntry(): boolean {
    const armed = this.armed;
    if (!armed) return false;
    if (tokenHere() === armed.token) return true;
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
    this.armed = null;
    const before = entriesBefore();
    const here = tokenHere();
    if (here !== null) {
      // This entry is an extra one from before (the page was reloaded on it, or the person came back
      // to it from a page opened on top): it is taken as it is. Another one on top would leave two
      // entries of this screen to go through on the way out. With the real entry and a page before
      // it underneath; otherwise there is nowhere to leave to, and it is just an entry.
      if (before.count >= 2) this.armed = { token: here, pathname: window.location.pathname, length: window.history.length };
      return;
    }
    // The first entry of the tab: Back has nowhere to go, so there is nothing to hold (and no way out to offer).
    if (before.count < 1) return;
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
    this.clearOverdue();
    let resolve: () => void = () => undefined;
    const promise = new Promise<void>((done) => (resolve = done));
    const timer = window.setTimeout(() => {
      this.pop = null;
      // The late `popstate` is only waited for a while: after that, a Back is the person's again.
      this.overdue = { navigated: false, timer: window.setTimeout(() => (this.overdue = null), POP_SAFETY_MS) };
      resolve();
    }, POP_SAFETY_MS);
    this.pop = { promise, resolve, timer };
    window.history.back();
    return promise;
  }

  private clearOverdue(): void {
    if (this.overdue) window.clearTimeout(this.overdue.timer);
    this.overdue = null;
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
      this.clearOverdue();
      if (navigated) window.history.forward();
      return;
    }
    if (this.leaving) {
      const { pathname, guards, timer } = this.leaving;
      window.clearTimeout(timer);
      // Still an entry of the screen being left, and one of this guard's (an older extra entry that
      // was under the real one): one more step, or "Descartar" would land the person on the same screen.
      if (tokenHere() !== null && window.location.pathname === pathname && entriesBefore().count >= 1) {
        this.leaving = { pathname, guards, timer: window.setTimeout(() => (this.leaving = null), LEAVE_SAFETY_MS) };
        window.history.back();
      } else {
        this.leaving = null;
      }
      return;
    }
    const armed = this.armed;
    if (!armed || this.guards.size === 0) return;
    // Still on the extra entry (the person came back to it from a page opened on top).
    if (tokenHere() === armed.token) return;
    this.armed = null;
    // Somewhere else: nothing to hold on to.
    if (window.location.pathname !== armed.pathname) return;
    // Back, onto the real entry of this screen. The extra one goes in again, so another Back is held too.
    this.arm();
    if (this.armed) this.ask({ kind: 'back' });
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
    // Only the navigation is stopped (next/link leaves a prevented click alone). The click still
    // reaches the link's own handler: the menu or the drawer it lives in closes as on any click.
    event.preventDefault();
    if (held) this.ask({ kind: 'href', href, anchor });
    else this.go(() => this.follow({ href, anchor }));
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
      if (this.leaving) window.clearTimeout(this.leaving.timer);
      this.clearOverdue();
      setWorkspaceHold(false);
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
  /** Runs an action that leaves the screen (sign out), asking first when there are changes. */
  confirm: (run: LeaveAction) => void;
  /** Asks to leave the screen: true when the way is clear, false when the person chose to stay. */
  request: () => Promise<boolean>;
}

/** How the shell navigates from code. Outside the provider (a component rendered alone) it is the plain router. */
export function useGuardedNavigation(): GuardedNavigation {
  const guard = useContext(LeaveGuardContext);
  const router = useRouter();
  return useMemo(
    () =>
      guard
        ? { push: (href) => guard.push(href), confirm: (run) => guard.confirm(run), request: () => guard.request() }
        : { push: (href) => router.push(href), confirm: (run) => void run(), request: () => Promise.resolve(true) },
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
