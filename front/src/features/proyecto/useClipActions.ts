'use client';

import { usePathname, useRouter } from 'next/navigation';
import { useCallback, useState } from 'react';
import type { Clip } from '@/api/contracts/projects';
import { useClipDownload } from '@/api/hooks';
import { downloadUrl } from '@/lib/download';
import { errorMessage } from '@/lib/error-messages';
import { useToast } from '@/ui';

/** Download and copy-link of a clip, with their toasts (map 1.8: "Clip descargado"). */
export function useClipActions() {
  const toast = useToast();
  const download = useClipDownload();
  const { mutateAsync } = download;
  // Several downloads can be under way at once: each card tracks its own.
  const [downloadingIds, setDownloadingIds] = useState<string[]>([]);

  const downloadClip = useCallback(
    async (clip: Pick<Clip, 'id'>, done = 'Clip descargado') => {
      setDownloadingIds((ids) => [...ids.filter((id) => id !== clip.id), clip.id]);
      // Awaited per call: the callbacks passed to `mutate` only fire for the latest call of a shared
      // mutation, so with two downloads at once the first one would never finish.
      try {
        const { url, filename } = await mutateAsync(clip.id);
        downloadUrl(url, filename);
        toast.success(done);
      } catch (error) {
        toast.error(errorMessage(error, 'No pudimos descargar el clip.'));
      } finally {
        setDownloadingIds((ids) => ids.filter((id) => id !== clip.id));
      }
    },
    [mutateAsync, toast],
  );

  const copyLink = useCallback(
    async (path: string) => {
      try {
        await navigator.clipboard.writeText(new URL(path, window.location.origin).toString());
        toast.success('Link copiado');
      } catch {
        toast.error('No pudimos copiar el link.');
      }
    },
    [toast],
  );

  return { downloadClip, downloadingIds, copyLink };
}

/** Opens the publish panel (E1) with the convention `?publicar=<clipId>[,<clipId>…]` on the current screen. */
export function usePublish(): (clipIds: string[]) => void {
  const router = useRouter();
  const pathname = usePathname() ?? '';
  return useCallback(
    (clipIds: string[]) => {
      if (clipIds.length === 0) return;
      router.push(`${pathname}?publicar=${clipIds.join(',')}`, { scroll: false });
    },
    [pathname, router],
  );
}

/** Where "Crear de nuevo" and "Duplicar configuración" go: Nuevo proyecto (C1) starting from this project's configuration. */
export function recreateHref(projectId: string): string {
  return `/proyectos/nuevo?desde=${projectId}`;
}
