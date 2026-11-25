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

/**
 * D8 · Cambios sin guardar for a whole screen. While `dirty`:
 * - closing or reloading the tab shows the browser's own warning;
 * - a click on any in-app link is held until the user chooses Descartar or Seguir editando.
 * The App Router has no way to block a navigation, so links are intercepted at the document.
 */
export function useUnsavedGuard(dirty: boolean, onDiscard?: () => void): UnsavedGuard {
  const router = useRouter();
  const [pending, setPending] = useState<string | null>(null);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const bypass = useRef(false);
  useBeforeUnload(dirty);

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
      setPending(`${url.pathname}${url.search}${url.hash}`);
    };
    // Capture: before next/link starts the navigation.
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  const stay = useCallback(() => setPending(null), []);
  const discard = useCallback(() => {
    const href = pending;
    setPending(null);
    if (!href) return;
    onDiscard?.();
    bypass.current = true;
    router.push(href);
  }, [onDiscard, pending, router]);
  const leave = useCallback(
    (href: string) => {
      if (dirtyRef.current && !bypass.current) setPending(href);
      else router.push(href);
    },
    [router],
  );
  const allowNext = useCallback(() => {
    bypass.current = true;
  }, []);

  return { asking: pending !== null, stay, discard, leave, allowNext };
}
