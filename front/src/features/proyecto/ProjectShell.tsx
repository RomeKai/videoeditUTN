'use client';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { Project } from '@/api/contracts/projects';
import { ApiError } from '@/api/errors';
import { useProject, useRenameProject } from '@/api/hooks';
import { DeleteProjectDialog } from '@/dialogs';
import { errorMessage } from '@/lib/error-messages';
import { useIsMobile } from '@/lib/hooks';
import { LoadError } from '@/shell/LoadError';
import { usePermissions } from '@/shell/session';
import {
  Avatar,
  Badge,
  Button,
  DateTime,
  Dialog,
  DropdownMenu,
  Duration,
  Field,
  IconButton,
  Input,
  Page,
  ProjectHeader,
  Skeleton,
  StepSegments,
  VideoThumb,
  useToast,
  type MenuItem,
  type ProjectHeaderMeta,
} from '@/ui';
import { buildProjectTabs, costLabel, currentTab, describeProjectStatus, headerStatusText, isWholeVideo, projectDuration, sourceInfo, type ProjectTabKey } from './model';
import { ProjectProvider, anyClipRendering } from './project-context';
import styles from './ProjectShell.module.css';
import { ProjectTabs } from './ProjectTabs';
import { recreateHref, useClipActions } from './useClipActions';

const MENU_LABEL = 'Más acciones: renombrar, duplicar configuración, borrar';

function RenameDialog({ project, open, onClose }: { project: Project; open: boolean; onClose: () => void }) {
  const rename = useRenameProject(project.id);
  const toast = useToast();
  const [title, setTitle] = useState(project.title);
  useEffect(() => {
    if (open) setTitle(project.title);
  }, [open, project.title]);
  const clean = title.trim();
  const close = () => {
    rename.reset();
    onClose();
  };
  return (
    <Dialog
      open={open}
      onClose={close}
      title="Renombrar el proyecto"
      size="sm"
      dismissable={!rename.isPending}
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={rename.isPending}>
            Cancelar
          </Button>
          <Button variant="primary" type="submit" form="rename-project" loading={rename.isPending} disabled={!clean || clean === project.title}>
            Guardar
          </Button>
        </>
      }
    >
      <form
        id="rename-project"
        onSubmit={(event) => {
          event.preventDefault();
          if (!clean || clean === project.title) return;
          rename.mutate(clean, {
            onSuccess: () => {
              toast.success('Proyecto renombrado');
              onClose();
            },
          });
        }}
      >
        <Field label="Nombre" error={rename.isError ? errorMessage(rename.error, 'No pudimos renombrar el proyecto.') : undefined}>
          <Input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={255} data-autofocus autoComplete="off" />
        </Field>
      </form>
    </Dialog>
  );
}

