'use client';

import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Platform } from '@/api/contracts/common';
import type { PostStatus } from '@/api/contracts/projects';
import type { PostsQuery, ScheduledPost } from '@/api/contracts/social';
import { ApiError } from '@/api/errors';
import { useQueryClient } from '@tanstack/react-query';
import { useCancelPost, usePost, usePostActions, usePosts, useRetryPost, useSocialAccounts } from '@/api/hooks';
import { keys } from '@/api/query';
import { forgetDraft } from './draftRegistry';
import { ConfirmDialog, ConnectNetworkDialog, useBillingDialogs } from '@/dialogs';
import { errorMessage } from '@/lib/error-messages';
import { useIsMobile, useMediaQuery } from '@/lib/hooks';
import { POST_STATUS } from '@/lib/project-status';
import { LoadError } from '@/shell/LoadError';
import { usePermissions } from '@/shell/session';
import {
  Banner,
  Button,
  Card,
  Drawer,
  FilterChip,
  FilterChips,
  Icon,
  IconButton,
  NETWORKS,
  Num,
  Page,
  PageHead,
  Pagination,
  SegmentedControl,
  Select,
  Skeleton,
  SkeletonRows,
  useToast,
  type FilterOption,
} from '@/ui';
import { MonthCalendar } from './MonthCalendar';
import { statusIcon } from './PostBits';
import { PostDetail } from './PostDetail';
import { PostCards, PostTable, type PostActions } from './PostList';
import styles from './Publicaciones.module.css';
import { WeekCalendar } from './WeekCalendar';
import {
  NETWORK_NOUN,
  PLATFORMS,
  STATUSES,
  addDays,
  addMonths,
  dayKey,
  failureMessage,
  monthGrid,
  monthLabel,
  needsReconnect,
  parseDayKey,
  shortDayTime,
  startOfDay,
  startOfMonth,
  startOfWeek,
  timeZoneLabel,
  weekRangeLabel,
  type PostGroup,
} from './posts';

type View = 'semana' | 'mes' | 'lista';
type StatusFilter = PostStatus | 'ALL';
const VIEWS: View[] = ['semana', 'mes', 'lista'];
const LIST_PAGE_SIZE = 50;
/** Enough for a month of a busy workspace; the calendars show one range at a time. */
const RANGE_PAGE_SIZE = 100;

/** The current time, refreshed while the screen is open (the "now" line and "Hoy"). */
function useNow(everyMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), everyMs);
    return () => window.clearInterval(timer);
  }, [everyMs]);
  return now;
}

