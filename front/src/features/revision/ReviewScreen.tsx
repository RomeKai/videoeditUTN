'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { RenderResolution } from '@/api/contracts/common';
import type { PaperEditData, Project } from '@/api/contracts/projects';
import { useApprovePaperEdit, usePaperEditData, usePricing, useProject, useWallet } from '@/api/hooks';
import { keys } from '@/api/query';
import { ConfirmCostDialog, UnsavedChangesDialog, useBillingDialogs } from '@/dialogs';
import { cx } from '@/lib/cx';
import { approveErrorKind, autoRenderSkippedMessage, errorMessage } from '@/lib/error-messages';
import { formatDuration, formatMinutes, formatNumber, formatPercent, formatTimecode, formatTokens } from '@/lib/format';
import { useIsMobile } from '@/lib/hooks';
import { NO_ACCESS_REASON } from '@/lib/permissions';
import { estimateCost, planAllowsResolution } from '@/lib/pricing';
import { useLeaveGuard } from '@/shell/leaveGuard';
import { LoadError } from '@/shell/LoadError';
import { usePermissions, usePlan, useSession } from '@/shell/session';
import {
  ActionBar,
  Badge,
  Banner,
  Button,
  Card,
  Field,
  Icon,
  IconButton,
  Input,
  isPlaceholderSource,
  Kbd,
  Page,
  Popover,
  ProjectHeader,
  SegmentedControl,
  Skeleton,
  Switch,
  useToast,
  VideoPlayer,
  type VideoPlayerHandle,
} from '@/ui';
import { ClipList } from './components/ClipList';
import { EdgeInput } from './components/EdgeInput';
import { TranscriptPanel } from './components/TranscriptPanel';
import { TrimTimeline, windowAround, type TimeWindow } from './components/TrimTimeline';
import timeline from './components/timeline.module.css';
import { buildApprovedSegments, clearDraft, draftReducer, draftScope, includedTotals, isDirty, newLocalKey, restoreDraft, saveDraft, sortClips, type ClipSort, type Draft } from './lib/draft';
import { billedSeconds as billed, clamp, rulerTicks } from './lib/time';
import { buildRows, captionAt, snapEnd, snapStart, stepEdge, titleFromText, wordIndexAt, type Word } from './lib/transcript';
import styles from './review.module.css';
import { useFreePreview } from './useFreePreview';

type View = 'crop' | 'original';

/** Why "Renderizar" waits while the review is being fetched again. */
const REFRESHING_REASON = 'Actualizando la revisión…';

const SHORTCUTS: Array<{ keys: string[]; label: string }> = [
  { keys: ['espacio'], label: 'Reproducir o pausar' },
  { keys: ['↑', '↓'], label: 'Cambiar de clip' },
  { keys: ['A'], label: 'Incluir el clip' },
  { keys: ['X'], label: 'Excluir el clip' },
  { keys: ['I', 'O'], label: 'Marcar inicio y fin' },
  { keys: ['←', '→'], label: 'Mover el cursor' },
  { keys: ['⇧', '←', '→'], label: 'En el texto: seleccionar palabras' },
  { keys: ['espacio'], label: 'En el texto: marcar o desmarcar la palabra' },
  { keys: ['↵'], label: 'En el texto: ir a esa palabra en el video' },
];

