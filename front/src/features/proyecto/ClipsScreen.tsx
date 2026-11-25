'use client';

import { useMemo, useState } from 'react';
import type { Clip, Project } from '@/api/contracts/projects';
import { useDeleteClip, useDownloadAllClips, useProject, useRetryClip } from '@/api/hooks';
import { ConfirmDialog } from '@/dialogs';
import { downloadUrl } from '@/lib/download';
import { errorMessage } from '@/lib/error-messages';
import { formatDuration, plural } from '@/lib/format';
import { useMediaQuery } from '@/lib/hooks';
import { countClips } from '@/lib/project-status';
import { usePermissions } from '@/shell/session';
import {
  Badge,
  Banner,
  Button,
  Checkbox,
  ClipCard,
  DropdownMenu,
  EmptyState,
  FilterChips,
  IconButton,
  NetworkChip,
  StatusDot,
  VideoThumb,
  isPlaceholderSource,
  useToast,
  type FilterOption,
  type MenuItem,
} from '@/ui';
import { ActionSheet, type SheetAction } from './ActionSheet';
import { ClipPreviewDialog } from './ClipPreviewDialog';
import styles from './ClipsScreen.module.css';
import { clipCounts, clipDuration, describeClip, previewBlockedReason, shortDateTime, sortClips, type ClipDisplay, type ClipFilter } from './model';
import { useClipActions, usePublish } from './useClipActions';

interface TileHandlers {
  onPublish: (clip: Clip) => void;
  onDownload: (clip: Clip) => void;
  onCopyLink: (clip: Clip) => void;
  onPreview: (clip: Clip) => void;
  onRetry: (clip: Clip) => void;
  onDelete: (clip: Clip) => void;
}

/** Every action of a clip, in the order of the phone sheet. The hover tray and the menus pick from it. */
function useClipActionList(project: Project, handlers: TileHandlers) {
  const { gate } = usePermissions();
  const previewBlocked = previewBlockedReason(project);
  const resolution = project.metadata.render_resolution ?? '720p';
  return (clip: Clip, display: ClipDisplay): SheetAction[] => {
    const editHref = `/proyectos/${project.id}/clips/${clip.id}`;
    const actions: SheetAction[] = [];
    if (display.ready) {
      actions.push({ key: 'publish', label: 'Publicar', icon: 'send', primary: true, onSelect: () => handlers.onPublish(clip), ...gate('post.publish') });
      actions.push({ key: 'download', label: 'Descargar', icon: 'download', hint: `${resolution} · MP4`, onSelect: () => handlers.onDownload(clip) });
    } else {
      actions.push({
        key: 'preview',
        label: 'Vista previa',
        icon: 'eye',
        hint: 'Gratis · 360p',
        onSelect: () => handlers.onPreview(clip),
        ...(previewBlocked ? { disabledReason: previewBlocked } : {}),
      });
    }
    if (display.group === 'failed') actions.push({ key: 'retry', label: 'Reintentar', icon: 'retry', hint: 'No gasta tokens', onSelect: () => handlers.onRetry(clip), ...gate('clip.edit') });
    if (display.group === 'draft') {
      actions.push({ key: 'review', label: 'Revisar clips', icon: 'check-circle', href: `/proyectos/${project.id}/revision` });
      return actions;
    }
    actions.push({ key: 'edit', label: 'Editar', icon: 'edit', href: editHref, ...gate('clip.edit') });
    actions.push({ key: 'copy', label: 'Copiar link', icon: 'link', onSelect: () => handlers.onCopyLink(clip) });
    actions.push({ key: 'delete', label: 'Borrar', icon: 'trash', danger: true, separatorBefore: true, onSelect: () => handlers.onDelete(clip), ...gate('clip.edit') });
    return actions;
  };
}

