'use client';

import { useDeleteProject, usePosts } from '@/api/hooks';
import { errorMessage } from '@/lib/error-messages';
import { formatDateTime, formatTokens, plural } from '@/lib/format';
import { Banner } from '@/ui/Banner';
import { NetworkChip } from '@/ui/NetworkChip';
import { VideoThumb } from '@/ui/VideoThumb';
import { ConfirmDialog } from './ConfirmDialog';
import styles from './Dialogs.module.css';

export interface DeleteProjectDialogProps {
  open: boolean;
  onClose: () => void;
  project: { id: string; title: string; cost?: { amount: number; state: 'reserved' | 'charged' | 'refunded' } | null } | null;
  /** After the project was deleted (go back to the list, show a toast). */
  onDeleted?: () => void;
}

/**
 * D4 · Borrar proyecto: the video, the clips and the scheduled publications go
 * away and cannot be recovered. Lists the publications that get cancelled.
 * Nothing already charged is given back.
 */
export function DeleteProjectDialog({ open, onClose, project, onDeleted }: DeleteProjectDialogProps) {
  const remove = useDeleteProject();
  const posts = usePosts({ project: project?.id, status: 'SCHEDULED', page_size: 50 }, { enabled: open && Boolean(project) });
  const scheduled = posts.data?.results ?? [];
  const charged = project?.cost && project.cost.state === 'charged' ? project.cost.amount : 0;
  return (
    <ConfirmDialog
      open={open && project !== null}
      onClose={() => {
        remove.reset();
        onClose();
      }}
      onConfirm={() => {
        if (!project) return;
        remove.mutate(project.id, {
          onSuccess: () => {
            onClose();
            onDeleted?.();
          },
        });
      }}
      tone="danger"
      title={`¿Borrar «${project?.title ?? ''}»?`}
      description="Se borran el video, los clips y las publicaciones programadas. No se puede deshacer."
      confirmLabel="Borrar proyecto"
      loading={remove.isPending}
      error={remove.isError ? errorMessage(remove.error, 'No pudimos borrar el proyecto.') : undefined}
    >
      {scheduled.length > 0 ? (
        <div className={styles.sched}>
          <p className="t-label muted">
            {plural(scheduled.length, 'publicación programada', 'publicaciones programadas')} que se {scheduled.length === 1 ? 'cancela' : 'cancelan'}
          </p>
          <ul className={styles.schedList}>
            {scheduled.map((post) => (
              <li key={post.id}>
                <VideoThumb ratio="9:16" xs width={27} seed={post.clip.id} />
                <span className={styles.schedMain}>
                  <span className={styles.strong}>{post.clip.title}</span>
                  <span className={styles.schedNet}>
                    <NetworkChip platform={post.platform} size="sm" short />
                    <span className="num">{formatDateTime(post.publish_at)}</span>
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <Banner tone="neutral" live="off">
        {charged > 0 ? (
          <>
            No se devuelve nada de lo ya cobrado por este proyecto (<span className="num">{formatTokens(charged)}</span>).
          </>
        ) : (
          'No se devuelve nada de lo ya cobrado por este proyecto.'
        )}
      </Banner>
    </ConfirmDialog>
  );
}
