'use client';

import Link from 'next/link';
import type { Project } from '@/api/contracts/projects';
import { plural } from '@/lib/format';
import { describeProjectStatus } from '@/lib/project-status';
import { usePermissions } from '@/shell/session';
import { Avatar, Button, Card, DateTime, DropdownMenu, Duration, Icon, IconButton, Table, VideoThumb, cx, type MenuItem } from '@/ui';
import { ProjectStatusLine } from './ProjectStatusLine';
import styles from './Projects.module.css';
import { primaryAction, projectDuration, projectSource, recreateHref } from './project-view';

export interface ProjectActions {
  onRename: (project: Project) => void;
  onDelete: (project: Project) => void;
}

/** Abrir, renombrar, duplicar configuración y borrar (map B2). Lo que el rol no permite queda deshabilitado, no oculto. */
function useProjectMenu({ onRename, onDelete }: ProjectActions) {
  const { can, reason } = usePermissions();
  return (project: Project): MenuItem[] => [
    { key: 'open', label: 'Abrir', icon: 'external', href: `/proyectos/${project.id}` },
    { key: 'rename', label: 'Renombrar', icon: 'edit', onSelect: () => onRename(project), disabled: !can('project.rename'), disabledReason: reason },
    {
      key: 'duplicate',
      label: 'Duplicar configuración',
      icon: 'copy',
      href: recreateHref(project.id),
      disabled: !can('project.create'),
      disabledReason: reason,
    },
    {
      key: 'delete',
      label: 'Borrar',
      icon: 'trash',
      danger: true,
      separatorBefore: true,
      onSelect: () => onDelete(project),
      disabled: !can('project.delete', { ownerId: project.uploaded_by }),
      disabledReason: reason,
    },
  ];
}

function MoreMenu({ project, items, size }: { project: Project; items: MenuItem[]; size?: 'sm' | 'md' | 'lg' }) {
  return (
    <DropdownMenu
      ariaLabel={`Más acciones de ${project.title}`}
      items={items}
      trigger={(props) => <IconButton icon="more" size={size} label={`Más acciones de ${project.title}: abrir, renombrar, borrar`} tooltip={false} {...props} />}
    />
  );
}

function MainAction({ project, block, size = 'sm' }: { project: Project; block?: boolean; size?: 'sm' | 'md' }) {
  const { gate } = usePermissions();
  const action = primaryAction(project, describeProjectStatus(project));
  if (!action) return null;
  return (
    <Button size={size} block={block} variant={action.variant} href={action.href} {...(action.needsCreate ? gate('project.create') : {})}>
      {action.label}
    </Button>
  );
}

function Name({ project, clamp }: { project: Project; clamp?: boolean }) {
  const source = projectSource(project);
  return (
    <Link className={styles.name} href={`/proyectos/${project.id}`}>
      <span className={styles.src} title={source.label}>
        <Icon name={source.icon} size={14} />
      </span>
      <span className="sr-only">{source.label}: </span>
      <span className={clamp ? styles.nameClamp : styles.nameTxt} title={project.title}>
        {project.title}
      </span>
    </Link>
  );
}

function Creator({ project, size = 'sm' }: { project: Project; size?: 'xs' | 'sm' }) {
  const who = project.uploaded_by_detail;
  if (!who) return <span className={styles.dash}>—</span>;
  return (
    <span title={who.name}>
      <Avatar name={who.name} seed={who.id} size={size} />
    </span>
  );
}

function ClipsCount({ project }: { project: Project }) {
  if (project.project_type === 'single_edit') {
    return (
      <span className={styles.dash} aria-label="Video completo, sin clips">
        —
      </span>
    );
  }
  if (project.clips.length === 0) {
    return (
      <span className={styles.dash} aria-label="Todavía no hay clips">
        —
      </span>
    );
  }
  return <>{project.clips.length}</>;
}