function HeaderActions({ project, tab, onRename, onDelete }: { project: Project; tab: ProjectTabKey; onRename: () => void; onDelete: () => void }) {
  const { gate, can } = usePermissions();
  const { downloadClip, downloadingIds } = useClipActions();
  const view = describeProjectStatus(project);
  const base = `/proyectos/${project.id}`;
  const clip = project.clips[0];
  // A single_edit project that was never priced as a whole video follows the clips flow.
  const action = view.action === 'result' && !isWholeVideo(project) ? 'clips' : view.action;
  // The clips are still there after a re-dispatch that could not be paid.
  const unpaid = project.pipeline_error_code === 'AutoRenderNotPaid' && project.clips.length > 0;

  // One primary action per screen: the next thing to do with this project.
  let primary: ReactNode = null;
  if (action === 'review' && project.status === 'awaiting_approval') {
    primary = (
      <Button variant="primary" href={`${base}/revision`}>
        Revisar clips
      </Button>
    );
  } else if ((action === 'clips' || (action === 'recreate' && unpaid && tab !== 'resumen')) && tab !== 'clips') {
    primary = (
      <Button variant="primary" href={`${base}/clips`}>
        Ver clips
      </Button>
    );
  } else if (action === 'result') {
    const ready = clip && (clip.status === 'completed' || clip.status === 'published');
    if (tab === 'resultado' && ready) {
      primary = (
        <Button variant="primary" iconLeft="download" loading={downloadingIds.includes(clip.id)} onClick={() => downloadClip(clip, 'Video descargado')}>
          Descargar
        </Button>
      );
    } else if (tab !== 'resultado') {
      primary = (
        <Button variant="primary" href={`${base}/resultado`}>
          Ver resultado
        </Button>
      );
    }
  } else if (action === 'recreate' && !unpaid && tab !== 'resumen') {
    primary = (
      <Button variant="primary" iconLeft="retry" href={recreateHref(project.id)} {...gate('project.create')}>
        Crear de nuevo
      </Button>
    );
  }

  const deleteContext = { ownerId: project.uploaded_by };
  const items: MenuItem[] = [
    { key: 'rename', label: 'Renombrar', icon: 'edit', onSelect: onRename, disabled: !can('project.rename'), ...gate('project.rename') },
    { key: 'duplicate', label: 'Duplicar configuración', icon: 'copy', href: recreateHref(project.id), disabled: !can('project.create'), ...gate('project.create') },
    {
      key: 'delete',
      label: 'Borrar',
      icon: 'trash',
      danger: true,
      separatorBefore: true,
      onSelect: onDelete,
      disabled: !can('project.delete', deleteContext),
      ...gate('project.delete', deleteContext),
    },
  ];
  const nothingAllowed = items.every((item) => item.disabled);
  return (
    <>
      {primary}
      {nothingAllowed ? (
        // Lector: the menu itself is shown disabled (artboard R2).
        <IconButton icon="more" label={MENU_LABEL} className={styles.menuButton} {...gate('project.rename')} />
      ) : (
        <DropdownMenu
          ariaLabel="Acciones del proyecto"
          items={items}
          trigger={(props) => <IconButton icon="more" label={MENU_LABEL} className={styles.menuButton} {...props} />}
        />
      )}
    </>
  );
}

function HeaderSkeleton() {
  return (
    <div aria-busy="true" aria-label="Cargando el proyecto">
      <h1 className="sr-only">Proyecto</h1>
      <Skeleton width={180} height={14} />
      <div className={styles.skelHead}>
        <Skeleton width={160} height={90} radius={8} className={styles.skelThumb} />
        <div className={styles.skelBody}>
          <Skeleton width="min(360px, 80%)" height={28} />
          <Skeleton width={200} height={14} />
          <Skeleton width="min(520px, 100%)" height={14} />
        </div>
      </div>
      <Skeleton width="100%" height={40} />
      <div className={styles.skelContent}>
        <Skeleton width="100%" height={280} radius={12} />
      </div>
    </div>
  );
}

/**
 * Layout of the project tabs: the single project header (title, state, data and
 * one primary action plus the menu) and the sub-tabs. Only the tab's content
 * changes below. Polling lives in `useProject` (every 5 s while in progress).
 */
