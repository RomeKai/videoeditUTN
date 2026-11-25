'use client';

import { useQueryClient } from '@tanstack/react-query';
import Link from 'next/link';
import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { RenderResolution } from '@/api/contracts/common';
import type { Clip, Project } from '@/api/contracts/projects';
import { ApiError } from '@/api/errors';
import { useClip, useClipDownload, usePricing, useProject, useRenderClip, useRetryClip, useUpdateClip, useWallet } from '@/api/hooks';
import { keys } from '@/api/query';
import { ConfirmCostDialog, UnsavedChangesDialog, useBillingDialogs } from '@/dialogs';
import { cx } from '@/lib/cx';
import { downloadUrl } from '@/lib/download';
import { errorMessage } from '@/lib/error-messages';
import { formatDuration, formatPercent, formatTimecode, formatTokens } from '@/lib/format';
import { useIsMobile } from '@/lib/hooks';
import { NO_ACCESS_REASON } from '@/lib/permissions';
import { estimateCost, planAllowsResolution } from '@/lib/pricing';
import { CLIP_STATUS } from '@/lib/project-status';
import { LoadError } from '@/shell/LoadError';
import { usePermissions, usePlan, useSession } from '@/shell/session';
import { NotFoundMessage } from '@/shell/SystemPage';
import {
  ActionBar,
  Badge,
  Banner,
  Button,
  Card,
  CardHead,
  Field,
  Icon,
  Input,
  Kbd,
  Page,
  ProjectHeader,
  SegmentedControl,
  Skeleton,
  useToast,
  VideoPlayer,
  type VideoPlayerHandle,
} from '@/ui';
import { EdgeInput } from './components/EdgeInput';
import { TrimTimeline, windowAround, type TimeWindow } from './components/TrimTimeline';
import timeline from './components/timeline.module.css';
import { describeStyle } from './lib/style';
import { applyEditorDraft, editorScope, readEditorDraft, storeEditorDraft } from './lib/editorDraft';
import { billedSeconds as billed, clamp, formatEdge, MIN_CLIP_SECONDS } from './lib/time';
import { buildRows, snapEnd, snapStart, stepEdge, type Word } from './lib/transcript';
import styles from './editor.module.css';
import { useFreePreview } from './useFreePreview';
import { useUnsavedGuard } from './useUnsavedGuard';

export const PROJECT_RENDERING_MESSAGE = 'El proyecto se está renderizando: esperá a que termine para cambiarlo.';

interface Range {
  start: number;
  end: number;
}

function EditorSkeleton() {
  return (
    <div className={styles.editor} aria-busy="true" aria-label="Cargando el editor">
      <Card className={styles.sk}>
        <Skeleton height={0} style={{ paddingBottom: '150%' }} radius={8} />
        <Skeleton width="50%" height={14} />
      </Card>
      <div className={styles.col}>
        <Card className={styles.sk}>
          <Skeleton width={120} height={18} />
          <Skeleton height={120} radius={6} />
          <Skeleton width="60%" height={36} radius={6} />
        </Card>
        <Card className={styles.sk}>
          <Skeleton width="40%" height={36} radius={6} />
        </Card>
      </div>
    </div>
  );
}

/** D4 · Editor de clip: trim and title; the style belongs to the project. */
export function ClipEditorScreen({ projectId, clipId }: { projectId: string; clipId: string }) {
  const clip = useClip(clipId);
  const project = useProject(projectId);
  const { me } = useSession();
  // The draft is stored per person: wait for the session before reading it.
  if (clip.isLoading || project.isLoading || (clip.data && project.data && !me)) {
    return (
      <Page width="wide" className={styles.page}>
        <ProjectHeader compact crumbs={[{ label: 'Proyectos', href: '/proyectos' }, { label: 'Editar clip' }]} title="Editar clip" />
        <EditorSkeleton />
      </Page>
    );
  }
  if (!clip.data || !project.data || !me) {
    const retry = () => {
      void clip.refetch();
      void project.refetch();
    };
    return (
      <Page width="wide" className={styles.page}>
        <LoadError error={clip.error ?? project.error} onRetry={retry} />
      </Page>
    );
  }
  if (clip.data.project !== project.data.id) {
    return (
      <Page width="wide" className={styles.page}>
        <NotFoundMessage inline />
      </Page>
    );
  }
  // One editor per clip: its draft never leaks into another clip's screen.
  return <Editor key={`${me.id}.${clip.data.id}`} userId={me.id} clip={clip.data} project={project.data} loadError={clip.error ?? project.error} />;
}

