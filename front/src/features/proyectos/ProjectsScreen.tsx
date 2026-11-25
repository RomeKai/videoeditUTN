'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { DEFAULT_PAGE_SIZE } from '@/api/contracts/common';
import type { Project, ProjectStatusGroup } from '@/api/contracts/projects';
import { ApiError } from '@/api/errors';
import { useProjects } from '@/api/hooks';
import { keys } from '@/api/query';
import { DeleteProjectDialog } from '@/dialogs';
import { plural } from '@/lib/format';
import { useDebounced, useIsMobile, useStoredState } from '@/lib/hooks';
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
import { ORDERINGS, STATUS_FILTERS, applyListQuery, type Ordering } from './project-view';

type View = 'list' | 'grid';

/** B2 · Proyectos: todo lo que pasa en el workspace, con buscador, filtro por estado, orden y dos vistas. */
export function ProjectsScreen() {
  const { workspace } = useSession();
  const toast = useToast();
  const queryClient = useQueryClient();
  const mobile = useIsMobile();
  const [view, setView] = useStoredState<View>('oc.proyectos.vista', 'list');
  const [search, setSearch] = useState('');
  const [group, setGroup] = useState<ProjectStatusGroup>('all');
  const [ordering, setOrdering] = useState<Ordering>('-created_at');
  const [paging, setPaging] = useState({ scope: '', page: 1 });
  const [renaming, setRenaming] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState<Project | null>(null);
  const term = useDebounced(search.trim(), 300);

  // The page belongs to one search in one workspace: a new search or another workspace starts on the first
  // page in the same render, so no request ever asks for a page of the previous list.
  const workspaceId = workspace?.id ?? null;
  const scope = `${workspaceId ?? ''}|${term}`;
  const askedPage = paging.scope === scope ? paging.page : 1;
  const setPage = (next: number) => setPaging({ scope, page: Math.max(1, next) });

  const query = useProjects({
    page: askedPage,
    page_size: DEFAULT_PAGE_SIZE,
    ...(group !== 'all' ? { status_group: group } : {}),
    ...(term ? { search: term } : {}),
    ...(ordering !== '-created_at' ? { ordering } : {}),
  });
  const data = query.data;
  // Today's backend answers one array and ignores the query: filter, search, order and pages happen here.
  const local = useMemo(
    () => (data && !data.paginated ? applyListQuery(data.results, { group, term, ordering, page: askedPage, pageSize: DEFAULT_PAGE_SIZE, workspaceId }) : null),
    [data, group, term, ordering, askedPage, workspaceId],
  );
  const projects = local?.results ?? data?.results ?? [];
  const counts = local?.counts ?? data?.status_counts;
  const count = local?.count ?? data?.count ?? 0;
  const page = local?.page ?? askedPage;
  const filtered = group !== 'all' || term !== '';
  const total = counts?.all ?? count;
  const workspaceEmpty = Boolean(data) && !filtered && total === 0;
  const anyInProgress = projects.some((project) => describeProjectStatus(project).inProgress);
  const order = ORDERINGS.find((item) => item.value === ordering) ?? ORDERINGS[0];

  // A page past the end answers 404: the last row was deleted (here or by someone else), or the list got
  // shorter while polling. Step back until a page exists.
  const pastTheEnd = query.error instanceof ApiError && query.error.status === 404 && askedPage > 1;
  useEffect(() => {
    if (pastTheEnd) {
      // The pages kept in memory are as outdated as the one that just disappeared.
      void queryClient.invalidateQueries({ queryKey: keys.projects.all });
      setPaging((current) => ({ scope, page: Math.max(1, (current.scope === scope ? current.page : 1) - 1) }));
    } else if (data?.paginated && data.results.length === 0 && data.count > 0 && askedPage > 1) setPaging({ scope, page: Math.max(1, Math.ceil(data.count / DEFAULT_PAGE_SIZE)) });
  }, [pastTheEnd, data, askedPage, scope, queryClient]);

  const pickGroup = (next: ProjectStatusGroup) => {
    setGroup(next);
    setPage(1);
  };
  const pickOrdering = (next: Ordering) => {
    setOrdering(next);
    setPage(1);
  };
  const clearFilters = () => {
    setSearch('');
    pickGroup('all');
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