function toMenuItems(actions: SheetAction[]): MenuItem[] {
  return actions.map((action) => ({
    key: action.key,
    label: action.label,
    icon: action.icon,
    href: action.href,
    onSelect: action.onSelect,
    danger: action.danger,
    separatorBefore: action.separatorBefore,
    disabled: Boolean(action.disabledReason),
    disabledReason: action.disabledReason,
  }));
}

interface ClipTileProps {
  clip: Clip;
  project: Project;
  actions: SheetAction[];
  display: ClipDisplay;
  touch: boolean;
  selecting: boolean;
  picked: boolean;
  onPick: (picked: boolean) => void;
  onOpenSheet: () => void;
  retrying: boolean;
  downloading: boolean;
}

function ClipTile({ clip, project, actions, display, touch, selecting, picked, onPick, onOpenSheet, retrying, downloading }: ClipTileProps) {
  const { gate } = usePermissions();
  const [hovering, setHovering] = useState(false);
  const byKey = (key: string) => actions.find((action) => action.key === key);
  const retry = byKey('retry');
  const hasThumb = display.group !== 'rendering' && display.group !== 'failed';
  const duration = clipDuration(clip);

  let corner = null;
  if (selecting) {
    corner = display.ready ? (
      <Checkbox checked={picked} onChange={(event) => onPick(event.target.checked)} aria-label={`Seleccionar «${clip.title}»`} />
    ) : null;
  } else if (touch) {
    corner = (
      <IconButton
        icon="more"
        size="lg"
        className={styles.onDark}
        label={`Acciones de «${clip.title}»`}
        aria-haspopup="dialog"
        onClick={onOpenSheet}
      />
    );
  } else if (!hasThumb) {
    // Rendering and failed cards have no hover tray: their actions live in a corner menu.
    corner = (
      <DropdownMenu
        ariaLabel={`Acciones de «${clip.title}»`}
        items={toMenuItems(actions.filter((action) => action.key !== 'retry'))}
        trigger={(props) => <IconButton icon="more" size="sm" className={styles.onDark} label={`Más acciones de «${clip.title}»`} {...props} />}
      />
    );
  }

  const main = byKey('publish') ?? byKey('preview');
  const download = byKey('download');
  const edit = byKey('edit');
  const more = actions.filter((action) => !['publish', 'preview', 'download', 'edit', 'retry'].includes(action.key) || (action.key === 'preview' && main?.key !== 'preview'));
  const tray =
    hasThumb && !selecting && !touch ? (
      <div className={styles.tray}>
        {main ? (
          <Button size="sm" variant={main.key === 'publish' ? 'primary' : 'secondary'} onClick={main.onSelect} disabledReason={main.disabledReason}>
            {main.label}
          </Button>
        ) : null}
        <span className={styles.spacer} />
        {download ? <IconButton icon="download" size="sm" label={`Descargar «${clip.title}»`} tooltip onClick={download.onSelect} disabled={downloading} /> : null}
        {edit ? <IconButton icon="edit" size="sm" label={`Editar «${clip.title}»`} tooltip href={edit.href} disabledReason={edit.disabledReason} /> : null}
        {more.length > 0 ? (
          <DropdownMenu
            ariaLabel={`Más acciones de «${clip.title}»`}
            side="top"
            items={toMenuItems(more)}
            trigger={(props) => <IconButton icon="more" size="sm" label={`Más acciones de «${clip.title}»`} {...props} />}
          />
        ) : null}
      </div>
    ) : null;

  return (
    <li
      className={`${styles.tile} ${picked ? styles.picked : ''}`}
      aria-busy={display.group === 'rendering' ? true : undefined}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
    >
      <ClipCard
        seed={clip.id}
        title={clip.title}
        ratio={project.aspect_ratio}
        thumbnailUrl={clip.thumbnail_url}
        duration={duration}
        score={display.group === 'draft' || clip.virality_score > 0 ? clip.virality_score : undefined}
        captionColor={project.add_subtitles ? project.subtitle_color : undefined}
        state={display.group === 'rendering' ? 'rendering' : display.group === 'failed' ? 'failed' : 'ready'}
        corner={corner}
        overlay={
          <>
            {hovering && clip.preview_url && !isPlaceholderSource(clip.preview_url) && !selecting ? (
              <video className={styles.hoverVideo} src={clip.preview_url} muted loop autoPlay playsInline aria-hidden="true" />
            ) : null}
            {tray}
          </>
        }
        failedAction={
          retry ? (
            <Button size="sm" variant="secondary" iconLeft="retry" onClick={retry.onSelect} loading={retrying} {...gate('clip.edit')}>
              Reintentar
            </Button>
          ) : undefined
        }
        meta={
          <>
            <Badge tone={display.tone} dot={!display.icon} icon={display.icon}>
              {display.label}
            </Badge>
            {hasThumb ? null : <span className="num">{formatDuration(duration)}</span>}
          </>
        }
      >
        {display.where ? (
          <p className={styles.where}>
            <NetworkChip platform={display.where.platform} size="sm" short />
            <span aria-hidden="true">·</span>
            <span className="num">{shortDateTime(display.where.publishAt)}</span>
          </p>
        ) : null}
      </ClipCard>
    </li>
  );
}