function Editor({ userId, clip, project, loadError }: { userId: string; clip: Clip; project: Project; loadError: unknown }) {
  const toast = useToast();
  const isMobile = useIsMobile();
  const { can, gate } = usePermissions();
  const { plan } = usePlan();
  const pricing = usePricing();
  const wallet = useWallet();
  const billing = useBillingDialogs();
  const update = useUpdateClip(clip.id);
  const renderClip = useRenderClip(clip.id);
  const retry = useRetryClip();
  const queryClient = useQueryClient();
  const download = useClipDownload();

  const wholeVideo = project.project_type === 'single_edit';
  const backHref = wholeVideo ? `/proyectos/${project.id}/resultado` : `/proyectos/${project.id}/clips`;
  const backLabel = wholeVideo ? 'Resultado' : 'Clips';

  const words = useMemo<Word[]>(() => project.transcript_data ?? [], [project.transcript_data]);
  const rows = useMemo(() => buildRows(words, { softWords: 12, hardWords: 40 }), [words]);
  const total = Math.max(project.duration_seconds ?? 0, words[words.length - 1]?.end ?? 0, clip.end_time);

  /* ── Draft ────────────────────────────────────────────────────────────── */
  // Unsaved work kept for the tab's session: it survives a reload, Back and the trip to buy tokens.
  const scope = editorScope(userId, clip.id);
  // Only the fields the person really changed come back; the rest follows the server as it is now.
  const [stored] = useState(() => applyEditorDraft({ title: clip.title, start: clip.start_time, end: clip.end_time }, readEditorDraft(scope)));
  const [title, setTitle] = useState(stored.title);
  const [range, setRange] = useState<Range>({ start: stored.start, end: Math.min(stored.end, Math.max(total, clip.end_time)) });
  // The trim of the render that exists. It only differs from the saved clip after "save without rendering".
  const [rendered] = useState<Range>({ start: clip.start_time, end: clip.end_time });
  const [renderedTitle, setRenderedTitle] = useState(clip.title);
  const [pendingRender, setPendingRender] = useState(false);
  const [detail, setDetail] = useState<TimeWindow>(() =>
    windowAround(Math.min(stored.start, clip.start_time), Math.max(stored.end, clip.end_time), total),
  );
  const [fieldError, setFieldError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);

  // The server's copy changed (someone else edited, or a refetch after saving): follow it only in
  // what has no local unsaved edit. What the person is typing or trimming is never overwritten.
  const synced = useRef({ title: clip.title, start: clip.start_time, end: clip.end_time });
  useEffect(() => {
    const last = synced.current;
    if (last.title === clip.title && last.start === clip.start_time && last.end === clip.end_time) return;
    synced.current = { title: clip.title, start: clip.start_time, end: clip.end_time };
    setTitle((current) => (current.trim() === last.title ? clip.title : current));
    setRange((current) => (current.start === last.start && current.end === last.end ? { start: clip.start_time, end: clip.end_time } : current));
  }, [clip.title, clip.start_time, clip.end_time]);

  const trimDirty = range.start !== clip.start_time || range.end !== clip.end_time;
  const titleDirty = title.trim() !== clip.title;
  const dirty = trimDirty || titleDirty;
  const trimChanged = range.start !== rendered.start || range.end !== rendered.end;
  const titleChanged = title.trim() !== renderedTitle;

  // Dropped on purpose (saved and leaving, or "Descartar"): stop mirroring the draft to the storage.
  const dropped = useRef(false);
  useEffect(() => {
    if (dropped.current) return;
    const base = { title: clip.title, start: clip.start_time, end: clip.end_time };
    storeEditorDraft(scope, dirty ? { title, start: range.start, end: range.end, base } : null);
  }, [scope, clip.title, clip.start_time, clip.end_time, dirty, range.end, range.start, title]);
  const dropDraft = () => {
    dropped.current = true;
    storeEditorDraft(scope, null);
  };

  // A 409 said the project is rendering before the cache knew. It stops holding once that render ends.
  const lastStatus = useRef(project.status);
  useEffect(() => {
    if (lastStatus.current === 'rendering' && project.status !== 'rendering') setConflict(false);
    lastStatus.current = project.status;
  }, [project.status]);

  const projectRendering = project.status === 'rendering' || conflict;
  const clipRendering = clip.status === 'rendering';
  const hasRender = clip.status === 'completed' || clip.status === 'published';
  const canEdit = can('clip.edit');
  const canTrim = canEdit && !clipRendering && !projectRendering;
  const canRename = canEdit && !clipRendering;
  // A clip of a project still in review is rendered (and paid) by approving the review: rendering it
  // alone here would charge it now and again at the approval.
  const inReviewDraft = clip.status === 'draft' && project.status === 'awaiting_approval';
  const renderVerb = hasRender ? 'Volver a renderizar' : 'Renderizar';
  const lockReason = !canEdit ? NO_ACCESS_REASON : clipRendering ? 'Este clip se está renderizando' : projectRendering ? PROJECT_RENDERING_MESSAGE : undefined;

  // Buying tokens or changing plan keeps the work (the draft is stored); any other link asks first.
  const guard = useUnsavedGuard(dirty, {
    allow: (href) => href.startsWith('/uso/comprar') || href.startsWith('/uso/planes'),
    onDiscard: dropDraft,
  });

  const fit = (next: Range) => setDetail(windowAround(Math.min(next.start, rendered.start), Math.max(next.end, rendered.end), total));
  const setEdge = (edge: 'start' | 'end', value: number, options: { fit?: boolean } = {}) => {
    if (!canTrim) return;
    setFieldError(null);
    setRange((current) => {
      const next =
        edge === 'start'
          ? { ...current, start: clamp(value, 0, current.end - MIN_CLIP_SECONDS) }
          : { ...current, end: clamp(value, current.start + MIN_CLIP_SECONDS, Math.max(total, current.end)) };
      if (options.fit !== false) fit(next);
      return next;
    });
  };
  const snap = (edge: 'start' | 'end', value: number) => (edge === 'start' ? snapStart(words, value) : snapEnd(words, value));

  /* ── Player and free preview ──────────────────────────────────────────── */
  const player = useRef<VideoPlayerHandle>(null);
  const playerBox = useRef<HTMLDivElement>(null);
  const [time, setTime] = useState(0);
  const [watching, setWatching] = useState<'render' | 'preview'>('render');
  const preview = useFreePreview(clip.id);
  useEffect(() => {
    if (preview.state === 'ready') setWatching('preview');
    if (preview.state === 'idle') setWatching('render');
  }, [preview.state]);
  const showingPreview = watching === 'preview' && preview.state === 'ready';
  const shown: Range = showingPreview ? { start: clip.start_time, end: clip.end_time } : rendered;
  const shownDuration = shown.end - shown.start;

  // I, O, space and the arrows work from anywhere on the screen.
  const shortcutRef = useRef({ setEdge, snap, shown });
  shortcutRef.current = { setEdge, snap, shown };
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (target?.closest?.('input, textarea, select, [contenteditable="true"], [role="dialog"], [role="alertdialog"], [role="slider"], [role="menu"]')) return;
      if (target && playerBox.current?.contains(target)) return;
      const handle = player.current;
      if (!handle) return;
      const key = event.key;
      const at = shortcutRef.current.shown.start + handle.getCurrentTime();
      if (key === ' ') {
        if (target?.closest?.('button, a, summary, [role="button"], [role="switch"]')) return;
        event.preventDefault();
        handle.toggle();
      } else if (key === 'ArrowLeft' || key === 'ArrowRight') {
        event.preventDefault();
        handle.seek(handle.getCurrentTime() + (key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 1 : 5));
      } else if (key === 'i' || key === 'I') {
        event.preventDefault();
        shortcutRef.current.setEdge('start', shortcutRef.current.snap('start', at));
      } else if (key === 'o' || key === 'O') {
        event.preventDefault();
        shortcutRef.current.setEdge('end', shortcutRef.current.snap('end', at));
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  /* ── Cost ─────────────────────────────────────────────────────────────── */
  const resolution: RenderResolution = project.metadata.render_resolution ?? '720p';
  const billedSeconds = billed(range.end - range.start);
  const estimate = pricing.data ? estimateCost({ durationSeconds: billedSeconds, resolution }, pricing.data, plan) : null;
  const balance = wallet.data?.available_balance ?? null;
  const short = estimate !== null && balance !== null && estimate.total > balance;
  const planBlocks = plan !== null && !planAllowsResolution(plan, resolution);
  const [confirming, setConfirming] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  /** Saves title and trim (free). Returns false when it could not. */
  const saveChanges = async (): Promise<boolean> => {
    if (!dirty) return true;
    if (title.trim() === '') {
      setFieldError('Poné un título para el clip.');
      return false;
    }
    try {
      await update.mutateAsync({
        ...(titleDirty ? { title: title.trim() } : {}),
        ...(trimDirty ? { start_time: range.start, end_time: range.end } : {}),
      });
      if (trimDirty && hasRender) setPendingRender(true);
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.isConflict) {
        // 409 project_rendering: what the render reads cannot change under it. DRF sends only
        // `{ detail }`, so without a code the server's own sentence is shown.
        setConflict(true);
        void queryClient.invalidateQueries({ queryKey: keys.projects.detail(project.id) });
        toast.error(error.code === 'project_rendering' ? PROJECT_RENDERING_MESSAGE : errorMessage(error, PROJECT_RENDERING_MESSAGE));
      } else if (error instanceof ApiError && error.status === 400) {
        setFieldError(error.fieldErrors.end_time?.[0] ?? error.fieldErrors.start_time?.[0] ?? error.fieldErrors.title?.[0] ?? error.message);
      } else {
        toast.error(errorMessage(error, 'No pudimos guardar los cambios. Probá de nuevo.'));
      }
      return false;
    }
  };

  const askToRender = () => {
    if (!estimate || inReviewDraft) return;
    if (title.trim() === '') {
      setFieldError('Poné un título para el clip.');
      return;
    }
    setSaveError(null);
    if (planBlocks) billing.showPlanLimit({ kind: 'resolution', detail: `Este proyecto se creó en ${resolution} y el plan ${plan?.name ?? 'actual'} renderiza hasta ${plan?.max_resolution ?? '720p'}.` });
    else if (short) billing.showInsufficientBalance({ required: estimate.total, concept: 'Costo del render' });
    else setConfirming(true);
  };

  const saveAndRender = async () => {
    setSaveError(null);
    const hadChanges = dirty;
    if (!(await saveChanges())) {
      setConfirming(false);
      return;
    }
    try {
      await renderClip.mutateAsync();
      toast.success('Render en marcha. Te avisamos cuando esté listo.');
      dropDraft();
      guard.leaveNow(backHref);
    } catch (error) {
      if (billing.handleError(error, { concept: 'Costo del render' })) {
        setConfirming(false);
        if (hadChanges) toast.show('Guardamos los cambios. Falta renderizar el clip.', { tone: 'info' });
      } else if (error instanceof ApiError && error.isConflict) {
        setConfirming(false);
        const message = error.code === 'project_rendering' ? PROJECT_RENDERING_MESSAGE : errorMessage(error, 'El clip ya se está renderizando.');
        if (error.code === 'project_rendering' || /proyecto/i.test(message)) {
          setConflict(true);
          void queryClient.invalidateQueries({ queryKey: keys.projects.detail(project.id) });
        }
        toast.error(message);
      } else {
        setSaveError(errorMessage(error, 'No pudimos empezar el render. Probá de nuevo.'));
      }
    }
  };

  const saveOnly = async () => {
    const onlyTitle = !trimDirty;
    if (await saveChanges()) {
      setRenderedTitle(title.trim());
      toast.success(onlyTitle ? 'Título guardado' : 'Cambios guardados');
    }
  };

  const askPreview = async () => {
    if (dirty && !(await saveChanges())) return;
    preview.request();
  };

  const onDownload = () =>
    download.mutate(clip.id, {
      onSuccess: (file) => {
        downloadUrl(file.url, file.filename);
        toast.success('Clip descargado');
      },
      onError: (error) => toast.error(errorMessage(error, 'No pudimos preparar la descarga. Probá de nuevo.')),
    });

  /* ── Transcript of the stretch ────────────────────────────────────────── */
  const stretch = useMemo(() => {
    const from = Math.min(range.start, rendered.start) - 8;
    const to = Math.max(range.end, rendered.end) + 8;
    return rows.filter((row) => row.end >= from && row.start <= to);
  }, [range.end, range.start, rendered.end, rendered.start, rows]);
  const inRange = (word: Word, target: Range) => word.end > target.start + 0.01 && word.start < target.end - 0.01;
  const firstRow = stretch.find((row) => words.slice(row.from, row.to + 1).some((word) => inRange(word, range)))?.index;
  const lastRow = [...stretch].reverse().find((row) => words.slice(row.from, row.to + 1).some((word) => inRange(word, range)))?.index;

  const changes = [trimDirty || (pendingRender && trimChanged) ? 'recorte' : null, titleDirty ? 'título' : null].filter(Boolean).join(' y ');
  const styleRows = describeStyle(project);
  const status = CLIP_STATUS[clip.status];
  const renderLabel = estimate ? formatTokens(estimate.total) : '';
  const costLine = estimate ? (
    <>
      {isMobile ? null : 'Renderizar '}
      <span className="num">{formatDuration(range.end - range.start)}</span> en <span className="num">{resolution}</span>
      {!isMobile && estimate.discountRate > 0 ? (
        <>
          {' '}
          con <span className="num">{formatPercent(estimate.discountRate)}</span> de descuento del plan {plan?.name}
        </>
      ) : null}
      {balance === null ? null : short ? (
        <>
          {' '}
          ·{' '}
          <span style={{ color: 'var(--warn)' }}>
            Te faltan <span className="num">{formatTokens(estimate.total - balance)}</span>
          </span>
        </>
      ) : (
        <>
          {' '}
          · Saldo después <span className="num">{formatTokens(balance - estimate.total)}</span>
        </>
      )}
    </>
  ) : (
    'Calculando el costo…'
  );

  const nudge = (edge: 'start' | 'end') => (
    <div className={styles.nudge}>
      <Button className={cx('num', styles.nudgeBtn)} disabledReason={lockReason} aria-label={`Mover el ${edge === 'start' ? 'inicio' : 'fin'} 0,5 segundos antes`} onClick={() => setEdge(edge, range[edge] - 0.5)}>
        −0,5 s
      </Button>
      {isMobile ? (
        <output className={cx('timecode', styles.val)}>{formatEdge(range[edge])}</output>
      ) : (
        <EdgeInput id={`d4-${edge}`} className={styles.edgeInput} value={range[edge]} readOnly={!canTrim} readOnlyReason={lockReason} onCommit={(value) => setEdge(edge, snap(edge, value))} />
      )}
      <Button className={cx('num', styles.nudgeBtn)} disabledReason={lockReason} aria-label={`Mover el ${edge === 'start' ? 'inicio' : 'fin'} 0,5 segundos después`} onClick={() => setEdge(edge, range[edge] + 0.5)}>
        +0,5 s
      </Button>
    </div>
  );
  const was = (edge: 'start' | 'end') =>
    range[edge] !== rendered[edge] ? (
      <p className={styles.was}>
        Antes: <s className="timecode">{formatEdge(rendered[edge])}</s>
      </p>
    ) : null;

  // Mock media (`mock:` URLs) has no file behind it: the shared player shows its placeholder for those.
  const src = showingPreview ? preview.preview?.url : clip.preview_url;

  return (
    <>
      <Page width="wide" flush className={styles.page}>
        <ProjectHeader
          compact
          crumbs={[
            { label: 'Proyectos', href: '/proyectos' },
            { label: project.title, href: `/proyectos/${project.id}` },
            { label: backLabel, href: backHref },
            { label: 'Editar clip' },
          ]}
          title={clip.title}
          badge={
            dirty ? (
              <Badge dot tone="warn">
                Cambios sin guardar
              </Badge>
            ) : (
              <Badge dot tone={status.tone}>
                {status.label}
              </Badge>
            )
          }
          actions={
            <Button
              size="sm"
              iconLeft="download"
              title="Descarga el render actual, sin los cambios"
              loading={download.isPending}
              disabledReason={hasRender ? gate('clip.download').disabledReason : 'Todavía no hay un render para descargar'}
              onClick={onDownload}
            >
              Descargar
            </Button>
          }
        />
        {loadError ? <LoadError error={loadError} fullPageForMissing={false} className={styles.banner} /> : null}
        {!canEdit ? (
          <Banner tone="info" className={styles.banner} title="Estás viendo el clip en modo lectura">
            Podés reproducirlo y descargarlo. Para cambiar el recorte o el título, pedile acceso a un admin.
          </Banner>
        ) : clipRendering ? (
          <Banner tone="info" className={styles.banner} title="Este clip se está renderizando">
            Cuando termine vas a poder editarlo de nuevo.
          </Banner>
        ) : projectRendering ? (
          <Banner tone="warn" className={styles.banner} title={PROJECT_RENDERING_MESSAGE}>
            Mientras tanto podés cambiar el título.
          </Banner>
        ) : clip.status === 'failed' ? (
          <Banner
            tone="err"
            className={styles.banner}
            title="No se pudo generar. Reintentar no gasta tokens."
            action={
              <Button
                size="sm"
                iconLeft="retry"
                loading={retry.isPending}
                onClick={() => retry.mutate(clip.id, { onError: (error) => toast.error(errorMessage(error, 'No pudimos reintentar. Probá de nuevo.')) })}
              >
                Reintentar
              </Button>
            }
          />
        ) : clip.status === 'draft' ? (
          <Banner
            tone="info"
            className={styles.banner}
            title="Este clip todavía no se renderizó"
            action={
              project.status === 'awaiting_approval' ? (
                <Button size="sm" href={`/proyectos/${project.id}/revision`}>
                  Ir a la revisión
                </Button>
              ) : undefined
            }
          />
        ) : null}
        {isMobile ? (
          <Banner tone="neutral" className={styles.banner} title="Para editar con más detalle, usá una computadora">
            En el teléfono movés los bordes de a <span className="num">0,5 s</span> y cambiás el título.
          </Banner>
        ) : null}

        <div className={styles.editor}>
          <Card as="section" className={styles.playerCard} aria-label="Reproductor del clip">
            <div className={styles.stage} ref={playerBox}>
              <VideoPlayer
                ref={player}
                className={styles.player}
                label={`Reproductor: ${clip.title}`}
                src={src}
                seed={clip.id}
                ratio={project.aspect_ratio}
                duration={shownDuration}
                captionColor={project.add_subtitles ? project.subtitle_color : undefined}
                onTimeUpdate={setTime}
                onMarkIn={(seconds) => setEdge('start', snap('start', shown.start + seconds))}
                onMarkOut={(seconds) => setEdge('end', snap('end', shown.start + seconds))}
                overlay={
                  <span className={styles.vchip}>
                    {showingPreview ? 'Vista previa · 360p' : hasRender ? 'Render actual' : 'Sin render todavía'}
                    {showingPreview || hasRender ? (
                      <>
                        {' '}
                        · <span className="num">{formatDuration(shownDuration)}</span>
                      </>
                    ) : null}
                  </span>
                }
              />
            </div>
            <div className={styles.badges}>
              {preview.state === 'ready' ? (
                <SegmentedControl<'render' | 'preview'>
                  ariaLabel="Qué ver"
                  value={watching}
                  onChange={setWatching}
                  options={[
                    { value: 'render', label: 'Render actual' },
                    { value: 'preview', label: 'Vista previa' },
                  ]}
                />
              ) : (
                <span />
              )}
              <span className={styles.badgeRow}>
                <Badge tone="outline">
                  <span className="num">{project.aspect_ratio}</span>
                </Badge>
                <Badge tone="outline">
                  <span className="num">{resolution}</span>
                </Badge>
              </span>
            </div>
            <div className={styles.pnote}>
              <p role="note">
                <Icon name="info" size={14} />
                <span>
                  {showingPreview
                    ? 'Estás viendo la vista previa del recorte guardado: 360p, sin seguimiento de caras ni corte de silencios.'
                    : trimChanged
                      ? 'Estás viendo el render actual. El recorte nuevo se ve cuando termina el render.'
                      : 'Estás viendo el render actual.'}
                </span>
              </p>
              {wholeVideo ? (
                <p>
                  <Icon name="eye-off" size={14} />
                  <span>El video completo se renderiza entero: no tiene vista previa.</span>
                </p>
              ) : (
                <div className={styles.previewRow}>
                  <Button size="sm" iconLeft="eye" loading={preview.state === 'working' || (update.isPending && !confirming)} disabledReason={dirty && !canEdit ? NO_ACCESS_REASON : undefined} onClick={() => void askPreview()}>
                    {dirty ? 'Guardar y ver vista previa' : preview.state === 'ready' || preview.state === 'failed' ? 'Pedir otra vista previa' : 'Vista previa gratis'}
                  </Button>
                  <span role="status" className={preview.state === 'failed' || preview.error ? styles.previewErr : undefined}>
                    {preview.error
                      ? preview.error
                      : preview.state === 'working'
                        ? 'Preparando la vista previa… tarda unos segundos.'
                        : preview.state === 'failed'
                          ? 'No pudimos generar la vista previa.'
                          : preview.state === 'ready'
                            ? 'Lista. Es gratis y vence en una hora.'
                            : 'En 360p, gratis: mirá el recorte antes de pagar el render.'}
                  </span>
                </div>
              )}
              <p>
                <Icon name="settings" size={14} />
                <span>
                  El estilo se cambia en el proyecto y aplica a todos sus clips.{' '}
                  <Link className="lnk" href={`/proyectos/${project.id}`}>
                    Ir a la configuración
                  </Link>
                </span>
              </p>
            </div>
            <dl className={styles.style} aria-label="Estilo del proyecto">
              {styleRows.map((row) => (
                <div key={row.key}>
                  <dt>{row.label}</dt>
                  <dd>
                    {row.swatch ? <i className={styles.swatch} style={{ background: row.swatch }} aria-hidden="true" /> : null}
                    {row.value}
                  </dd>
                </div>
              ))}
            </dl>
          </Card>

          <div className={styles.col}>
            <Card as="section" className={styles.tlCard} aria-label="Recorte del clip">
              <CardHead
                actions={
                  isMobile ? (
                    <span className={cx('num', styles.mDur)}>
                      Duración {trimChanged ? <s>{formatDuration(rendered.end - rendered.start)}</s> : null}
                      <b>{formatDuration(range.end - range.start)}</b>
                    </span>
                  ) : (
                    <div className={timeline.legend}>
                      <span>
                        <i className={cx(timeline.sw, timeline.swSel)} />
                        Recorte nuevo
                      </span>
                      <span>
                        <i className={cx(timeline.sw, timeline.swCur)} />
                        Render actual
                      </span>
                      <span>
                        <i className={cx(timeline.sw, timeline.hatch, timeline.swHatch)} />
                        Agregado, sin vista previa
                      </span>
                    </div>
                  )
                }
              >
                <h2 className="t-h3">Recorte</h2>
              </CardHead>
              {isMobile ? null : (
                <div className={styles.tlBody}>
                  <TrimTimeline
                    tall
                    words={words}
                    window={detail}
                    start={range.start}
                    end={range.end}
                    rendered={rendered}
                    time={shown.start + time}
                    seed={project.id}
                    disabled={!canTrim}
                    onSeek={(seconds) => player.current?.seek(seconds - shown.start)}
                    onDrag={(edge, seconds) => setEdge(edge, snap(edge, seconds), { fit: false })}
                    onStep={(edge, direction) => setEdge(edge, stepEdge(words, edge, range[edge], direction))}
                  />
                </div>
              )}
              <div className={styles.trim}>
                <div className={styles.field} role="group" aria-label="Inicio del clip">
                  <label className={styles.fieldLabel} htmlFor={isMobile ? undefined : 'd4-start'}>
                    Inicio {isMobile ? null : <Kbd>I</Kbd>}
                  </label>
                  {nudge('start')}
                  {was('start')}
                </div>
                <div className={styles.field} role="group" aria-label="Fin del clip">
                  <label className={styles.fieldLabel} htmlFor={isMobile ? undefined : 'd4-end'}>
                    Fin {isMobile ? null : <Kbd>O</Kbd>}
                  </label>
                  {nudge('end')}
                  {was('end')}
                </div>
                <div className={styles.dur}>
                  {isMobile ? null : (
                    <>
                      <span className={styles.fieldLabel}>Duración</span>
                      <b className="num">
                        {trimChanged ? <s>{formatDuration(rendered.end - rendered.start)}</s> : null}
                        {formatDuration(range.end - range.start)}
                      </b>
                    </>
                  )}
                  <p className={styles.was}>{isMobile ? 'Los bordes se ajustan a palabras completas.' : 'Arrastrá los bordes o escribí el tiempo. Se ajustan a palabras completas.'}</p>
                </div>
              </div>
            </Card>
            <Card as="section" className={styles.titleCard} aria-label="Título del clip">
              <Field label="Título" error={fieldError ?? undefined}>
                <Input
                  value={title}
                  maxLength={255}
                  readOnly={!canRename}
                  title={canEdit ? undefined : NO_ACCESS_REASON}
                  onChange={(event) => {
                    setTitle(event.target.value);
                    setFieldError(null);
                  }}
                />
              </Field>
              {titleChanged ? (
                <p className={cx(styles.was, styles.titleWas)}>
                  Antes: <s>{renderedTitle}</s>
                </p>
              ) : null}
            </Card>
          </div>
        </div>

        <Card as="section" className={styles.trCard} aria-label="Transcripción del tramo">
          <CardHead
            actions={
              <span className="timecode muted">
                {formatTimecode(range.start)}–{formatTimecode(range.end)}
              </span>
            }
          >
            <h2 className="t-h3">Transcripción del tramo</h2>
          </CardHead>
          {stretch.length === 0 ? (
            <p className={cx('t-small muted', styles.noText)}>Este tramo no tiene transcripción.</p>
          ) : (
            <div className={styles.segTr}>
              {stretch.map((row) => {
                const rowWords = words.slice(row.from, row.to + 1);
                const anyIn = rowWords.some((word) => inRange(word, range));
                return (
                  <Fragment key={row.index}>
                    {firstRow === row.index ? (
                      <div className={cx('timecode', styles.edge)}>
                        Inicio · {formatTimecode(range.start)} {range.start !== rendered.start ? <s>{formatTimecode(rendered.start)}</s> : null}
                      </div>
                    ) : null}
                    <div className={cx(styles.trRow, !anyIn && styles.ctx)}>
                      <span className="timecode">{formatTimecode(row.start)}</span>
                      <p>
                        {rowWords.map((word, index) => {
                          const isIn = inRange(word, range);
                          const added = isIn && !inRange(word, rendered);
                          return (
                            <span key={index} className={cx(!isIn && styles.out, added && styles.add)}>
                              {word.text}{' '}
                            </span>
                          );
                        })}
                      </p>
                    </div>
                    {lastRow === row.index ? (
                      <div className={cx('timecode', styles.edge)}>
                        Fin · {formatTimecode(range.end)} {range.end !== rendered.end ? <s>{formatTimecode(rendered.end)}</s> : null}
                      </div>
                    ) : null}
                  </Fragment>
                );
              })}
            </div>
          )}
        </Card>
      </Page>

      <ActionBar
        className={styles.bar}
        summary={dirty ? `Cambios sin guardar: ${changes}` : pendingRender ? 'El recorte guardado todavía no se renderizó' : 'Sin cambios'}
        detail={
          inReviewDraft
            ? 'Este clip se renderiza al aprobar la revisión del proyecto. Guardar el recorte o el título no gasta tokens.'
            : trimDirty || !dirty
              ? costLine
              : 'Cambiar el título no vuelve a renderizar ni gasta tokens.'
        }
      >
        {dirty ? (
          <Button
            variant="ghost"
            size="lg"
            onClick={() => {
              dropDraft();
              guard.leaveNow(backHref);
            }}
          >
            {isMobile ? 'Descartar' : 'Descartar cambios'}
          </Button>
        ) : (
          <Button variant="ghost" size="lg" href={backHref}>
            {isMobile ? 'Volver' : `Volver a ${backLabel}`}
          </Button>
        )}
        {dirty && (inReviewDraft || !trimDirty) ? (
          <Button variant="primary" size="lg" loading={update.isPending} onClick={() => void saveOnly()} {...gate('clip.edit')}>
            {trimDirty ? 'Guardar cambios' : 'Guardar título'}
          </Button>
        ) : inReviewDraft ? (
          <Button variant="secondary" size="lg" disabledReason={gate('clip.edit').disabledReason ?? 'Renderizalo desde la revisión'}>
            Renderizar
          </Button>
        ) : (
          <Button
            variant={dirty || pendingRender ? 'accent' : 'secondary'}
            size="lg"
            disabled={!estimate || clipRendering || projectRendering}
            onClick={askToRender}
            {...gate('clip.edit')}
          >
            {dirty ? 'Guardar y renderizar' : renderVerb}
            {estimate ? (
              <>
                <span className={styles.dot} aria-hidden="true">
                  ·
                </span>
                <span className="num">{renderLabel}</span>
              </>
            ) : null}
          </Button>
        )}
      </ActionBar>
      {estimate ? (
        <ConfirmCostDialog
          open={confirming}
          onClose={() => setConfirming(false)}
          onConfirm={() => void saveAndRender()}
          title={dirty ? 'Guardar y renderizar' : renderVerb}
          subtitle={title.trim() || clip.title}
          durationSeconds={billedSeconds}
          resolution={resolution}
          estimate={estimate}
          planName={plan?.name}
          balance={balance ?? 0}
          baseRate={pricing.data?.base_cost_per_second_720p}
          confirmLabel="Renderizar"
          loading={update.isPending || renderClip.isPending}
          error={saveError}
        />
      ) : null}
      <UnsavedChangesDialog {...guard.dialog} what={<>el {changes || 'recorte'} nuevo de «{clip.title}»</>} />
    </>
  );
}