export function ProjectShell({ projectId, children }: { projectId: string; children: ReactNode }) {
  const [deleting, setDeleting] = useState(false);
  const [deleted, setDeleted] = useState(false);
  // While the delete dialog is open, and for good once it deleted the project, nothing is fetched:
  // the screen keeps the last data until the list replaces it, instead of asking for a project that is gone.
  const query = useProject(projectId, { enabled: !deleting && !deleted, pollWhile: anyClipRendering });
  const lastProject = useRef(query.data);
  if (query.data) lastProject.current = query.data;
  const pathname = usePathname() ?? '';
  const router = useRouter();
  const isMobile = useIsMobile();
  const toast = useToast();
  const { can } = usePermissions();
  const [renaming, setRenaming] = useState(false);
  const project = query.data ?? (deleting || deleted ? lastProject.current : undefined);
  const tab = currentTab(pathname, projectId);

  // A whole-video project has Resultado instead of Clips, and the other way round.
  const single = project ? isWholeVideo(project) : false;
  const misplaced = project ? (single && tab === 'clips') || (!single && tab === 'resultado') : false;
  useEffect(() => {
    if (misplaced) router.replace(`/proyectos/${projectId}/${single ? 'resultado' : 'clips'}`);
  }, [misplaced, projectId, router, single]);

  if (!project) {
    if (query.isError) {
      const missing = query.error instanceof ApiError && (query.error.status === 404 || query.error.status === 403);
      return (
        <Page>
          {missing ? null : <h1 className={`t-h1 ${styles.errorTitle}`}>Proyecto</h1>}
          <LoadError error={query.error} onRetry={() => void query.refetch()} />
        </Page>
      );
    }
    return (
      <Page>
        <HeaderSkeleton />
      </Page>
    );
  }

  const view = describeProjectStatus(project);
  const source = sourceInfo(project);
  const duration = projectDuration(project);
  const creator = project.uploaded_by_detail;
  const meta: ProjectHeaderMeta[] = [{ key: 'source', icon: source.icon, content: source.label }];
  if (duration !== null) meta.push({ key: 'duration', icon: 'clock', content: <Duration seconds={duration} /> });
  if (creator) meta.push({ key: 'creator', content: <Avatar name={creator.name} seed={creator.id} size="xs" showName /> });
  meta.push({ key: 'date', icon: 'calendar', content: <DateTime value={project.created_at} /> });
  if (project.cost) {
    const label = costLabel(project.cost);
    meta.push({
      key: 'cost',
      icon: 'tokens',
      content: (
        <span className="num">
          {can('billing.manage') ? (
            <Link className={styles.link} href="/uso/movimientos">
              {label}
            </Link>
          ) : (
            label
          )}
          {project.cost.state === 'reserved' && view.key === 'waiting' ? ' · se devuelven si falla' : null}
        </span>
      ),
    });
  }

  return (
    <Page>
      {query.isError ? <LoadError error={query.error} onRetry={() => void query.refetch()} fullPageForMissing={false} className={styles.staleError} /> : null}
      <ProjectHeader
        crumbs={[{ label: 'Proyectos', href: '/proyectos' }, { label: project.title }]}
        compact={isMobile}
        thumb={isMobile ? undefined : <VideoThumb ratio="16:9" seed={project.id} src={project.thumbnail_url} duration={duration} />}
        title={project.title}
        badge={
          <>
            <IconButton
              icon="edit"
              size="sm"
              label="Renombrar el proyecto"
              className={styles.rename}
              onClick={() => setRenaming(true)}
              {...(can('project.rename') ? {} : { disabledReason: 'Pedile acceso a un admin' })}
            />
            <Badge tone={view.tone} dot>
              {view.label}
            </Badge>
          </>
        }
        statusText={
          <>
            <span className="num">{headerStatusText(project, view)}</span>
            {isMobile && view.step ? <StepSegments wide done={view.step.current - 1} className={styles.mobileSegs} /> : null}
          </>
        }
        meta={meta}
        actions={<HeaderActions project={project} tab={tab} onRename={() => setRenaming(true)} onDelete={() => setDeleting(true)} />}
      />
      <ProjectTabs tabs={buildProjectTabs(project)} current={tab} />
      <ProjectProvider value={project}>{misplaced ? null : children}</ProjectProvider>
      <RenameDialog project={project} open={renaming} onClose={() => setRenaming(false)} />
      <DeleteProjectDialog
        open={deleting}
        onClose={() => setDeleting(false)}
        project={project}
        onDeleted={() => {
          setDeleted(true);
          toast.success('Proyecto borrado');
          router.replace('/proyectos');
        }}
      />
    </Page>
  );
}