function ShortcutsButton({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  return (
    <Popover
      ariaLabel="Atajos de teclado"
      align="end"
      open={open}
      onOpenChange={onOpenChange}
      trigger={(props) => (
        <Button {...props} size="sm" iconLeft="info" aria-label="Atajos de teclado">
          Atajos <Kbd>?</Kbd>
        </Button>
      )}
    >
      <dl className={styles.keys}>
        {SHORTCUTS.map((shortcut) => (
          <div key={shortcut.label}>
            <dt>
              {shortcut.keys.map((key) => (
                <Kbd key={key}>{key}</Kbd>
              ))}
            </dt>
            <dd>{shortcut.label}</dd>
          </div>
        ))}
      </dl>
    </Popover>
  );
}

function ReviewSkeleton() {
  return (
    <div className={styles.review} aria-busy="true" aria-label="Cargando la revisión">
      <Card className={styles.skCard}>
        {Array.from({ length: 6 }, (_, index) => (
          <div key={index} className={styles.skRow}>
            <Skeleton width={32} height={56} radius={4} />
            <div>
              <Skeleton width="70%" height={14} />
              <Skeleton width="45%" height={12} />
            </div>
          </div>
        ))}
      </Card>
      <div className={styles.col}>
        <Card className={styles.skCard}>
          <Skeleton height={0} style={{ paddingBottom: '56.25%' }} radius={8} />
          <Skeleton width="40%" height={14} />
        </Card>
        <Card className={styles.skCard}>
          <Skeleton height={96} radius={6} />
        </Card>
      </div>
      <Card className={styles.skCard}>
        {Array.from({ length: 8 }, (_, index) => (
          <Skeleton key={index} width={`${90 - (index % 3) * 12}%`} height={14} />
        ))}
      </Card>
    </div>
  );
}

function durationOf(data: PaperEditData, project: Project | undefined, words: ReadonlyArray<Word>): number {
  const lastWord = words[words.length - 1]?.end ?? 0;
  const lastClip = data.ai_suggestions.reduce((max, clip) => Math.max(max, clip.end_time), 0);
  return Math.max(project?.duration_seconds ?? 0, data.metadata.source_media?.duration_s ?? 0, data.metadata.duration ?? 0, lastWord, lastClip, 1);
}

/** D2 · Revisión de clips. */
export function ReviewScreen({ projectId }: { projectId: string }) {
  const paperEdit = usePaperEditData(projectId);
  const { me } = useSession();
  // The draft is stored per person: with the data in hand, wait for the session before reading it.
  if (paperEdit.isLoading || (paperEdit.data && !me)) {
    return (
      <Page width="wide" className={styles.page}>
        <ProjectHeader compact crumbs={[{ label: 'Proyectos', href: '/proyectos' }, { label: 'Revisión' }]} title="Revisión de clips" />
        <ReviewSkeleton />
      </Page>
    );
  }
  if (!paperEdit.data || !me) {
    return (
      <Page width="wide" className={styles.page}>
        <LoadError error={paperEdit.error} onRetry={() => void paperEdit.refetch()} />
      </Page>
    );
  }
  return (
    <Review
      key={`${me.id}.${projectId}`}
      projectId={projectId}
      userId={me.id}
      data={paperEdit.data}
      refreshing={paperEdit.isFetching}
      loadError={paperEdit.error}
      onRetry={() => void paperEdit.refetch()}
    />
  );
}

interface ReviewProps {
  projectId: string;
  userId: string;
  data: PaperEditData;
  /** The review is being fetched again: what is on screen may be about to change. */
  refreshing: boolean;
  loadError: unknown;
  onRetry: () => void;
}

function Review({ projectId, userId, data, refreshing, loadError, onRetry }: ReviewProps) {
  const toast = useToast();
  const isMobile = useIsMobile();
  const { can, gate } = usePermissions();
  const canEdit = can('project.review');
  const { plan } = usePlan();
  const pricing = usePricing();
  const wallet = useWallet();
  const project = useProject(projectId).data;
  const billing = useBillingDialogs();
  const approve = useApprovePaperEdit(projectId);
  const queryClient = useQueryClient();

  const words = useMemo<Word[]>(() => data.transcript ?? [], [data.transcript]);
  const rows = useMemo(() => buildRows(words), [words]);
  const total = durationOf(data, project, words);
  const inReview = data.status === 'awaiting_approval';

  const [fitNonce, setFitNonce] = useState(0);
  const scope = draftScope(userId, projectId);
  const [draft, dispatch] = useReducer(draftReducer, undefined, (): Draft => restoreDraft(scope, data.ai_suggestions));
  // The first data can come from the cache while it is fetched again (a trim just saved in the clip
  // editor, a teammate's change). When the fresh suggestions arrive, every clip nobody touched follows them.
  const seenSuggestions = useRef(data.ai_suggestions);
  useEffect(() => {
    if (seenSuggestions.current === data.ai_suggestions) return;
    seenSuggestions.current = data.ai_suggestions;
    dispatch({ type: 'sync', suggestions: data.ai_suggestions });
    setFitNonce((nonce) => nonce + 1);
  }, [data.ai_suggestions]);
  const dirty = isDirty(draft);
  useEffect(() => {
    saveDraft(scope, draft);
  }, [draft, scope]);

  const [sort, setSort] = useState<ClipSort>('score');
  const [view, setView] = useState<View>('crop');
  const [solo, setSolo] = useState(true);
  const [time, setTime] = useState(0);
  const [tab, setTab] = useState<'clips' | 'text'>('clips');
  const [keysOpen, setKeysOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [approveError, setApproveError] = useState<string | null>(null);
  const player = useRef<VideoPlayerHandle>(null);
  const playerBox = useRef<HTMLDivElement>(null);
  const playNext = useRef(false);

  const ordered = useMemo(() => sortClips(draft.clips, sort), [draft.clips, sort]);
  const selected = draft.clips.find((clip) => clip.key === draft.selectedKey) ?? null;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const selectedIndex = selected ? ordered.findIndex((clip) => clip.key === selected.key) : -1;

  /* A project that left the review goes to its clips (or to its summary when it has none yet). */
  const destination = data.status === 'rendering' || data.status === 'completed' || data.status === 'partial' ? `/proyectos/${projectId}/clips` : `/proyectos/${projectId}`;
  const guard = useLeaveGuard(dirty && inReview, {
    allow: (href) => href.startsWith('/uso/comprar') || href.startsWith('/uso/planes'),
    onDiscard: () => clearDraft(scope),
  });
  const { leaveNow } = guard;
  useEffect(() => {
    if (!inReview) {
      clearDraft(scope);
      // Through the guard: it takes its history entry away first, so the replace lands on the review's own entry.
      leaveNow(destination, { replace: true });
    }
  }, [destination, inReview, scope, leaveNow]);

  /* The stretch of video the detail lanes show: follows the selection, and re-fits after an edit that is not a drag. */
  const [detail, setDetail] = useState<TimeWindow>(() => (selected ? windowAround(selected.start, selected.end, total) : { from: 0, to: total }));
  const refit = useCallback(() => {
    const clip = selectedRef.current;
    setDetail(clip ? windowAround(clip.start, clip.end, total) : { from: 0, to: total });
  }, [total]);
  useEffect(refit, [refit, draft.selectedKey, fitNonce]);

  // Outside "Solo el clip" the player keeps the whole video: move the cursor to the clip that was picked.
  useEffect(() => {
    const clip = selectedRef.current;
    if (clip && !solo) player.current?.seek(clip.start);
    if (playNext.current) {
      playNext.current = false;
      player.current?.play();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft.selectedKey]);

  const select = useCallback((key: string, options: { play?: boolean } = {}) => {
    if (options.play) {
      if (selectedRef.current?.key === key) player.current?.toggle();
      else playNext.current = true;
    }
    dispatch({ type: 'select', key });
  }, []);

  const setEdge = useCallback(
    (edge: 'start' | 'end', value: number, { fit = true }: { fit?: boolean } = {}) => {
      const clip = selectedRef.current;
      if (!clip || !canEdit) return;
      dispatch({ type: 'edge', key: clip.key, edge, value, total });
      if (fit) setFitNonce((nonce) => nonce + 1);
    },
    [canEdit, total],
  );
  const snap = useCallback((edge: 'start' | 'end', value: number) => (edge === 'start' ? snapStart(words, value) : snapEnd(words, value)), [words]);

  const move = useCallback(
    (delta: number) => {
      if (ordered.length === 0) return;
      const next = ordered[clamp(selectedIndex + delta, 0, ordered.length - 1)];
      if (next) dispatch({ type: 'select', key: next.key });
    },
    [ordered, selectedIndex],
  );

  /** Keys of the review that are not the player's own. Returns true when the key did something. */
  const shortcut = useCallback(
    (key: string): boolean => {
      const clip = selectedRef.current;
      switch (key) {
        case 'ArrowUp':
          move(-1);
          return true;
        case 'ArrowDown':
          move(1);
          return true;
        case 'a':
        case 'A':
          if (clip && canEdit) dispatch({ type: 'toggle', key: clip.key, included: true });
          return true;
        case 'x':
        case 'X':
          if (clip && canEdit) dispatch({ type: 'toggle', key: clip.key, included: false });
          return true;
        case '?':
          setKeysOpen((open) => !open);
          return true;
        default:
          return false;
      }
    },
    [canEdit, move],
  );

  // The shortcuts work anywhere on the screen, not only with the focus on the player.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target instanceof HTMLElement ? event.target : null;
      if (target?.closest?.('input, textarea, select, [contenteditable="true"], [role="dialog"], [role="alertdialog"], [role="slider"], [role="menu"]')) return;
      if (target && playerBox.current?.contains(target)) return;
      const key = event.key;
      const handle = player.current;
      if (key === ' ') {
        if (target?.closest?.('button, a, summary, [role="button"], [role="switch"]')) return;
        event.preventDefault();
        handle?.toggle();
      } else if (key === 'ArrowLeft' || key === 'ArrowRight') {
        event.preventDefault();
        handle?.seek(handle.getCurrentTime() + (key === 'ArrowLeft' ? -1 : 1) * (event.shiftKey ? 1 : 5));
      } else if (key === 'i' || key === 'I') {
        event.preventDefault();
        if (handle) setEdge('start', snap('start', handle.getCurrentTime()));
      } else if (key === 'o' || key === 'O') {
        event.preventDefault();
        if (handle) setEdge('end', snap('end', handle.getCurrentTime()));
      } else if (shortcut(key)) {
        event.preventDefault();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [setEdge, shortcut, snap]);

  /* ── Cost ─────────────────────────────────────────────────────────────── */
  const totals = includedTotals(draft.clips);
  const resolution: RenderResolution = data.metadata.render_resolution ?? '720p';
  const billedSeconds = billed(totals.seconds);
  const estimate = pricing.data && totals.count > 0 ? estimateCost({ durationSeconds: billedSeconds, resolution }, pricing.data, plan) : null;
  const balance = wallet.data?.available_balance ?? null;
  const short = estimate !== null && balance !== null && estimate.total > balance;
  const planBlocks = plan !== null && !planAllowsResolution(plan, resolution);
  // PricingEngine also refuses a render longer than the plan's video limit.
  const tooLong = plan !== null && billedSeconds > plan.max_video_duration_seconds;

  const askToRender = () => {
    if (!estimate) return;
    setApproveError(null);
    if (planBlocks) billing.showPlanLimit({ kind: 'resolution', detail: `Este proyecto se creó en ${resolution} y el plan ${plan?.name ?? 'actual'} renderiza hasta ${plan?.max_resolution ?? '720p'}.` });
    else if (tooLong) {
      billing.showPlanLimit({
        kind: 'duration',
        detail: `Elegiste ${formatMinutes(billedSeconds)} de clips y el plan ${plan?.name ?? 'actual'} renderiza hasta ${formatMinutes(plan?.max_video_duration_seconds ?? 0)}. Sacá alguno o cambiá de plan.`,
      });
    } else if (short) billing.showInsufficientBalance({ required: estimate.total, concept: 'Costo del render' });
    else setConfirming(true);
  };

  const render = () => {
    const segments = buildApprovedSegments(draft.clips, words);
    setApproveError(null);
    approve.mutate(
      { approved_segments: segments },
      {
        onSuccess: () => {
          clearDraft(scope);
          toast.success(`Render en marcha: ${segments.length === 1 ? '1 clip' : `${segments.length} clips`}`);
          // Replace: the review is over, Back must not land on it (it would bounce to Clips again).
          guard.leaveNow(`/proyectos/${projectId}/clips`, { replace: true });
        },
        onError: (error) => {
          // Today the backend answers 400 `{ error }` for a review that was already approved and for a
          // plan limit; 409 only when two approvals race. approveErrorKind() tells them apart.
          const kind = approveErrorKind(error);
          if (kind?.kind === 'not_awaiting_approval') {
            setConfirming(false);
            clearDraft(scope);
            // Drop what the cache still says about the review (the paper-edit data hangs from this key),
            // so coming back to /revision redirects right away instead of showing the stale review.
            void queryClient.invalidateQueries({ queryKey: keys.projects.detail(projectId) });
            toast.show(kind.message, { tone: 'info' });
            guard.leaveNow(`/proyectos/${projectId}/clips`, { replace: true });
          } else if (kind?.kind === 'insufficient_funds' && billing.handleError(error, { concept: 'Costo del render' })) {
            // 402 opens "Saldo insuficiente"; the selection stays as it is.
            setConfirming(false);
          } else if (kind?.kind === 'plan_limit') {
            setConfirming(false);
            billing.showPlanLimit({ kind: kind.limit ?? 'unknown', detail: kind.message });
          } else {
            setApproveError(kind?.message ?? errorMessage(error, 'No pudimos empezar el render. Probá de nuevo.'));
          }
        },
      },
    );
  };

  /* ── Player ───────────────────────────────────────────────────────────── */
  const aspect = project?.aspect_ratio ?? '9:16';
  const activeWord = wordIndexAt(words, time);
  const caption =
    view === 'crop' && (project?.add_subtitles ?? true) && selected && time >= selected.start && time <= selected.end
      ? captionAt(words, time, selected.start, project?.subtitle_words_per_segment ?? 3)
      : null;
  const preview = useFreePreview(selected?.clipId ?? null);
  const previewBlocked = !selected
    ? 'Elegí un clip'
    : !selected.clipId
      ? 'La vista previa está disponible para los clips sugeridos'
      : selected.original && (selected.start !== selected.original.start || selected.end !== selected.original.end)
        ? 'La vista previa muestra el recorte sugerido, sin tus ajustes'
        : undefined;

  const viewSwitch = (
    <SegmentedControl<View>
      ariaLabel="Qué ver"
      value={view}
      onChange={setView}
      touch={isMobile}
      options={[
        { value: 'original', label: 'Original' },
        { value: 'crop', label: `Recorte ${aspect}` },
      ]}
    />
  );

  // "Renderizar sin revisar" was on but could not be paid: the project waits here instead.
  const skipped = autoRenderSkippedMessage(data.metadata.auto_render_skipped);
  const noSuggestions = data.ai_suggestions.length === 0;
  const empty = draft.clips.length === 0;
  const statusText = noSuggestions
    ? 'No encontramos momentos que funcionen solos. Elegí momentos desde la transcripción.'
    : `Encontramos ${data.ai_suggestions.length === 1 ? '1 clip' : `${data.ai_suggestions.length} clips`}. Elegí cuáles renderizar y ajustá los bordes antes de pagar el render.`;

  if (!inReview) {
    return (
      <Page width="wide" className={styles.page}>
        <ProjectHeader compact title={data.title} statusText="Este proyecto ya no está en revisión. Te llevamos a sus clips." actions={<Button href={destination}>Ir al proyecto</Button>} />
      </Page>
    );
  }

  const clipsPanel = (
    <Card as="section" className={styles.panel} aria-label={noSuggestions ? 'Tus clips' : 'Clips sugeridos'}>
      {isMobile ? null : (
        <div className={styles.panelHead}>
          <div className={styles.panelTitle}>
            <h2 className="t-body">{noSuggestions ? 'Tus clips' : 'Clips sugeridos'}</h2>
            <Badge size="sm">
              <span className="num">{draft.clips.length}</span>
            </Badge>
          </div>
          {empty ? null : (
            <SegmentedControl<ClipSort>
              ariaLabel="Ordenar clips"
              value={sort}
              onChange={setSort}
              options={[
                { value: 'score', label: 'Puntaje' },
                { value: 'time', label: 'Orden del video' },
              ]}
            />
          )}
        </div>
      )}
      {empty ? (
        <div className={styles.none}>
          <span className={styles.noneIco} aria-hidden="true">
            <Icon name="clips" size={18} />
          </span>
          <div>
            <h3 className="t-body">{noSuggestions ? 'No encontramos momentos que funcionen solos.' : 'No quedan clips en la lista.'}</h3>
            <p className="t-small muted">Elegí momentos desde la transcripción.</p>
          </div>
          <ol>
            <li>
              <span>
                <b>Seleccioná</b> una frase o varias seguidas en el texto.
              </span>
            </li>
            <li>
              <span>
                Tocá <b>Crear clip con la selección</b>. El clip aparece en esta lista.
              </span>
            </li>
            <li>
              <span>
                Repetilo con cada momento y después <b>renderizá</b>.
              </span>
            </li>
          </ol>
        </div>
      ) : (
        <ClipList
          clips={ordered}
          selectedKey={draft.selectedKey}
          canEdit={canEdit}
          captionColor={project?.subtitle_color}
          mobile={isMobile}
          onSelect={select}
          onToggle={(key, included) => dispatch({ type: 'toggle', key, included })}
          onRemove={(key) => dispatch({ type: 'remove', key })}
          onRename={(key, title) => dispatch({ type: 'rename', key, title })}
          onNudge={(key, edge, delta) => {
            const clip = draft.clips.find((item) => item.key === key);
            if (clip) dispatch({ type: 'edge', key, edge, value: (edge === 'start' ? clip.start : clip.end) + delta, total });
          }}
        />
      )}
      {isMobile ? null : (
        <div className={styles.panelFoot}>
          <Button
            size="sm"
            variant="ghost"
            iconLeft="plus"
            {...gate('project.review')}
            onClick={() => {
              const at = player.current?.getCurrentTime() ?? 0;
              const start = snapStart(words, clamp(at, 0, Math.max(0, total - 5)));
              const end = snapEnd(words, Math.min(total, start + 30));
              dispatch({ type: 'add', key: newLocalKey(), start, end, title: `Clip en ${formatTimecode(start)}`, total });
            }}
          >
            Nuevo clip desde el cursor
          </Button>
        </div>
      )}
    </Card>
  );

  const playerCard = (
    <Card as="section" className={styles.playerCard} aria-label="Reproductor">
      {empty && !isMobile ? (
        <div className={styles.panelHead}>
          <h2 className="t-body">Vista previa</h2>
        </div>
      ) : null}
      <div ref={playerBox} className={styles.playerBox}>
        <VideoPlayer
          ref={player}
          label={`Reproductor: ${data.title}`}
          src={data.proxy_url}
          seed={projectId}
          ratio="16:9"
          duration={total}
          range={solo && selected ? { start: selected.start, end: selected.end } : undefined}
          onTimeUpdate={setTime}
          onMarkIn={(seconds) => setEdge('start', snap('start', seconds))}
          onMarkOut={(seconds) => setEdge('end', snap('end', seconds))}
          onShortcut={shortcut}
          overlay={
            <>
              {view === 'crop' && aspect !== '16:9' ? (
                <div className={cx(styles.crop, aspect === '1:1' && styles.cropSquare)} role="img" aria-label={`Vista previa del recorte ${aspect}${caption ? ' con subtítulos' : ''}`}>
                  {caption ? (
                    <p className={styles.burn} style={{ color: '#F4F1EA' }}>
                      {caption.words.map((word, index) => (
                        <span key={index} style={index === caption.active ? { color: project?.subtitle_color ?? '#F2C94C' } : undefined}>
                          {word}{' '}
                        </span>
                      ))}
                    </p>
                  ) : null}
                </div>
              ) : null}
              {isMobile ? null : <div className={styles.viewSeg}>{viewSwitch}</div>}
            </>
          }
        >
          <div className={styles.pctl}>
            <IconButton icon="chevron-left" label="Clip anterior" disabled={selectedIndex <= 0} onClick={() => move(-1)} />
            <IconButton icon="chevron-right" label="Clip siguiente" disabled={selectedIndex === -1 || selectedIndex >= ordered.length - 1} onClick={() => move(1)} />
            {selected ? (
              <span className={styles.now}>
                <span className="t-label subtle">
                  Clip <span className="num">{selectedIndex + 1} de {ordered.length}</span>
                </span>
                <span className={styles.nowTitle}>{selected.title || 'Sin título'}</span>
              </span>
            ) : (
              <span className={styles.now} />
            )}
            {isMobile ? viewSwitch : <Switch checked={solo} onChange={setSolo} label="Solo el clip" disabled={!selected} />}
          </div>
          {isMobile ? (
            <div className={styles.mTrack} role="img" aria-label={`El video completo con ${draft.clips.length === 1 ? '1 clip marcado' : `${draft.clips.length} clips marcados`}.`}>
              {draft.clips.map((clip) => (
                <span
                  key={clip.key}
                  className={cx(styles.mMark, !clip.included && styles.mMarkOff, clip.key === draft.selectedKey && styles.mMarkSel)}
                  style={{ left: `${(clip.start / total) * 100}%` }}
                />
              ))}
            </div>
          ) : null}
        </VideoPlayer>
      </div>
      {selected ? (
        <div className={styles.previewRow}>
          {preview.state === 'ready' ? (
            <span className="t-small muted" role="status">
              <Icon name="check-circle" size={14} /> Vista previa lista en 360p, sin seguimiento de caras ni corte de silencios.{' '}
              {preview.preview?.url && !isPlaceholderSource(preview.preview.url) ? (
                <a className="lnk" href={preview.preview.url} target="_blank" rel="noopener noreferrer">
                  Abrirla
                </a>
              ) : null}
            </span>
          ) : preview.state === 'failed' ? (
            <span className="t-small" role="alert" style={{ color: 'var(--err)' }}>
              No pudimos generar la vista previa.
            </span>
          ) : preview.state === 'working' ? (
            <span className="t-small muted" role="status">
              Preparando la vista previa del recorte…
            </span>
          ) : (
            <span className="t-small muted">{preview.error ?? 'El recorte de arriba es una guía. La vista previa en 360p es gratis.'}</span>
          )}
          <Button
            size="sm"
            iconLeft="eye"
            loading={preview.state === 'working'}
            disabledReason={previewBlocked}
            onClick={preview.request}
          >
            {preview.state === 'ready' || preview.state === 'failed' ? 'Pedir otra' : 'Vista previa gratis'}
          </Button>
        </div>
      ) : null}
      {selected && !isMobile ? (
        <div className={styles.inspector}>
          <Field
            label={
              <>
                <span className={cx('num', styles.clipN)}>Clip {selected.n}</span> Título
              </>
            }
          >
            <Input
              value={selected.title}
              maxLength={255}
              readOnly={!canEdit}
              title={canEdit ? undefined : NO_ACCESS_REASON}
              onChange={(event) => dispatch({ type: 'rename', key: selected.key, title: event.target.value })}
            />
          </Field>
          <Field
            label={
              <>
                Inicio <Kbd>I</Kbd>
              </>
            }
          >
            <EdgeInput value={selected.start} readOnly={!canEdit} onCommit={(value) => setEdge('start', snap('start', value))} />
          </Field>
          <Field
            label={
              <>
                Fin <Kbd>O</Kbd>
              </>
            }
          >
            <EdgeInput value={selected.end} readOnly={!canEdit} onCommit={(value) => setEdge('end', snap('end', value))} />
          </Field>
          <div className={styles.dur}>
            <span className="t-label muted">Duración</span>
            <b className="num">{formatDuration(selected.end - selected.start)}</b>
          </div>
        </div>
      ) : null}
    </Card>
  );

  const ticks = rulerTicks(0, total, 8);
  const timelineCard = (
    <Card as="section" className={styles.tlCard} aria-label="Línea de tiempo">
      <div className={styles.tlHead}>
        <h2 className="t-body">Línea de tiempo</h2>
        <div className={timeline.legend}>
          {empty ? (
            <span>
              <i className={cx(timeline.sw, timeline.swPick)} />
              Selección
            </span>
          ) : (
            <>
              <span>
                <i className={timeline.sw} />
                Incluido
              </span>
              <span>
                <i className={cx(timeline.sw, timeline.swHollow)} />
                Excluido
              </span>
              <span>
                <i className={cx(timeline.sw, timeline.swSel)} />
                Seleccionado
              </span>
            </>
          )}
        </div>
      </div>
      <div>
        <div className={cx(timeline.ruler, styles.ovRuler)} aria-hidden="true">
          {ticks.map((tick, index) => (
            <span key={tick} className={cx(timeline.tick, index === 0 && timeline.tickFirst, tick / total > 0.96 && timeline.tickLast)} style={{ left: `${(tick / total) * 100}%` }}>
              {formatTimecode(tick)}
            </span>
          ))}
        </div>
        <div className={styles.ovNums} aria-hidden="true">
          {draft.clips.map((clip) => (
            <span key={clip.key} className={cx('num', styles.mkN, clip.key === draft.selectedKey && styles.mkNSel)} style={{ left: `${((clip.start + clip.end) / 2 / total) * 100}%` }}>
              {clip.n}
            </span>
          ))}
        </div>
        <div
          className={styles.ov}
          onPointerDown={(event) => {
            if (event.target !== event.currentTarget) return;
            const box = event.currentTarget.getBoundingClientRect();
            if (box.width > 0) {
              setSolo(false);
              const at = ((event.clientX - box.left) / box.width) * total;
              window.setTimeout(() => player.current?.seek(at), 0);
            }
          }}
        >
          {draft.clips.map((clip) => (
            <button
              key={clip.key}
              type="button"
              className={cx(styles.mk, !clip.included && styles.mkOut, clip.key === draft.selectedKey && styles.mkSel)}
              style={{ left: `${((clip.start + clip.end) / 2 / total) * 100}%`, width: `${((clip.end - clip.start) / total) * 100}%` }}
              aria-label={`Clip ${clip.n}: ${clip.title}, ${formatTimecode(clip.start)}–${formatTimecode(clip.end)}, ${clip.included ? 'incluido' : 'excluido'}`}
              aria-pressed={clip.key === draft.selectedKey}
              onClick={() => select(clip.key)}
            />
          ))}
          {selected ? (
            <span className={styles.ovWin} style={{ left: `${(detail.from / total) * 100}%`, width: `${((detail.to - detail.from) / total) * 100}%` }} />
          ) : (
            <span className={timeline.playhead} style={{ left: `${(time / total) * 100}%` }} />
          )}
        </div>
        {selected ? (
          <>
            <div
              className={styles.funnel}
              style={{ clipPath: `polygon(${(detail.from / total) * 100}% 0, ${(detail.to / total) * 100}% 0, 100% 100%, 0 100%)` }}
              aria-hidden="true"
            />
            <div className={styles.detailBox}>
              <TrimTimeline
                words={words}
                window={detail}
                start={selected.start}
                end={selected.end}
                time={time}
                seed={projectId}
                disabled={!canEdit}
                onSeek={(seconds) => player.current?.seek(seconds)}
                onDrag={(edge, seconds) => setEdge(edge, snap(edge, seconds), { fit: false })}
                onStep={(edge, direction) => setEdge(edge, stepEdge(words, edge, edge === 'start' ? selected.start : selected.end, direction))}
              />
            </div>
          </>
        ) : null}
      </div>
      {selected ? (
        <div className={styles.detailMeta}>
          <span>
            Video completo <span className="timecode">{formatTimecode(total)}</span> · detalle{' '}
            <span className="timecode">
              {formatTimecode(detail.from)}–{formatTimecode(detail.to)}
            </span>
          </span>
          <span>Los bordes se ajustan a palabras completas.</span>
        </div>
      ) : (
        <p className="t-label subtle">Todavía no hay clips marcados. Los que crees aparecen acá.</p>
      )}
    </Card>
  );

  const transcript = (
    <Card as="section" className={cx(styles.tr, empty && styles.trMain, isMobile && styles.trMobile)} aria-label="Transcripción">
      <TranscriptPanel
        words={words}
        rows={rows}
        clip={selected ? { n: selected.n, start: selected.start, end: selected.end } : null}
        activeWord={activeWord}
        canEdit={canEdit}
        variant={empty && !isMobile ? 'main' : 'side'}
        mobile={isMobile}
        onSeek={(seconds) => {
          if (solo && selected && (seconds < selected.start || seconds > selected.end)) setSolo(false);
          window.setTimeout(() => player.current?.seek(seconds), 0);
        }}
        onCreateClip={(start, end, text) => {
          dispatch({ type: 'add', key: newLocalKey(), start, end, title: titleFromText(text), total });
          if (isMobile) setTab('clips');
        }}
        onMoveEdge={(edge, seconds) => setEdge(edge, seconds)}
        onFitClip={(start, end) => {
          if (!selected) return;
          // Order matters: widen first so the clamp against the other edge never blocks the move.
          dispatch({ type: 'edge', key: selected.key, edge: 'end', value: Math.max(end, selected.start + 1), total });
          dispatch({ type: 'edge', key: selected.key, edge: 'start', value: start, total });
          dispatch({ type: 'edge', key: selected.key, edge: 'end', value: end, total });
          setFitNonce((nonce) => nonce + 1);
        }}
      />
    </Card>
  );

  // While the review is fetched again the clips on screen may be about to change: "Renderizar" waits.
  // The reason is on screen (the bar's small line) and on the button (tooltip and description).
  const roleReason = gate('project.review').disabledReason;
  const waitingReason = roleReason === undefined && refreshing && totals.count > 0 && estimate ? REFRESHING_REASON : '';
  const waitingNote = waitingReason ? <span className={styles.waiting}>{waitingReason}</span> : null;
  const clipsWord = totals.count === 1 ? '1 clip seleccionado' : `${formatNumber(totals.count, 0)} clips seleccionados`;
  const rate = pricing.data?.base_cost_per_second_720p ?? 0.5;

  return (
    <>
      <Page width="wide" flush className={styles.page}>
        <ProjectHeader
          compact
          crumbs={[{ label: 'Proyectos', href: '/proyectos' }, { label: data.title, href: `/proyectos/${projectId}` }, { label: 'Revisión' }]}
          title={data.title}
          badge={
            <Badge dot tone="accent">
              Listo para revisar
            </Badge>
          }
          statusText={statusText}
          actions={isMobile ? undefined : <ShortcutsButton open={keysOpen} onOpenChange={setKeysOpen} />}
        />
        {loadError ? <LoadError error={loadError} onRetry={onRetry} fullPageForMissing={false} className={styles.banner} /> : null}
        {skipped ? (
          <Banner tone="warn" className={styles.banner} title={skipped.title}>
            {skipped.text}
          </Banner>
        ) : null}
        {canEdit ? null : (
          <Banner tone="info" className={styles.banner} title="Estás viendo la revisión en modo lectura">
            Podés reproducir los clips y leer la transcripción. Para elegir clips y renderizar, pedile acceso a un admin.
          </Banner>
        )}
        <div className={cx(styles.review, empty && styles.empty, isMobile && styles.mobile)}>
          {isMobile ? (
            <>
              {playerCard}
              <SegmentedControl<'clips' | 'text'>
                ariaLabel="Qué ver debajo del reproductor"
                block
                touch
                value={tab}
                onChange={setTab}
                options={[
                  { value: 'clips', label: `Clips · ${draft.clips.length}` },
                  { value: 'text', label: 'Transcripción' },
                ]}
              />
              {tab === 'clips' ? clipsPanel : transcript}
            </>
          ) : empty ? (
            <>
              {clipsPanel}
              {transcript}
              <div className={styles.col}>
                {playerCard}
                {timelineCard}
              </div>
            </>
          ) : (
            <>
              {clipsPanel}
              <div className={styles.col}>
                {playerCard}
                {timelineCard}
              </div>
              {transcript}
            </>
          )}
        </div>
      </Page>
      <ActionBar
        className={styles.bar}
        summary={
          isMobile ? (
            <span className="num">
              {totals.count === 1 ? '1 clip' : `${totals.count} clips`} · {formatDuration(totals.seconds)} · {estimate ? formatTokens(estimate.total) : '0 tokens'}
            </span>
          ) : (
            <>
              <span className="num">{clipsWord}</span> · <span className="num">{formatDuration(totals.seconds)}</span> en total ·{' '}
              <span className="num">{estimate ? formatTokens(estimate.total) : '0 tokens'}</span>
            </>
          )
        }
        detail={
          totals.count === 0 ? (
            <>
              {empty ? 'Creá al menos un clip para renderizar' : 'Elegí al menos un clip para renderizar'}
              {balance !== null ? (
                <>
                  {' '}
                  · Saldo <span className="num">{formatTokens(balance)}</span>
                </>
              ) : null}
            </>
          ) : !estimate ? (
            'Calculando el costo…'
          ) : isMobile ? (
            <>
              <span className="num">{short ? `Te faltan ${formatTokens(estimate.total - (balance ?? 0))}` : balance !== null ? `Saldo después ${formatTokens(balance - estimate.total)}` : ''}</span>
              {waitingNote ? <> · {waitingNote}</> : null}
            </>
          ) : (
            <>
              <span className="num">{formatTokens(estimate.total)}</span> = <span className="num">{formatNumber(billedSeconds, 0)}</span> s ×{' '}
              <span className="num">{formatNumber(rate)}</span>
              {estimate.multiplier !== 1 ? (
                <>
                  {' '}
                  × <span className="num">{formatNumber(estimate.multiplier)}</span> ({resolution})
                </>
              ) : null}
              {estimate.discountRate > 0 ? (
                <>
                  {' '}
                  − <span className="num">{formatPercent(estimate.discountRate)}</span> del plan {plan?.name}
                </>
              ) : null}{' '}
              ·{' '}
              <button
                type="button"
                className={cx('lnk', styles.linkBtn)}
                aria-disabled={!canEdit || undefined}
                title={canEdit ? undefined : NO_ACCESS_REASON}
                onClick={canEdit ? askToRender : undefined}
              >
                Ver detalle
              </button>{' '}
              ·{' '}
              {balance === null ? null : short ? (
                <span style={{ color: 'var(--warn)' }}>
                  Te faltan <span className="num">{formatTokens(estimate.total - balance)}</span>
                </span>
              ) : (
                <>
                  Saldo después <span className="num">{formatTokens(balance - estimate.total)}</span>
                </>
              )}
              {waitingNote ? <> · {waitingNote}</> : null}
            </>
          )
        }
      >
        <Button
          variant="accent"
          size="lg"
          disabled={totals.count === 0 || !estimate}
          disabledReason={roleReason ?? waitingReason}
          aria-label={totals.count > 0 ? `Renderizar ${totals.count === 1 ? '1 clip' : `${totals.count} clips`}` : 'Renderizar'}
          onClick={askToRender}
        >
          {isMobile || totals.count === 0 ? 'Renderizar' : `Renderizar ${totals.count === 1 ? '1 clip' : `${totals.count} clips`}`}
        </Button>
      </ActionBar>
      {estimate ? (
        <ConfirmCostDialog
          open={confirming}
          onClose={() => setConfirming(false)}
          onConfirm={render}
          title={`Renderizar ${totals.count === 1 ? '1 clip' : `${totals.count} clips`}`}
          subtitle={data.title}
          durationSeconds={billedSeconds}
          resolution={resolution}
          estimate={estimate}
          planName={plan?.name}
          balance={balance ?? 0}
          baseRate={rate}
          confirmLabel="Renderizar"
          loading={approve.isPending}
          error={approveError}
        />
      ) : null}
      <UnsavedChangesDialog {...guard.dialog} what="los clips que elegiste y los bordes que ajustaste" />
    </>
  );
}
