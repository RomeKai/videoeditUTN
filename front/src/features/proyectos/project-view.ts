import { isOfWorkspace } from '@/api/config';
import type { Project, ProjectStatusGroup } from '@/api/contracts/projects';
import { projectStatusGroup, type ProjectStatusView } from '@/lib/project-status';
import type { ButtonVariant, IconName } from '@/ui';

/** What the list needs of a project (the list endpoint sends the whole serializer). */
export type ProjectRow = Project;

const PROVIDERS: Array<[RegExp, string]> = [
  [/(^|\.)youtube\.com$|(^|\.)youtu\.be$/, 'YouTube'],
  [/(^|\.)vimeo\.com$/, 'Vimeo'],
  [/(^|\.)twitch\.tv$/, 'Twitch'],
  [/(^|\.)drive\.google\.com$/, 'Google Drive'],
];

/** "YouTube" for a YouTube link; null when the site is not one we name. */
export function linkProvider(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const host = new URL(url).hostname.toLowerCase();
    return PROVIDERS.find(([pattern]) => pattern.test(host))?.[1] ?? null;
  } catch {
    return null;
  }
}

export interface ProjectSource {
  kind: 'file' | 'link';
  /** "Archivo", "Link de YouTube", "Link". */
  label: string;
  icon: IconName;
}

/** Origin of the project (map B2: archivo o link). */
export function projectSource(project: Pick<Project, 'video_url' | 'source_file'>): ProjectSource {
  if (project.video_url) {
    const provider = linkProvider(project.video_url);
    return { kind: 'link', label: provider ? `Link de ${provider}` : 'Link', icon: 'link' };
  }
  return { kind: 'file', label: 'Archivo', icon: 'file' };
}

/** Source duration in seconds, wherever the backend left it. */
export function projectDuration(project: Pick<Project, 'duration_seconds' | 'metadata'>): number | null {
  return project.duration_seconds ?? project.metadata.source_media?.duration_s ?? project.metadata.duration ?? null;
}

export interface PrimaryAction {
  label: string;
  href: string;
  variant: ButtonVariant;
  /** Creating again spends tokens: gated by role. */
  needsCreate: boolean;
}

/** Main action of map §3 for the state the project is in. */
export function primaryAction(project: Pick<Project, 'id'>, view: ProjectStatusView): PrimaryAction | null {
  switch (view.action) {
    case 'review':
      return { label: 'Revisar clips', href: `/proyectos/${project.id}/revision`, variant: 'accent', needsCreate: false };
    case 'clips':
      return { label: 'Ver clips', href: `/proyectos/${project.id}/clips`, variant: 'secondary', needsCreate: false };
    case 'result':
      return { label: 'Ver resultado', href: `/proyectos/${project.id}/resultado`, variant: 'secondary', needsCreate: false };
    case 'recreate':
      return { label: 'Crear de nuevo', href: recreateHref(project.id), variant: 'secondary', needsCreate: true };
    default:
      return null;
  }
}

/** Nuevo proyecto opens with the configuration of this project ("Crear de nuevo", "Duplicar configuración"). */
export function recreateHref(projectId: string): string {
  return `/proyectos/nuevo?desde=${projectId}`;
}

export const STATUS_FILTERS = [
  { value: 'all', label: 'Todos' },
  { value: 'in_progress', label: 'En proceso' },
  { value: 'to_review', label: 'Para revisar' },
  { value: 'ready', label: 'Listos' },
  { value: 'failed', label: 'Con error' },
] as const;

export const ORDERINGS = [
  { value: '-created_at', label: 'Recientes', long: 'Más recientes', summary: 'recientes primero' },
  { value: 'created_at', label: 'Más antiguos', long: 'Más antiguos', summary: 'más antiguos primero' },
  { value: 'title', label: 'Título (A–Z)', long: 'Título (A–Z)', summary: 'por título' },
] as const;

export type Ordering = (typeof ORDERINGS)[number]['value'];

export interface ListQuery {
  group: ProjectStatusGroup;
  /** Already trimmed. */
  term: string;
  ordering: Ordering;
  /** 1-based. */
  page: number;
  pageSize: number;
  /**
   * Active workspace. The backend's list brings the projects of every workspace the user belongs to (it does
   * not read X-Workspace-Id yet), so the rows and the counts keep only this one's.
   */
  workspaceId?: string | null;
}

export interface ListPage {
  results: Project[];
  /** Projects that match the filter and the search, in every page. */
  count: number;
  /** Per status filter, among the ones that match the search (as the paginated API counts them). */
  counts: Record<ProjectStatusGroup, number>;
  /** The page actually shown: the asked one, clamped to the last that exists. */
  page: number;
}

/**
 * The list query applied in the browser, for a backend that answers every
 * project in one array and ignores page, filter, search and order (gap 14).
 * Same rules as the paginated endpoint: the search matches the title.
 */
export function applyListQuery(projects: ReadonlyArray<Project>, query: ListQuery): ListPage {
  const term = query.term.toLowerCase();
  // The backend scopes the list by `X-Workspace-Id` (PR #125). Kept as a cheap defence: a project of
  // another workspace must never show up here, whatever the list brings.
  // (`isOfWorkspace`: in hybrid mode the active workspace can be a placeholder whose real id is not known.)
  const workspaceId = query.workspaceId;
  const own = workspaceId ? projects.filter((project) => isOfWorkspace(project.workspace, workspaceId)) : projects;
  const matching = term ? own.filter((project) => project.title.toLowerCase().includes(term)) : [...own];
  const counts: Record<ProjectStatusGroup, number> = { all: matching.length, in_progress: 0, to_review: 0, ready: 0, failed: 0 };
  const groups = new Map<string, ProjectStatusGroup>();
  for (const project of matching) {
    const group = projectStatusGroup(project);
    groups.set(project.id, group);
    counts[group] += 1;
  }
  const filtered = query.group === 'all' ? matching : matching.filter((project) => groups.get(project.id) === query.group);
  filtered.sort((a, b) =>
    query.ordering === 'title' ? a.title.localeCompare(b.title, 'es') : query.ordering === 'created_at' ? a.created_at.localeCompare(b.created_at) : b.created_at.localeCompare(a.created_at),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / query.pageSize));
  const page = Math.min(Math.max(1, query.page), pages);
  return { results: filtered.slice((page - 1) * query.pageSize, page * query.pageSize), count: filtered.length, counts, page };
}
