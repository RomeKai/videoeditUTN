import type { Project, ProjectListItem } from '@/api/contracts/projects';
import type { ProjectStatusView } from '@/lib/project-status';
import type { ButtonVariant, IconName } from '@/ui';

/** A project as the list endpoint sends it: without the transcript and the selection log. */
export type ProjectRow = ProjectListItem;

/**
 * The hosts the backend downloads links from, exactly (`LINK_HOSTS` of
 * back/apps/videos/services/link_validation.py): no other subdomain.
 */
export const LINK_HOSTS: Readonly<Record<string, string>> = {
  'youtube.com': 'YouTube',
  'www.youtube.com': 'YouTube',
  'm.youtube.com': 'YouTube',
  'music.youtube.com': 'YouTube',
  'youtu.be': 'YouTube',
  'vimeo.com': 'Vimeo',
  'www.vimeo.com': 'Vimeo',
  'player.vimeo.com': 'Vimeo',
  'twitch.tv': 'Twitch',
  'www.twitch.tv': 'Twitch',
  'm.twitch.tv': 'Twitch',
  'clips.twitch.tv': 'Twitch',
  'player.twitch.tv': 'Twitch',
  'drive.google.com': 'Google Drive',
};

/** "YouTube" for a YouTube link; null when the site is not one we name. */
export function linkProvider(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const host = new URL(url).hostname.toLowerCase().replace(/\.$/, '');
    return Object.hasOwn(LINK_HOSTS, host) ? (LINK_HOSTS[host] ?? null) : null;
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