/** Vista principal (B2-Proyectos): filas de alto fijo, el estado en una sola línea. */
export function ProjectsTable({ projects, ...actions }: { projects: Project[] } & ProjectActions) {
  const menu = useProjectMenu(actions);
  return (
    <Table className={styles.projects} caption="Proyectos del workspace">
      <colgroup>
        <col />
        <col className={styles.cSt} />
        <col className={styles.cDur} />
        <col className={styles.cClips} />
        <col className={styles.cWho} />
        <col className={styles.cDate} />
        <col className={styles.cAct} />
        <col className={styles.cMenu} />
      </colgroup>
      <thead>
        <tr>
          <th scope="col">Proyecto</th>
          <th scope="col">Estado</th>
          <th scope="col">Duración</th>
          <th scope="col">Clips</th>
          <th scope="col">
            <span aria-hidden="true">Creó</span>
            <span className="sr-only">Creado por</span>
          </th>
          <th scope="col">Fecha</th>
          <th scope="col">
            <span className="sr-only">Acción principal</span>
          </th>
          <th scope="col">
            <span className="sr-only">Más acciones</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {projects.map((project) => {
          const duration = projectDuration(project);
          return (
            <tr key={project.id}>
              <td>
                <div className={styles.proj}>
                  <VideoThumb className={styles.projThumb} seed={project.id} src={project.thumbnail_url} duration={duration} />
                  <Name project={project} />
                </div>
              </td>
              <td>
                <ProjectStatusLine project={project} />
              </td>
              <td className="num">{duration === null ? <span className={styles.dash}>—</span> : <Duration seconds={duration} />}</td>
              <td className="num">
                <ClipsCount project={project} />
              </td>
              <td>
                <Creator project={project} />
              </td>
              <td>
                <DateTime className={styles.when} value={project.created_at} format="relative" />
              </td>
              <td className={styles.act}>
                <MainAction project={project} block />
              </td>
              <td>
                <MoreMenu project={project} items={menu(project)} />
              </td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}

/** Vista alternativa (B2c-Proyectos-grilla). */
export function ProjectsGrid({ projects, ...actions }: { projects: Project[] } & ProjectActions) {
  const menu = useProjectMenu(actions);
  return (
    <ul className={styles.pgrid} aria-label="Proyectos del workspace">
      {projects.map((project) => (
        <Card as="li" key={project.id} className={styles.pcard}>
          <div className={styles.pcardTop}>
            <VideoThumb className={styles.pcardThumb} seed={project.id} src={project.thumbnail_url} duration={projectDuration(project)} />
            <Name project={project} />
            <ProjectStatusLine project={project} className={styles.pcardSt} />
          </div>
          <div className={styles.pcardFoot}>
            <Creator project={project} />
            <DateTime className={cx(styles.when, styles.pcardWhen)} value={project.created_at} format="relative" />
            <MainAction project={project} />
            <MoreMenu project={project} items={menu(project)} />
          </div>
        </Card>
      ))}
    </ul>
  );
}

/** Teléfono (M1-Movil-proyectos): tarjetas apiladas. */
export function ProjectsCards({ projects, ...actions }: { projects: Project[] } & ProjectActions) {
  const menu = useProjectMenu(actions);
  return (
    <ul className={styles.mList} aria-label="Proyectos del workspace">
      {projects.map((project) => {
        const source = projectSource(project);
        const view = describeProjectStatus(project);
        const clips = project.project_type === 'single_edit' ? 0 : project.clips.length;
        return (
          <Card as="li" key={project.id} className={styles.mCard}>
            <Link className={styles.mLink} href={`/proyectos/${project.id}`}>
              <VideoThumb className={styles.mThumb} seed={project.id} src={project.thumbnail_url} duration={projectDuration(project)} xs />
              <span className={styles.mBody}>
                <span className={styles.mTitle}>{project.title}</span>
                <ProjectStatusLine project={project} view={view} inline />
                <span className={styles.mMeta}>
                  {project.uploaded_by_detail ? <Avatar name={project.uploaded_by_detail.name} seed={project.uploaded_by_detail.id} size="xs" /> : null}
                  <Icon name={source.icon} size={12} />
                  <span className="sr-only">{source.label}</span>
                  <span className={cx('num', styles.mEllip)}>
                    {clips > 0 ? `${plural(clips, 'clip', 'clips')} · ` : ''}
                    <DateTime value={project.created_at} format="relative" />
                  </span>
                </span>
              </span>
            </Link>
            <span className={styles.mMore}>
              <MoreMenu project={project} items={menu(project)} size="lg" />
            </span>
            {/* M1: only the action that needs the user (review) gets its own button; the rest open from the card. */}
            {view.action === 'review' ? (
              <div className={styles.mCta}>
                <MainAction project={project} block size="md" />
              </div>
            ) : null}
          </Card>
        );
      })}
    </ul>
  );
}
