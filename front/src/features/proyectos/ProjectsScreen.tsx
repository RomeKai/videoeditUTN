'use client';

import { useQueryClient } from '@tanstack/react-query';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { DEFAULT_PAGE_SIZE } from '@/api/contracts/common';
import type { ProjectListItem as Project, ProjectStatusGroup } from '@/api/contracts/projects';
import { ApiError } from '@/api/errors';
import { useProjects } from '@/api/hooks';
import { keys } from '@/api/query';
import { DeleteProjectDialog } from '@/dialogs';
import { plural } from '@/lib/format';
import { useIsMobile, useStoredState } from '@/lib/hooks';
import { describeProjectStatus } from '@/lib/project-status';
import { LoadError } from '@/shell/LoadError';
import { useSession } from '@/shell/session';
import {
  Button,
  Card,
  DropdownMenu,
  EmptyState,
  FilterChips,
  Input,
  Page,
  PageHead,
  Pagination,
  SegmentedControl,
  SkeletonRows,
  StatusDot,
  useToast,
  type MenuItem,
} from '@/ui';
import { ProjectsCards, ProjectsGrid, ProjectsTable } from './ProjectItems';
import { ProjectsEmpty } from './ProjectsEmpty';
import styles from './Projects.module.css';
import { RenameProjectDialog } from './RenameProjectDialog';
import { listQuery, ORDERINGS, readListParams, STATUS_FILTERS, type Ordering, type ProjectsListState } from './project-view';

type View = 'list' | 'grid';

/** How long the search waits after the last key before it asks. */
const SEARCH_DELAY_MS = 300;

/**
 * B2 · Proyectos: todo lo que pasa en el workspace, con buscador, filtro por estado, orden y dos vistas.
 * The server pages, filters, searches, orders and counts the filters; the screen only asks.
 *
 * The filter, the order, the page and the search live in the URL (`?estado=&orden=&pagina=&buscar=`, each
 * one only when it is not the default): a reload, a shared link or coming back from a project show the same
 * list. The URL is what the screen reads; a choice replaces it (no history entry per click).
 */
