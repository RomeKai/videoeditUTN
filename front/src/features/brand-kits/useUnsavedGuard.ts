'use client';

import { useRouter } from 'next/navigation';
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
  /** Lets the next navigation through (after saving). */
  allowNext: () => void;
}

/** Where the user was going when the guard stopped them: a path, or one step back in the history. */
type Pending = { kind: 'href'; href: string } | { kind: 'back' };

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
  /** The next `popstate` is this hook removing its own entry. */
  const ownPop = useRef(false);
  useBeforeUnload(dirty);

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
    const original = router.push;
    const guarded: typeof router.push = (href, options) => {
      if (bypass.current || !dirtyRef.current || samePage(href)) return original.call(router, href, options);
      setPending({ kind: 'href', href });
    };
    try {
      router.push = guarded;
    } catch {
      return;
    }
    return () => {
      if (router.push === guarded) router.push = original;
    };
  }, [dirty, router]);

  // Back. The extra entry carries the router's own state, so the router sees the same page.
  const arm = useCallback(() => {
    if (armed.current) return;
    window.history.pushState(window.history.state, '', window.location.href);
    armed.current = true;
  }, []);
  useEffect(() => {
    if (dirty) arm();
    else if (armed.current && !bypass.current) {
      // Clean again (saved, or the changes were undone): the extra entry goes away.
      armed.current = false;
      ownPop.current = true;
      window.history.back();
    }
  }, [dirty, arm]);
  useEffect(() => {
    const onPop = () => {
      if (ownPop.current) {
        ownPop.current = false;
        return;
      }
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
    if (target.kind === 'back') window.history.back();
    else router.push(target.href);
  }, [onDiscard, pending, router]);
  const leave = useCallback(
    (href: string) => {
      if (dirtyRef.current && !bypass.current) setPending({ kind: 'href', href });
      else router.push(href);
    },
    [router],
  );
  const allowNext = useCallback(() => {
    bypass.current = true;
  }, []);

  return { asking: pending !== null, stay, discard, leave, allowNext };
}
