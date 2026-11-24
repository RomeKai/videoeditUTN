import type { Project } from '@/api/contracts/projects';
import { countClips, describeProjectStatus, type ProjectStatusView } from '@/lib/project-status';
import { Badge, ProgressBar, StepSegments, cx } from '@/ui';
import styles from './Projects.module.css';

export interface ProjectStatusLineProps {
  project: Project;
  /** Computed once per row by the caller, which also needs it for the main action. */
  view?: ProjectStatusView;
  /** Phone cards: the label and the text flow in one wrapping line. */
  inline?: boolean;
  className?: string;
}

/**
 * State of a project in the list (map §3 and 9.1.3): the label of section 3 and
 * one short line. The full text stays in the tooltip and on the project screen.
 */
export function ProjectStatusLine({ project, view = describeProjectStatus(project), inline = false, className }: ProjectStatusLineProps) {
  const clips = countClips(project.clips);
  const rendering = view.key === 'rendering' && project.project_type !== 'single_edit' && clips.total > 0;
  return (
    <span className={cx(styles.st, inline && styles.stInline, className)}>
      <Badge tone={view.tone} dot>
        {view.label}
      </Badge>
      <span className={styles.stLine}>
        <span className={cx(styles.stText, 'num')} title={view.text !== view.shortText ? view.text : undefined}>
          {view.shortText}
        </span>
        {view.step ? <StepSegments done={view.step.current - 1} running label={`Pasos completados: ${view.step.current - 1} de 4`} /> : null}
        {rendering ? <ProgressBar className={styles.stProgress} value={clips.ready} max={clips.total} label={`Clips renderizados: ${clips.ready} de ${clips.total}`} /> : null}
      </span>
    </span>
  );
}
