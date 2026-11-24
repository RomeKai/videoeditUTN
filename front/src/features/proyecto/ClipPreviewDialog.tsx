'use client';

import Link from 'next/link';
import { useEffect } from 'react';
import type { Clip, Project } from '@/api/contracts/projects';
import { ApiError } from '@/api/errors';
import { useClipPreview, useRequestClipPreview } from '@/api/hooks';
import { errorMessage } from '@/lib/error-messages';
import { usePlan } from '@/shell/session';
import { Banner, Button, Dialog, Spinner, VideoPlayer, VideoThumb } from '@/ui';
import styles from './ClipsScreen.module.css';
import { clipDuration } from './model';
import shell from './ProjectShell.module.css';

export interface ClipPreviewDialogProps {
  /** The clip to preview; null keeps the dialog closed. */
  clip: Clip | null;
  project: Pick<Project, 'aspect_ratio' | 'add_subtitles' | 'subtitle_color'>;
  onClose: () => void;
}

function PreviewBody({ clip, project }: { clip: Clip; project: ClipPreviewDialogProps['project'] }) {
  const request = useRequestClipPreview(clip.id);
  const current = useClipPreview(clip.id);
  const { plan } = usePlan();
  const { mutate } = request;
  // Opening the dialog asks for the preview; the backend answers with the one in progress if there is one.
  useEffect(() => {
    mutate();
  }, [mutate, clip.id]);

  const preview = current.data ?? request.data ?? null;
  const tooMany = request.error instanceof ApiError && request.error.status === 429;
  const retry = (
    <Button size="sm" variant="secondary" iconLeft="retry" onClick={() => mutate()} loading={request.isPending}>
      Reintentar
    </Button>
  );

  let stage;
  if (request.isError && !preview) {
    stage = (
      <Banner tone={tooMany ? 'warn' : 'err'} title={tooMany ? 'Ya hay vistas previas en proceso' : 'No podemos armar la vista previa'} action={tooMany ? retry : undefined}>
        {tooMany ? 'Esperá unos segundos y probá de nuevo.' : errorMessage(request.error, 'Probá de nuevo en un rato.')}
      </Banner>
    );
  } else if (preview?.status === 'failed') {
    stage = (
      <Banner tone="err" title="No pudimos armar la vista previa" action={retry}>
        No se cobró nada. Podés pedirla de nuevo.
      </Banner>
    );
  } else if (preview?.status === 'ready' && preview.url) {
    stage = (
      <div className={styles.previewStage}>
        <VideoPlayer
          src={preview.url}
          seed={clip.id}
          ratio={project.aspect_ratio}
          duration={clipDuration(clip)}
          captionColor={project.add_subtitles ? project.subtitle_color : undefined}
          label={`Vista previa: ${clip.title}`}
        />
      </div>
    );
  } else {
    stage = (
      <div className={styles.previewStage} aria-live="polite">
        <VideoThumb ratio={project.aspect_ratio} seed={clip.id} captions={false} width={200}>
          <span className={styles.previewWait}>
            <Spinner size={14} />
            {preview?.status === 'rendering' ? 'Armando la vista previa…' : 'En la fila…'}
          </span>
        </VideoThumb>
      </div>
    );
  }

  const simplified = preview?.simplified;
  return (
    <>
      {stage}
      <p className="t-small muted num">
        Es gratis y no reserva tokens. Sale en {simplified?.height ?? 360}p
        {simplified && !simplified.face_tracking ? ', sin seguimiento de caras' : ''}
        {simplified && !simplified.silence_removal ? ' y sin quitar silencios' : ''}: el render final se ve mejor.
      </p>
      {plan?.has_watermark ? (
        <p className="t-small muted">
          Tu plan agrega marca de agua.{' '}
          <Link className={shell.link} href="/uso/planes">
            Ver planes
          </Link>
        </p>
      ) : null}
    </>
  );
}

/** Free 360p preview of a clip that is not rendered yet (PTE-03). Asks for it on open and polls until it is ready. */
export function ClipPreviewDialog({ clip, project, onClose }: ClipPreviewDialogProps) {
  return (
    <Dialog
      open={clip !== null}
      onClose={onClose}
      title="Vista previa"
      description={clip?.title}
      footer={
        <Button variant="primary" onClick={onClose} data-autofocus>
          Cerrar
        </Button>
      }
    >
      {clip ? <PreviewBody key={clip.id} clip={clip} project={project} /> : null}
    </Dialog>
  );
}