export function ProjectsScreen() {
  const { workspace } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const mobile = useIsMobile();
  const [view, setView] = useStoredState<View>('oc.proyectos.vista', 'list');
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const inUrl = useMemo(() => readListParams(params), [params]);
  const { group, ordering, search: term } = inUrl;
  const [search, setSearch] = useState(term);
  const [renaming, setRenaming] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);

  const write = useCallback(
    (changes: Partial<ProjectsListState>) => {
      const query = listQuery({ ...readListParams(params), ...changes }, params);
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  // The search asks a moment after the last key, and starts on the first page: both change in the URL at
  // once, so no request ever asks for a page of the previous search.
  const typed = search.trim();
  const written = useRef(term);
  useEffect(() => {
    // The search of the URL changed and this screen did not write it (a link back to Proyectos): the field follows.
    if (term === written.current) return;
    written.current = term;
    setSearch(term);
  }, [term]);
  useEffect(() => {
    if (typed === term) return;
    const timer = window.setTimeout(() => {
      written.current = typed;
      write({ search: typed, page: 1 });
    }, SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [typed, term, write]);

  // The page belongs to one workspace: in another one the list starts on the first page in the same
  // render, while the URL is still the one of the workspace before.
  const workspaceId = workspace?.id ?? null;
  const [pageOf, setPageOf] = useState<{ workspace: string | null; stale: unknown }>({ workspace: workspaceId, stale: null });
  if (pageOf.workspace !== workspaceId) setPageOf({ workspace: workspaceId, stale: pageOf.workspace !== null && inUrl.page > 1 ? params : null });
  const stalePage = pageOf.workspace !== workspaceId ? pageOf.workspace !== null : pageOf.stale === params;
  const askedPage = stalePage ? 1 : inUrl.page;
  useEffect(() => {
    if (pageOf.stale === params && inUrl.page > 1) write({ page: 1 });
  }, [pageOf, params, inUrl.page, write]);
  const setPage = (next: number) => write({ page: Math.max(1, next) });

  const query = useProjects({
    page: askedPage,
    page_size: DEFAULT_PAGE_SIZE,
    ...(group !== 'all' ? { status_group: group } : {}),
    ...(term ? { search: term } : {}),
    ...(ordering !== '-created_at' ? { ordering } : {}),
  });
  const data = query.data;
  const projects = data?.results ?? [];
  const counts = data?.status_counts;
  const count = data?.count ?? 0;
  const page = askedPage;
  const filtered = group !== 'all' || term !== '';
  const total = counts?.all ?? count;
  // While the next page loads the previous one stays on screen (`keepPreviousData`), and its counts are the
  // ones of the previous search: a search without results that was just cleared is not an empty workspace.
  const workspaceEmpty = Boolean(data) && !query.isPlaceholderData && !filtered && total === 0;
  const anyInProgress = projects.some((project) => describeProjectStatus(project).inProgress);
  const order = ORDERINGS.find((item) => item.value === ordering) ?? ORDERINGS[0];

  // A page past the end answers 404: the last row was deleted (here or by someone else), the list got
  // shorter while polling, or the URL names a page that is not there. If a page of this same list answered
  // before, go back one, or to the last page its count knows if that is nearer. If none did (a URL opened
  // on a page that does not exist) nothing says how long the list is: the first page, in one step. Never
  // one request per page between the URL's and the end.
  const listKey = `${workspaceId ?? ''}|${group}|${term}`;
  const known = useRef<{ list: string; count: number } | null>(null);
  useEffect(() => {
    if (data && !query.isPlaceholderData) known.current = { list: listKey, count: data.count };
  }, [data, query.isPlaceholderData, listKey]);
  const pastTheEnd = query.error instanceof ApiError && query.error.status === 404 && askedPage > 1;
  useEffect(() => {
    if (pastTheEnd) {
      // The pages kept in memory are as outdated as the one that just disappeared.
      void queryClient.invalidateQueries({ queryKey: keys.projects.all });
      const lastKnown = known.current?.list === listKey ? Math.ceil(known.current.count / DEFAULT_PAGE_SIZE) : 1;
      write({ page: Math.max(1, Math.min(askedPage - 1, lastKnown)) });
    } else if (data && !query.isPlaceholderData && data.results.length === 0 && data.count > 0 && askedPage > 1) write({ page: Math.max(1, Math.ceil(data.count / DEFAULT_PAGE_SIZE)) });
  }, [pastTheEnd, data, query.isPlaceholderData, askedPage, listKey, write, queryClient]);

  const pickGroup = (next: ProjectStatusGroup) => write({ group: next, page: 1 });
  const pickOrdering = (next: Ordering) => write({ ordering: next, page: 1 });
  const clearFilters = () => {
    setSearch('');
    written.current = '';
    write({ group: 'all', search: '', page: 1 });
  };

  const filterOptions = STATUS_FILTERS.map((option) => ({ ...option, count: counts?.[option.value] }));
  const orderItems: MenuItem[] = ORDERINGS.map((item) => ({
    key: item.value,
    label: item.long,
    checked: item.value === ordering,
    onSelect: () => pickOrdering(item.value),
  }));
  const mobileItems: MenuItem[] = [
    ...filterOptions.map((option, index) => ({
      key: `estado-${option.value}`,
      label: option.label,
      hint: option.count,
      checked: option.value === group,
      onSelect: () => pickGroup(option.value),
      ...(index === 0 ? { groupLabel: 'Estado' } : {}),
    })),
    ...orderItems.map((item, index) => ({ ...item, key: `orden-${item.key}`, ...(index === 0 ? { groupLabel: 'Orden', separatorBefore: true } : {}) })),
  ];

  const actions = { onRename: setRenaming, onDelete: setDeleting };
  const pagination = data ? <Pagination page={page} pageSize={DEFAULT_PAGE_SIZE} count={count} onPageChange={setPage} noun="proyectos" /> : null;
  const liveNote = anyInProgress ? (
    <span className={styles.live}>
      <StatusDot tone="info" live />
      Los proyectos en proceso se actualizan solos cada 5 s.
    </span>
  ) : (
    <span />
  );

  let body;
  if (query.isPending) {
    body = (
      <Card>
        <SkeletonRows rows={6} label="Cargando proyectos" />
      </Card>
    );
  } else if (!data) {
    body = null;
  } else if (workspaceEmpty) {
    body = <ProjectsEmpty />;
  } else if (projects.length === 0 && query.isPlaceholderData) {
    // The page before this one had no rows (a search without results): rows are on their way.
    body = (
      <Card>
        <SkeletonRows rows={6} label="Cargando proyectos" />
      </Card>
    );
  } else if (projects.length === 0) {
    body = (
      <EmptyState
        icon="search"
        title={term ? `Ningún proyecto coincide con «${term}»` : 'No hay proyectos en este estado'}
        description={term ? 'Probá con otra palabra del título.' : 'Cambiá el filtro para ver el resto.'}
        action={
          <Button variant="secondary" onClick={clearFilters}>
            Ver todos los proyectos
          </Button>
        }
      />
    );
  } else if (mobile) {
    body = (
      <>
        <ProjectsCards projects={projects} {...actions} />
        <div className={styles.mFoot}>
          {liveNote}
          {pagination}
        </div>
      </>
    );
  } else if (view === 'grid') {
    body = (
      <>
        <ProjectsGrid projects={projects} {...actions} />
        <div className={styles.gfoot}>
          {liveNote}
          {pagination}
        </div>
      </>
    );
  } else {
    body = (
      <Card>
        <ProjectsTable projects={projects} {...actions} />
        <div className={styles.listFoot}>
          {liveNote}
          {pagination}
        </div>
      </Card>
    );
  }

  const searchBox = (
    <div className={styles.search}>
      <Input
        type="search"
        icon="search"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
        placeholder={mobile ? 'Buscar por título' : 'Buscar por título…'}
        aria-label="Buscar proyectos por título"
      />
    </div>
  );

  return (
    <Page>
      <PageHead
        title="Proyectos"
        description={
          mobile && data && !workspaceEmpty ? (
            <span className="num">
              {plural(count, 'proyecto', 'proyectos')} · {order.summary}
            </span>
          ) : (
            `Todo lo que está pasando en ${workspace?.name ?? 'tu workspace'}.`
          )
        }
      />
      {query.isError && !pastTheEnd ? <LoadError className={styles.error} error={query.error} onRetry={() => void query.refetch()} fullPageForMissing={false} /> : null}
      {workspaceEmpty || (query.isPending && !data) ? null : mobile ? (
        <div className={styles.mTools}>
          {searchBox}
          <DropdownMenu
            ariaLabel="Filtros y orden"
            items={mobileItems}
            trigger={(props) => (
              <Button variant="secondary" iconLeft="filter" {...props}>
                Filtros
              </Button>
            )}
          />
        </div>
      ) : (
        <div className={styles.toolbar}>
          {searchBox}
          <FilterChips options={filterOptions} value={group} onChange={pickGroup} ariaLabel="Filtrar por estado" />
          <span className={styles.spacer} />
          <DropdownMenu
            ariaLabel="Orden"
            items={orderItems}
            trigger={(props) => (
              <Button size="sm" variant="secondary" iconRight="chevron-down" aria-label={`Orden: ${order.long.toLowerCase()}`} {...props}>
                {order.label}
              </Button>
            )}
          />
          <SegmentedControl<View>
            ariaLabel="Vista"
            value={view}
            onChange={setView}
            options={[
              { value: 'list', label: null, icon: 'list', ariaLabel: 'Vista de lista' },
              { value: 'grid', label: null, icon: 'grid', ariaLabel: 'Vista de grilla' },
            ]}
          />
        </div>
      )}
      {body}
      {renaming ? <RenameProjectDialog key={renaming.id} project={renaming} onClose={() => setRenaming(null)} /> : null}
      <DeleteProjectDialog open={deleting !== null} project={deleting} onClose={() => setDeleting(null)} onDeleted={() => toast.success('Proyecto borrado')} />
    </Page>
  );
}
