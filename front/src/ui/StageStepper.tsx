import type { ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Icon } from './Icon';
import styles from './StageStepper.module.css';

export type StageState = 'done' | 'run' | 'wait' | 'fail' | 'pending';

export interface StageStep {
  key: string;
  label: ReactNode;
  state: StageState;
  /** Second line: "Terminado · empezó 14:18:06", "En curso · Paso 2 de 4", the failure message… */
  detail?: ReactNode;
  /** Right column: "0:42" or "—". */
  duration?: ReactNode;
  /** Heading drawn before this step ("Después de tu revisión" before Renderizar). */
  separatorBefore?: ReactNode;
}

const STATE_LABEL: Record<StageState, string> = {
  done: 'Terminado',
  run: 'En curso',
  wait: 'En espera',
  fail: 'Falló',
  pending: 'Pendiente',
};

/** Timeline of the process, deploy-style (kit: .steps / .step). Running is blue, waiting amber dashed, failed red. */
export function StageStepper({ steps, className, ariaLabel = 'Proceso' }: { steps: StageStep[]; className?: string; ariaLabel?: string }) {
  return (
    <ol className={cx(styles.steps, className)} aria-label={ariaLabel}>
      {steps.map((step) => (
        <li key={step.key} style={{ display: 'contents' }}>
          {step.separatorBefore ? <div className={cx(styles.sep, 't-over')}>{step.separatorBefore}</div> : null}
          <div className={cx(styles.step, step.state === 'pending' && styles.pending)} aria-current={step.state === 'run' ? 'step' : undefined}>
            <span className={cx(styles.icon, step.state !== 'pending' && styles[step.state])} aria-hidden="true">
              {step.state === 'done' ? <Icon name="check" size={12} /> : null}
              {step.state === 'fail' ? <Icon name="close" size={12} /> : null}
              {step.state === 'run' ? <span className={styles.runDot} /> : null}
            </span>
            <div>
              <p className={styles.label}>{step.label}</p>
              <p className={cx(styles.detail, step.state !== 'done' && styles[step.state])}>
                <span className="sr-only">{STATE_LABEL[step.state]}. </span>
                {step.detail ?? STATE_LABEL[step.state]}
              </p>
            </div>
            <span className={cx('num', styles.duration)}>{step.duration ?? '—'}</span>
          </div>
        </li>
      ))}
    </ol>
  );
}
