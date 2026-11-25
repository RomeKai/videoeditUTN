'use client';

import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useBeforeUnload } from '@/lib/hooks';

export interface UnsavedGuard {
  /** Props for <UnsavedChangesDialog>. */
  dialog: { open: boolean; onStay: () => void; onDiscard: () => void };
  /** Leaves through the guard: asks first when there are changes. */
  leave: (href: string) => void;
  /** Leaves without asking (after saving, or "Descartar cambios"). `replace` keeps this screen out of the history. */
  leaveNow: (href: string, options?: { replace?: boolean }) => void;
}

export interface UnsavedGuardOptions {
  /** Links that never ask: the round trip to buy tokens keeps the work. */
  allow?: (href: string) => boolean;
  /** Runs when the person chooses "Descartar", before leaving. */
  onDiscard?: () => void;
}

/**
 * D8 for a screen with unsaved work. The App Router has no navigation blocker,
 * so the guard listens to clicks on in-app links (top bar, breadcrumbs, command
 * menu results) and opens the dialog before following them; closing or
 * reloading the tab gets the browser's own prompt.
 */
export function useUnsavedGuard(dirty: boolean, { allow, onDiscard }: UnsavedGuardOptions = {}): UnsavedGuard {
  const router = useRouter();
  const [pending, setPending] = useState<string | null>(null);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const allowRef = useRef(allow);
  allowRef.current = allow;
  const onDiscardRef = useRef(onDiscard);
  onDiscardRef.current = onDiscard;
  // Set while leaving on purpose, so the click listener and the tab prompt stay quiet.
  const leaving = useRef(false);
  const [bypass, setBypass] = useState(false);

  useBeforeUnload(dirty && !bypass);

  useEffect(() => {
    const onClick = (event: MouseEvent) => {
      if (!dirtyRef.current || leaving.current || event.defaultPrevented) return;
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const anchor = (event.target as Element | null)?.closest?.('a[href]') as HTMLAnchorElement | null;
      if (!anchor || anchor.target === '_blank' || anchor.hasAttribute('download')) return;
      const url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return;
      const href = `${url.pathname}${url.search}${url.hash}`;
      if (url.pathname === window.location.pathname) return;
      if (allowRef.current?.(href)) return;
      event.preventDefault();
      event.stopPropagation();
      setPending(href);
    };
    document.addEventListener('click', onClick, true);
    return () => document.removeEventListener('click', onClick, true);
  }, []);

  const leaveNow = useCallback(
    (href: string, options: { replace?: boolean } = {}) => {
      leaving.current = true;
      setBypass(true);
      if (options.replace) router.replace(href);
      else router.push(href);
    },
    [router],
  );

  const leave = useCallback(
    (href: string) => {
      if (dirtyRef.current) setPending(href);
      else leaveNow(href);
    },
    [leaveNow],
  );

  return {
    dialog: {
      open: pending !== null,
      onStay: () => setPending(null),
      onDiscard: () => {
        const href = pending;
        setPending(null);
        onDiscardRef.current?.();
        if (href) leaveNow(href);
      },
    },
    leave,
    leaveNow,
  };
}