/** E2 · Publicaciones: everything published and what is going out, as a week, a month or a list. */
export function PublicacionesScreen() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const toast = useToast();
  const billing = useBillingDialogs();
  const { gate } = usePermissions();
  const isMobile = useIsMobile();
  const isWide = useMediaQuery('(min-width: 1024px)');
  const now = useNow();

  const requested = params.get('vista') as View | null;
  const view: View = isMobile ? 'lista' : requested && VIEWS.includes(requested) ? requested : 'semana';
  const status: StatusFilter = STATUSES.includes(params.get('estado') as PostStatus) ? (params.get('estado') as PostStatus) : 'ALL';
  const platform = PLATFORMS.includes(params.get('red') as Platform) ? (params.get('red') as Platform) : null;
  const anchorKey = params.get('fecha');
  const anchor = useMemo(() => parseDayKey(anchorKey) ?? startOfDay(new Date()), [anchorKey]);
  const page = Math.max(1, Number(params.get('pagina')) || 1);
  const detailId = params.get('detalle');

  const setParams = useCallback(
    (updates: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(updates)) {
        if (value === null || value === '') next.delete(key);
        else next.set(key, value);
      }
      const query = next.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  /* ── Data ──────────────────────────────────────────────────────────────── */
  const range = useMemo(() => {
    if (view === 'semana') {
      const monday = startOfWeek(anchor);
      return { from: monday, to: addDays(monday, 7) };
    }
    const days = monthGrid(anchor);
    return { from: days[0] as Date, to: addDays(days[days.length - 1] as Date, 1) };
  }, [anchor, view]);
  const filters: PostsQuery = { ...(status !== 'ALL' ? { status } : {}), ...(platform ? { platform } : {}) };

  const accounts = useSocialAccounts();
  const all = usePosts({ page_size: RANGE_PAGE_SIZE });
  const ranged = usePosts(
    { ...filters, from: range.from.toISOString(), to: new Date(range.to.getTime() - 1).toISOString(), page_size: RANGE_PAGE_SIZE },
    { enabled: view !== 'lista' },
  );
  const listed = usePosts({ ...filters, page, page_size: LIST_PAGE_SIZE }, { enabled: view === 'lista' });
  const current = view === 'lista' ? listed : ranged;

  const posts = useMemo(() => current.data?.results ?? [], [current.data]);
  const everything = all.data?.results;
  const nothingYet = all.data?.count === 0;
  const counts = current.data?.status_counts;
  const total = counts ? STATUSES.reduce((sum, key) => sum + (counts[key] ?? 0), 0) : undefined;
  const knowsAll = all.data ? all.data.count <= all.data.results.length : false;
  const failedPost = useMemo(
    () => [...(everything ?? [])].filter((post) => post.status === 'FAILED').sort((a, b) => b.updated_at.localeCompare(a.updated_at))[0],
    [everything],
  );

  /* ── Actions ───────────────────────────────────────────────────────────── */
  const queryClient = useQueryClient();
  const postActions = usePostActions();
  const retry = useRetryPost();
  const [moving, setMoving] = useState(false);
  const cancel = useCancelPost();
  const [mode, setMode] = useState<'view' | 'edit'>('view');
  const [busyId, setBusyId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<ScheduledPost | null>(null);
  const [connecting, setConnecting] = useState<Platform | null>(null);
  // A post being cancelled stops being watched before the request goes out: once deleted there is nothing to refetch.
  const [goneId, setGoneId] = useState<string | null>(null);
  const detail = usePost(detailId && detailId !== goneId ? detailId : null);
  const selected: ScheduledPost | undefined = detailId ? (detail.data ?? posts.find((post) => post.id === detailId) ?? everything?.find((post) => post.id === detailId)) : undefined;
  const siblings = selected ? (everything ?? []).filter((post) => post.video_clip === selected.video_clip && post.id !== selected.id) : [];

  const closeDetail = useCallback(() => {
    setMode('view');
    setParams({ detalle: null });
  }, [setParams]);

  const actions: PostActions = {
    open: (post, nextMode = 'view') => {
      setMode(nextMode);
      setParams({ detalle: post.id });
    },
    retry: (post) => {
      setBusyId(post.id);
      retry.mutate(post.id, {
        onSuccess: () => toast.success(`Reintentando «${post.clip.title}» en ${NETWORK_NOUN[post.platform]}`),
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'account_not_connected') {
            toast.error(`La cuenta de ${NETWORK_NOUN[post.platform]} no está conectada. Reconectala para reintentar.`);
          } else toast.error(errorMessage(error, 'No pudimos reintentar la publicación.'));
        },
        onSettled: () => setBusyId(null),
      });
    },
    askCancel: setCancelling,
    reconnect: setConnecting,
    publish: (clipId, network, draftId) => {
      // Chosen from the list: the draft is the person's, so the panel that opens uses it but never discards it.
      if (draftId) forgetDraft(draftId);
      setMode('view');
      setParams({ publicar: clipId, red: network ?? null, detalle: null });
    },
    publishGate: gate('post.publish'),
    connectGate: gate('social.manage'),
    busyId,
  };

  const move = async (group: PostGroup, to: Date) => {
    if (moving) return;
    setMoving(true);
    const ids = new Set(group.posts.map((post) => post.id));
    const publishAt = to.toISOString();
    // The card moves right away: every cached list and detail shows the new date, and the old ones are kept to undo it.
    await queryClient.cancelQueries({ queryKey: keys.social.posts });
    const before = queryClient.getQueriesData<unknown>({ queryKey: keys.social.posts });
    const moved = (post: ScheduledPost): ScheduledPost => (ids.has(post.id) ? { ...post, publish_at: publishAt } : post);
    queryClient.setQueriesData<unknown>({ queryKey: keys.social.posts }, (data: unknown) => {
      if (!data || typeof data !== 'object') return data;
      if ('results' in data && Array.isArray(data.results)) return { ...data, results: (data.results as ScheduledPost[]).map(moved) };
      return 'id' in data && 'publish_at' in data ? moved(data as ScheduledPost) : data;
    });
    try {
      // One after the other: a card can hold the same clip in several networks.
      for (const post of group.posts) await postActions.update({ id: post.id, publish_at: publishAt });
      toast.success(`«${group.title}» pasó al ${shortDayTime(to)}`);
    } catch (error) {
      for (const [key, data] of before) queryClient.setQueryData(key, data);
      if (!billing.handleError(error)) {
        const field = error instanceof ApiError ? error.fieldErrors.publish_at?.[0] : undefined;
        toast.error(field ?? errorMessage(error, 'No pudimos reprogramar la publicación.'));
      }
    } finally {
      setMoving(false);
      // Whatever happened, the server has the last word.
      void postActions.invalidate();
    }
  };
  // No second drag while the first one is being saved.
  const onMove = actions.publishGate.disabledReason || moving ? undefined : (group: PostGroup, to: Date) => void move(group, to);
  // Drafts have no date yet: they live in the list, not in the calendars.
  const dated = useMemo(() => posts.filter((post) => post.status !== 'DRAFT'), [posts]);

  const shift = (direction: -1 | 1) => {
    const next = view === 'mes' ? addMonths(startOfMonth(anchor), direction) : addDays(startOfWeek(anchor), direction * 7);
    setParams({ fecha: dayKey(next) });
  };

  /* ── Pieces ────────────────────────────────────────────────────────────── */
  const statusOptions: FilterOption<StatusFilter>[] = [
    { value: 'ALL', label: 'Todas', count: total },
    ...STATUSES.map((key) => ({
      value: key,
      label: POST_STATUS[key].plural,
      count: counts?.[key],
      icon: statusIcon(key),
      hideWhenEmpty: key === 'FAILED' && status !== 'FAILED',
    })),
  ];
  const platformCount = (key: Platform) => (knowsAll ? everything?.filter((post) => post.platform === key).length : undefined);

  const filtersBar = isMobile ? (
    <div className={styles.mTools}>
      <Select aria-label="Filtrar por estado" value={status} onChange={(event) => setParams({ estado: event.target.value === 'ALL' ? null : event.target.value, pagina: null })}>
        <option value="ALL">Todos los estados{total !== undefined ? ` (${total})` : ''}</option>
        {STATUSES.map((key) => (
          <option key={key} value={key}>
            {POST_STATUS[key].plural}
            {counts ? ` (${counts[key] ?? 0})` : ''}
          </option>
        ))}
      </Select>
      <Select aria-label="Filtrar por red" value={platform ?? ''} onChange={(event) => setParams({ red: event.target.value || null, pagina: null })}>
        <option value="">Todas las redes</option>
        {PLATFORMS.map((key) => (
          <option key={key} value={key}>
            {NETWORKS[key].shortName}
          </option>
        ))}
      </Select>
    </div>
  ) : (
    <div className={`${styles.toolbar} ${styles.toolbarFilters}`}>
      <FilterChips ariaLabel="Filtrar por estado" options={statusOptions} value={status} onChange={(value) => setParams({ estado: value === 'ALL' ? null : value, pagina: null })} />
      <span className={styles.spacer} />
      <div className={styles.fgroup} role="group" aria-label="Filtrar por red">
        {PLATFORMS.map((key) => (
          <FilterChip
            key={key}
            label={NETWORKS[key].shortName}
            icon={NETWORKS[key].icon}
            count={platformCount(key)}
            pressed={platform === key}
            onPressedChange={(pressed) => setParams({ red: pressed ? key : null, pagina: null })}
          />
        ))}
      </div>
    </div>
  );

  const calendarNav =
    view === 'lista' ? null : (
      <div className={styles.toolbar}>
        <div className={styles.fgroup}>
          <IconButton icon="chevron-left" label={view === 'mes' ? 'Mes anterior' : 'Semana anterior'} className={styles.navBtn} onClick={() => shift(-1)} />
          <IconButton icon="chevron-right" label={view === 'mes' ? 'Mes siguiente' : 'Semana siguiente'} className={styles.navBtn} onClick={() => shift(1)} />
          <Button size="sm" onClick={() => setParams({ fecha: null })}>
            Hoy
          </Button>
        </div>
        <h2 className={`t-h3 num ${styles.range}`}>{view === 'mes' ? monthLabel(anchor) : weekRangeLabel(startOfWeek(anchor))}</h2>
        <span className={styles.spacer} />
        {nothingYet || !onMove ? null : (
          <p className={`t-small subtle ${styles.hint}`}>
            <Icon name="info" size={14} />
            {view === 'mes' ? 'Arrastrá una publicación programada para cambiarle el día.' : 'Arrastrá una publicación programada para cambiarle el día o la hora.'}
          </p>
        )}
      </div>
    );

  const inlineDetail = view === 'lista' && isWide;
  const detailNode = detailId ? (
    <PostDetail post={selected} siblings={siblings} accounts={accounts.data} actions={actions} mode={mode} onModeChange={setMode} error={selected ? undefined : detail.error} inDrawer={!inlineDetail} />
  ) : null;

  let body: React.ReactNode;
  if (nothingYet) {
    body = (
      <div className={styles.emptyWrap}>
        {isMobile ? null : <WeekCalendar monday={startOfWeek(anchor)} posts={[]} now={now} ghost />}
        <Card as="section" className={styles.emptyCard} aria-labelledby="publicaciones-vacio">
          <span className={styles.emptyIco} aria-hidden="true">
            <Icon name="calendar" size={20} />
          </span>
          <h2 className="t-h3" id="publicaciones-vacio">
            Todavía no programaste nada
          </h2>
          <p className={`t-body muted ${styles.emptyText}`}>Cuando publiques o programes un clip, lo vas a ver acá con su red, su hora y su estado.</p>
          <div className={styles.emptyAction}>
            <Button variant="primary" iconLeft="clips" href="/proyectos">
              Ir a mis clips
            </Button>
          </div>
          <p className={`t-small subtle ${styles.emptyNote}`}>
            Publicás en las redes que tengas conectadas.{' '}
            <Link className={styles.ulink} href="/ajustes/redes">
              Ver redes
            </Link>
          </p>
        </Card>
      </div>
    );
  } else if (current.isPending) {
    body =
      view === 'lista' ? (
        <Card>
          <SkeletonRows rows={6} label="Cargando publicaciones" />
        </Card>
      ) : (
        <div aria-busy="true" aria-label="Cargando publicaciones">
          <Skeleton height={view === 'mes' ? 660 : 520} radius={8} />
        </div>
      );
  } else if (view !== 'lista' && status === 'DRAFT') {
    body = (
      <Card className={styles.noResults}>
        <p className="muted">Los borradores todavía no tienen fecha, por eso no están en el calendario.</p>
        <Button size="sm" onClick={() => setParams({ vista: 'lista' })}>
          Ver los borradores en la lista
        </Button>
      </Card>
    );
  } else if (view === 'semana') {
    body = <WeekCalendar monday={startOfWeek(anchor)} posts={dated} now={now} selectedId={detailId} onOpen={(post) => actions.open(post)} onMove={onMove} />;
  } else if (view === 'mes') {
    body = (
      <MonthCalendar
        month={anchor}
        posts={dated}
        now={now}
        selectedId={detailId}
        onOpen={(post) => actions.open(post)}
        onMove={onMove}
        onOpenWeek={(day) => setParams({ vista: null, fecha: dayKey(day) })}
      />
    );
  } else if (posts.length === 0) {
    body = (
      <Card className={styles.noResults}>
        <p className="muted">No hay publicaciones con estos filtros.</p>
        <Button size="sm" onClick={() => setParams({ estado: null, red: null, pagina: null })}>
          Quitar filtros
        </Button>
      </Card>
    );
  } else if (isMobile) {
    body = <PostCards posts={posts} accounts={accounts.data} now={now} selectedId={detailId} actions={actions} />;
  } else {
    body = (
      <div className={styles.split}>
        <Card as="section" className={styles.listcol} aria-label="Publicaciones">
          <PostTable posts={posts} accounts={accounts.data} now={now} selectedId={detailId} actions={actions} />
          {listed.data && listed.data.count > LIST_PAGE_SIZE ? (
            <div className={styles.pager}>
              <Pagination page={page} pageSize={LIST_PAGE_SIZE} count={listed.data.count} noun="publicaciones" onPageChange={(next) => setParams({ pagina: next > 1 ? String(next) : null })} />
            </div>
          ) : null}
        </Card>
        {detailId && inlineDetail ? (
          <Card as="aside" className={styles.detail} aria-label="Detalle de la publicación">
            <header className={styles.detailHead}>
              <h2 className="t-h3">Detalle de la publicación</h2>
              <IconButton icon="close" label="Cerrar el detalle" onClick={closeDetail} />
            </header>
            {detailNode}
          </Card>
        ) : null}
      </div>
    );
  }

  const failedActions = (post: ScheduledPost) => (
    <span className={styles.alertActions}>
      <Button variant="ghost" size="sm" onClick={() => actions.open(post)}>
        Ver detalle
      </Button>
      {needsReconnect(post, accounts.data) ? (
        <Button size="sm" iconLeft="link" onClick={() => setConnecting(post.platform)} {...actions.connectGate}>
          Reconectar
        </Button>
      ) : (
        <Button size="sm" iconLeft="retry" loading={busyId === post.id} onClick={() => actions.retry(post)} {...actions.publishGate}>
          Reintentar
        </Button>
      )}
    </span>
  );

  const zone = timeZoneLabel(now);
  return (
    <Page>
      <PageHead
        title="Publicaciones"
        description="Todo lo publicado y lo que va a salir."
        actions={
          <>
            {isMobile ? null : (
              <SegmentedControl
                className={styles.viewsw}
                ariaLabel="Vista"
                value={view}
                onChange={(next) => setParams({ vista: next === 'semana' ? null : next, pagina: null })}
                options={[
                  { value: 'semana', label: 'Semana' },
                  { value: 'mes', label: 'Mes' },
                  { value: 'lista', label: 'Lista' },
                ]}
              />
            )}
            {nothingYet ? null : (
              <Button iconLeft="clips" href="/proyectos">
                Ir a mis clips
              </Button>
            )}
          </>
        }
      />

      {all.isError || current.isError ? <LoadError error={current.error ?? all.error} onRetry={() => void Promise.all([all.refetch(), current.refetch()])} className={styles.alert} fullPageForMissing={false} /> : null}

      {failedPost && failedPost.id !== detailId ? (
        <Banner tone="err" className={styles.alert} title={`«${failedPost.clip.title}» no salió en ${NETWORK_NOUN[failedPost.platform]}`} action={isMobile ? undefined : failedActions(failedPost)}>
          {failureMessage(failedPost, accounts.data)}
          {isMobile ? <span className={styles.alertBelow}>{failedActions(failedPost)}</span> : null}
        </Banner>
      ) : null}

      {nothingYet ? null : (
        <>
          {calendarNav}
          {filtersBar}
        </>
      )}
      {body}
      {nothingYet && isMobile ? null : (
        <p className={`t-small subtle ${styles.footnote}`}>
          Horarios en tu zona: <Num>{zone}</Num>.
          {view === 'lista' || nothingYet ? null : ' Una ficha por clip y horario; si sale en varias redes a la vez, cada red muestra su estado.'}
          {view === 'mes' && !nothingYet ? (
            <>
              {' '}
              Los borradores sin fecha están en la{' '}
              <button type="button" className={`lnk ${styles.ulink}`} onClick={() => setParams({ vista: 'lista' })}>
                lista
              </button>
              .
            </>
          ) : null}
        </p>
      )}

      <Drawer open={Boolean(detailId) && !inlineDetail} onClose={closeDetail} title="Detalle de la publicación">
        {detailNode}
      </Drawer>

      <ConfirmDialog
        open={Boolean(cancelling)}
        onClose={() => {
          setCancelling(null);
          cancel.reset();
        }}
        tone="danger"
        title={cancelling?.status === 'DRAFT' ? '¿Borrar este borrador?' : '¿Cancelar esta publicación?'}
        description={
          cancelling
            ? cancelling.status === 'DRAFT'
              ? `Se borra el borrador de «${cancelling.clip.title}» para ${NETWORK_NOUN[cancelling.platform]}. El clip no se borra.`
              : `«${cancelling.clip.title}» no va a salir en ${NETWORK_NOUN[cancelling.platform]}. El clip no se borra y podés publicarlo de nuevo cuando quieras.`
            : undefined
        }
        confirmLabel={cancelling?.status === 'DRAFT' ? 'Borrar borrador' : 'Cancelar publicación'}
        cancelLabel="Volver"
        loading={cancel.isPending}
        error={cancel.isError ? (cancel.error instanceof ApiError && cancel.error.isConflict ? 'Ya se está publicando o ya se publicó: no se puede cancelar.' : errorMessage(cancel.error, 'No pudimos cancelar la publicación.')) : undefined}
        onConfirm={() => {
          if (!cancelling) return;
          const target = cancelling;
          setGoneId(target.id);
          cancel.mutate(target.id, {
            onError: () => setGoneId(null),
            onSuccess: () => {
              toast.success(target.status === 'DRAFT' ? 'Borrador borrado' : 'Publicación cancelada');
              setCancelling(null);
              if (detailId === target.id) closeDetail();
            },
          });
        }}
      />
      <ConnectNetworkDialog platform={connecting} onClose={() => setConnecting(null)} />
    </Page>
  );
}
