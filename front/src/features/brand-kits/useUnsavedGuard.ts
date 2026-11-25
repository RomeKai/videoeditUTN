'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useBeforeUnload } from '@/lib/hooks';

export interface UnsavedGuard {
  /** True while the "Tenés cambios sin guardar" dialog should be open. */
  asking: boolean;
  /** "Seguir editando". */
  stay: () => void;
  /** "Descartar": goes where the user was going. */
  discard: () => void;
  /** Leaves through the guard: asks first when there are changes. */
  leave: (href: string) => void;
  /**
   * After saving or deleting: stops guarding and takes the extra history entry away. Resolves when
   * the history is as the user left it, so the navigation that follows lands where it should.
   */
  release: () => Promise<void>;
}

/** Where the user was going when the guard stopped them: a path, or one step back in the history. */
type Pending = { kind: 'href'; href: string } | { kind: 'back' };

type Router = ReturnType<typeof useRouter>;
/** Says whether it holds a navigation started from code (and asks the user). */
type Interceptor = (href: string) => boolean;

/**
 * One wrapper per router, whoever asks. Each guard adds its interceptor and takes it out when it is done;
 * the router gets its own `push` back when the last one leaves, in whatever order they leave. (Each guard
 * wrapping and unwrapping on its own would leave a stale wrapper in the chain when the order is not a stack.)
 */
const wrapped = new WeakMap<Router, { original: Router['push']; guarded: Router['push']; interceptors: Set<Interceptor> }>();

export function interceptPush(router: Router, interceptor: Interceptor): () => void {
  let entry = wrapped.get(router);
  if (!entry) {
    const original = router.push;
    const interceptors = new Set<Interceptor>();
    const guarded: Router['push'] = (href, options) => {
      for (const held of interceptors) if (held(href)) return;
      return original.call(router, href, options);
    };
    try {
      router.push = guarded;
    } catch {
      return () => undefined;
    }
    entry = { original, guarded, interceptors };
    wrapped.set(router, entry);
  }
  const current = entry;
  current.interceptors.add(interceptor);
  return () => {
    current.interceptors.delete(interceptor);
    if (current.interceptors.size > 0 || wrapped.get(router) !== current) return;
    if (router.push === current.guarded) router.push = current.original;
    wrapped.delete(router);
  };
}

/** How long to wait for the browser to report that it went back one entry. */
const POP_TIMEOUT_MS = 500;

const samePage = (href: string): boolean => {
  try {
    return new URL(href, window.location.href).pathname === window.location.pathname;
  } catch {
    return false;
  }
};

/**
 * D8 · Cambios sin guardar for a whole screen. While `dirty`:
 * - closing or reloading the tab shows the browser's own warning;
 * - a click on any in-app link is held until the user chooses Descartar or Seguir editando;
 * - so is a navigation started from code (`router.push` from the command menu, the notifications or the workspace switcher);
 * - and the browser's Back button.
 *
 * The App Router has no way to block a navigation, so each route is caught where it starts:
 * links at the document, `router.push` on the router (one object shared by the whole app), and
 * Back with an extra history entry for the same page: going back lands on the real one, which is
 * still this screen, and the question is asked there.
 */
export function useUnsavedGuard(dirty: boolean, onDiscard?: () => void): UnsavedGuard {
  const router = useRouter();
  const [pending, setPending] = useState<Pending | null>(null);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const bypass = useRef(false);
  /** The extra history entry is in place. */
  const armed = useRef(false);
  /** Set while this hook removes its own entry: the next `popstate` is that, and calls it. */
  const ownPop = useRef<(() => void) | null>(null);
  useBeforeUnload(dirty);

  // A release lasts until the screen changes: the next one (the kit just created) is guarded again.
  const pathname = usePathname();
  useEffect(() => {
    bypass.current = false;
  }, [pathname]);

  // Links.
  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!dirtyRef.current || bypass.current) return;
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      // Same page (hash or query only): nothing is lost.
      if (url.pathname === window.location.pathname) return;
      event.preventDefault();
      event.stopPropagation();
      setPending({ kind: 'href', href: `${url.pathname}${url.search}${url.hash}` });
    };
    // Capture: before next/link starts the navigation.
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  // Navigations from code, anywhere in the app.
  useEffect(() => {
    if (!dirty) return;
    return interceptPush(router, (href) => {
      if (bypass.current || !dirtyRef.current || samePage(href)) return false;
      setPending({ kind: 'href', href });
      return true;
    });
  }, [dirty, router]);

  // Back. The extra entry carries the router's own state, so the router sees the same page.
  const arm = useCallback(() => {
    if (armed.current || bypass.current) return;
    window.history.pushState(window.history.state, '', window.location.href);
    armed.current = true;
  }, []);
  /** Takes the extra entry away, and resolves once the browser is back on the real one. */
  const disarm = useCallback((): Promise<void> => {
    if (!armed.current) return Promise.resolve();
    armed.current = false;
    return new Promise((resolve) => {
      const done = () => {
        window.clearTimeout(timer);
        ownPop.current = null;
        resolve();
      };
      const timer = window.setTimeout(done, POP_TIMEOUT_MS);
      ownPop.current = done;
      window.history.back();
    });
  }, []);
  useEffect(() => {
    if (dirty) arm();
    // Clean again (saved, or the changes were undone): the extra entry goes away.
    else void disarm();
  }, [dirty, arm, disarm]);
  useEffect(() => {
    const onPop = () => {
      if (ownPop.current) return ownPop.current();
      if (!armed.current) return;
      armed.current = false;
      if (dirtyRef.current && !bypass.current) setPending({ kind: 'back' });
    };
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const stay = useCallback(() => {
    // Back was held: the extra entry was spent, it goes in again.
    if (pending?.kind === 'back' && dirtyRef.current) arm();
    setPending(null);
  }, [arm, pending]);
  const discard = useCallback(() => {
    const target = pending;
    setPending(null);
    if (!target) return;
    onDiscard?.();
    bypass.current = true;
    // Back was held on the real entry already: one more step leaves the screen.
    if (target.kind === 'back') return window.history.back();
    // The extra entry goes first, so Back from the next screen returns here once, not twice.
    void disarm().then(() => router.push(target.href));
  }, [disarm, onDiscard, pending, router]);
  const leave = useCallback(
    (href: string) => {
      if (dirtyRef.current && !bypass.current) setPending({ kind: 'href', href });
      else router.push(href);
    },
    [router],
  );
  const release = useCallback(() => {
    bypass.current = true;
    return disarm();
  }, [disarm]);

  return { asking: pending !== null, stay, discard, leave, release };
}
