'use client';

import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useMemo } from 'react';
import { PublishPanel } from './PublishPanel';

/** Query parameter that opens the panel: `?publicar=<clipId>[,<clipId>…]` (front/ROUTES.md). */
export const PUBLISH_PARAM = 'publicar';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
/** The bulk endpoint takes 60 publications: 20 clips in up to 3 networks. */
export const MAX_CLIPS = 20;

/** Clip ids of the parameter: valid, without repeats, in order. */
export function parsePublishParam(value: string | null | undefined): string[] {
  if (!value) return [];
  const ids: string[] = [];
  for (const part of value.split(',')) {
    const id = part.trim();
    if (UUID.test(id) && !ids.includes(id)) ids.push(id);
  }
  return ids.slice(0, MAX_CLIPS);
}

/** Link that opens the panel over `pathname`, keeping the rest of its query. */
export function publishHref(pathname: string, clipIds: string[], search?: string): string {
  const params = new URLSearchParams(search ?? '');
  params.set(PUBLISH_PARAM, clipIds.join(','));
  return `${pathname}?${params.toString().replace(/%2C/gi, ',')}`;
}

function Mount() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const value = params.get(PUBLISH_PARAM);
  const clipIds = useMemo(() => parsePublishParam(value), [value]);
  const close = useCallback(() => {
    const next = new URLSearchParams(params.toString());
    next.delete(PUBLISH_PARAM);
    const query = next.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }, [params, pathname, router]);
  if (clipIds.length === 0) return null;
  // A different selection is a different panel: its state starts over.
  return <PublishPanel key={clipIds.join(',')} clipIds={clipIds} onClose={close} />;
}

/**
 * Mount point of E1 · Publicar for every screen with the app shell. Any screen
 * opens the panel by adding `?publicar=<clipId>[,…]` to its own URL; closing it
 * removes the parameter and leaves the screen as it was.
 */
export function PublishPanelMount() {
  return (
    <Suspense fallback={null}>
      <Mount />
    </Suspense>
  );
}