const FILTER_LABEL: Record<ClipFilter, string> = {
  all: 'Todos',
  ready: 'Listos',
  published: 'Publicados',
  scheduled: 'Programados',
  rendering: 'Renderizando',
  failed: 'Con error',
  draft: 'Sugeridos',
};

/** D3 · Clips: grid of clip cards with their state, filters, the selection mode and the actions per clip. */
export function ClipsScreen({ projectId }: { projectId: string }) {
  const { data: project } = useProject(projectId);
  const { gate } = usePermissions();
  const toast = useToast();
  const publish = usePublish();
  const { downloadClip, downloadingId, copyLink } = useClipActions();
  const downloadAll = useDownloadAllClips(projectId);
  const retryClip = useRetryClip();
  const deleteClip = useDeleteClip();
  // Phones and touch screens have no hover: the actions open in a bottom sheet.
  const touch = useMediaQuery('(max-width: 639px), (hover: none)');
  const [filter, setFilter] = useState<ClipFilter>('all');
  const [selecting, setSelecting] = useState(false);
  const [picked, setPicked] = useState<string[]>([]);
  const [sheetId, setSheetId] = useState<string | null>(null);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [deleteId, setDeleteId] = useState<string | null>(null);

  const clips = useMemo(() => sortClips(project?.clips ?? []), [project?.clips]);
  const find = (id: string | null) => clips.find((clip) => clip.id === id) ?? null;

  const actionsFor = useClipActionList(project ?? EMPTY_PROJECT, {
    onPublish: (clip) => publish([clip.id]),
    onDownload: (clip) => downloadClip(clip),
    onCopyLink: (clip) => void copyLink(`/proyectos/${projectId}/clips/${clip.id}`),
    onPreview: (clip) => setPreviewId(clip.id),
    onRetry: (clip) =>
      retryClip.mutate(clip.id, {
        onSuccess: () => toast.success('Reintentando el clip. No gasta tokens.'),
        onError: (error) => toast.error(errorMessage(error, 'No pudimos reintentar el clip.')),
      }),
    onDelete: (clip) => setDeleteId(clip.id),
  });

  if (!project) return null;
  const base = `/proyectos/${projectId}`;
  const counts = clipCounts(clips);
  const totals = countClips(clips);
  const readyIds = clips.filter((clip) => describeClip(clip).ready).map((clip) => clip.id);

  if (clips.length === 0) {
    if (project.status === 'failed') {
      return (
        <EmptyState
          icon="clips"
          title="Este proyecto no llegó a generar clips"
          description="El proceso falló antes de elegir los momentos."
          action={
            <Button variant="secondary" href={base}>
              Ver qué pasó
            </Button>
          }
        />
      );
    }
    if (project.status === 'awaiting_approval') {
      return (
        <EmptyState
          icon="clips"
          title="No encontramos momentos para sugerir"
          description="Podés elegirlos vos desde la transcripción, en la revisión."
          action={
            <Button variant="primary" href={`${base}/revision`}>
              Elegir momentos
            </Button>
          }
        />
      );
    }
    if (project.status === 'uploaded' || project.status === 'ingesting') {
      return (
        <EmptyState
          icon="clips"
          title="Todavía no hay clips"
          description="Aparecen acá cuando terminemos de elegir los mejores momentos."
          action={
            <Button variant="secondary" href={base}>
              Ver el proceso
            </Button>
          }
        />
      );
    }
    return <EmptyState icon="clips" title="No quedan clips en este proyecto" description="Los clips que borrás no se pueden recuperar." />;
  }

  const options: Array<FilterOption<ClipFilter>> = (['all', 'ready', 'published', 'scheduled', 'rendering', 'failed', 'draft'] as const).map((value) => ({
    value,
    label: FILTER_LABEL[value],
    count: counts[value],
    hideWhenEmpty: value === 'rendering' || value === 'failed' || value === 'draft' || (value !== 'all' && counts.draft === clips.length),
  }));
  const visible = filter === 'all' ? clips : clips.filter((clip) => describeClip(clip).group === filter);
  const pickedReady = picked.filter((id) => readyIds.includes(id));
  const stopSelecting = () => {
    setSelecting(false);
    setPicked([]);
  };
  const runDownloadAll = (ids?: string[]) =>
    downloadAll.mutate(ids, {
      onSuccess: ({ url }) => {
        downloadUrl(url, `${project.title.replace(/[^\p{L}\p{N}]+/gu, '-').replace(/^-|-$/g, '').toLowerCase() || 'clips'}.zip`);
        toast.success(ids ? `${plural(ids.length, 'clip descargado', 'clips descargados')}` : 'Clips descargados');
      },
      onError: (error) => toast.error(errorMessage(error, 'No pudimos armar el .zip.')),
    });

  const sheetClip = find(sheetId);
  const deleting = find(deleteId);
  return (
    <>
      {project.status === 'awaiting_approval' ? (
        <Banner
          tone="neutral"
          className={styles.notice}
          title="Estos clips todavía no se renderizaron"
          action={
            <Button size="sm" variant="primary" href={`${base}/revision`}>
              Revisar clips
            </Button>
          }
        >
          Son los momentos que sugiere la IA. Revisalos, elegí cuáles querés y renderizalos; mientras tanto podés pedir una vista previa gratis.
        </Banner>
      ) : null}
      {selecting ? (
        <div className={styles.selBar} role="toolbar" aria-label="Acciones para los clips seleccionados">
          <span className={styles.selCount} aria-live="polite">
            <span className="num">{pickedReady.length}</span> {pickedReady.length === 1 ? 'seleccionado' : 'seleccionados'}
          </span>
          <button type="button" className={styles.selAll} onClick={() => setPicked(pickedReady.length === readyIds.length ? [] : readyIds)}>
            {pickedReady.length === readyIds.length ? 'Quitar todos' : 'Elegir todos'}
          </button>
          <span className={styles.spacer} />
          <Button size="sm" variant="primary" iconLeft="send" disabled={pickedReady.length === 0} onClick={() => publish(pickedReady)} {...gate('post.publish')}>
            Publicar
          </Button>
          <Button size="sm" variant="secondary" iconLeft="download" disabled={pickedReady.length === 0} loading={downloadAll.isPending} onClick={() => runDownloadAll(pickedReady)}>
            Descargar
          </Button>
          <Button size="sm" variant="ghost" onClick={stopSelecting}>
            Cancelar
          </Button>
        </div>
      ) : (
        <div className={styles.toolbar}>
          <FilterChips options={options} value={filter} onChange={setFilter} ariaLabel="Filtrar clips por estado" className={styles.chips} />
          {totals.total > totals.ready + totals.failed && project.status === 'rendering' ? (
            <span className={`${styles.live} t-small muted`}>
              <StatusDot tone="info" live />
              <span className="num">
                {totals.ready} de {totals.total} clips listos
              </span>
            </span>
          ) : null}
          <span className={styles.spacer} />
          <div className={styles.tools}>
            <Button
              size="sm"
              variant="secondary"
              iconLeft="check-circle"
              aria-pressed={false}
              onClick={() => setSelecting(true)}
              {...(readyIds.length === 0 ? { disabledReason: 'Todavía no hay clips listos' } : gate('post.publish'))}
            >
              Seleccionar
            </Button>
            <Button
              size="sm"
              variant="secondary"
              iconLeft="download"
              loading={downloadAll.isPending}
              onClick={() => runDownloadAll()}
              {...(readyIds.length === 0 ? { disabledReason: 'Todavía no hay clips listos' } : {})}
            >
              Descargar todo <span className={styles.ext}>.zip</span>
            </Button>
          </div>
        </div>
      )}
      {visible.length === 0 ? (
        <EmptyState
          bare
          title={`No hay clips en «${FILTER_LABEL[filter]}»`}
          action={
            <Button variant="secondary" size="sm" onClick={() => setFilter('all')}>
              Ver todos
            </Button>
          }
        />
      ) : (
        <ul className={styles.grid}>
          {visible.map((clip) => {
            const display = describeClip(clip);
            return (
              <ClipTile
                key={clip.id}
                clip={clip}
                project={project}
                display={display}
                actions={actionsFor(clip, display)}
                touch={touch}
                selecting={selecting}
                picked={picked.includes(clip.id)}
                onPick={(value) => setPicked((current) => (value ? [...current.filter((id) => id !== clip.id), clip.id] : current.filter((id) => id !== clip.id)))}
                onOpenSheet={() => setSheetId(clip.id)}
                retrying={retryClip.isPending && retryClip.variables === clip.id}
                downloading={downloadingId === clip.id}
              />
            );
          })}
        </ul>
      )}
      <ActionSheet
        open={sheetClip !== null}
        onClose={() => setSheetId(null)}
        title={sheetClip?.title ?? ''}
        subtitle={sheetClip ? `${formatDuration(clipDuration(sheetClip))} · Puntaje ${sheetClip.virality_score} · ${describeClip(sheetClip).label}` : undefined}
        media={sheetClip ? <VideoThumb ratio="9:16" xs width={36} seed={sheetClip.id} /> : undefined}
        actions={sheetClip ? actionsFor(sheetClip, describeClip(sheetClip)) : []}
      />
      <ClipPreviewDialog clip={find(previewId)} project={project} onClose={() => setPreviewId(null)} />
      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          deleteClip.reset();
          setDeleteId(null);
        }}
        onConfirm={() => {
          if (!deleting) return;
          deleteClip.mutate(deleting.id, {
            onSuccess: () => {
              setDeleteId(null);
              setPicked((current) => current.filter((id) => id !== deleting.id));
              toast.success('Clip borrado');
            },
          });
        }}
        tone="danger"
        title={`¿Borrar «${deleting?.title ?? ''}»?`}
        description="Se borra el clip y se cancelan sus publicaciones programadas. No se puede deshacer."
        confirmLabel="Borrar clip"
        loading={deleteClip.isPending}
        error={deleteClip.isError ? errorMessage(deleteClip.error, 'No pudimos borrar el clip.') : undefined}
      />
    </>
  );
}

/** Stand-in while the project loads, so the hooks above run in the same order on every render. */
const EMPTY_PROJECT = { id: '', project_type: 'repurpose', source_file: null, metadata: {} } as unknown as Project;
