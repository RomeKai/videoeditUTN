'use client';

import Link from 'next/link';
import { useEffect } from 'react';
import { usePlans, useUpdateMe } from '@/api/hooks';
import { useStoredState } from '@/lib/hooks';
import { firstPlanWith } from '@/lib/pricing';
import { usePermissions, usePlan, useSession } from '@/shell/session';
import { Button, Card, EmptyState, Icon, IconButton, StepSegments, cx, type IconName } from '@/ui';
import styles from './Projects.module.css';

/** Tres ejemplos de qué se puede pedir con IA (map B2 · vacío). */
export const AI_EXAMPLES: Array<{ title: string; prompt: string; icon: IconName }> = [
  { title: 'Lo más gracioso', prompt: 'Sacá 5 clips de 30 s de las partes más graciosas, subtítulos grandes amarillos', icon: 'sparkles' },
  { title: 'Top 3 consejos', prompt: 'Encontrá los 3 mejores consejos del episodio y armá un clip de menos de un minuto con cada uno', icon: 'star' },
  { title: 'Un clip por pregunta', prompt: 'Quiero un clip por cada pregunta de la entrevista, en vertical y con subtítulos minimalistas', icon: 'captions' },
];

export function promptHref(prompt: string): string {
  return `/proyectos/nuevo?origen=ia&prompt=${encodeURIComponent(prompt)}`;
}

interface Step {
  key: string;
  title: string;
  detail: string;
  href: string;
  done: boolean;
  lockedPlan?: string;
}

function FirstSteps() {
  const { me } = useSession();
  const { plan } = usePlan();
  const plans = usePlans();
  const [hidden, setHidden] = useStoredState<string | null>('oc.proyectos.primeros-pasos.oculto', null);
  const { mutate: updateMe } = useUpdateMe();
  const onboarding = me?.onboarding;
  const allDone = Boolean(onboarding?.has_project && onboarding.has_brand_kit && onboarding.has_social_account && onboarding.has_invited_member);
  const tourPending = me ? !me.tour_completed : false;
  // Finishing the list ends the first-time tour (map B1), the same as closing it.
  useEffect(() => {
    if (allDone && tourPending) updateMe({ tour_completed: true });
  }, [allDone, tourPending, updateMe]);
  // The server's word wins: once the tour is done the list is gone in every browser. The local mark only covers
  // the moment between closing it and the answer.
  if (!me || me.tour_completed || hidden === me.id) return null;
  const close = () => {
    setHidden(me.id);
    updateMe({ tour_completed: true });
  };

  const done = me.onboarding;
  const teamPlan = plan && plan.max_members <= 1 ? firstPlanWith(plans.data ?? [], (item) => item.max_members > 1) : null;
  const steps: Step[] = [
    { key: 'project', title: 'Subir tu primer video', detail: 'Un archivo o un link de YouTube.', href: '/proyectos/nuevo', done: Boolean(done?.has_project) },
    { key: 'kit', title: 'Crear tu brand kit', detail: 'Tus colores, tu tipografía y tu logo.', href: '/brand-kits', done: Boolean(done?.has_brand_kit) },
    { key: 'social', title: 'Conectar una red', detail: 'TikTok, Instagram o YouTube.', href: '/ajustes/redes', done: Boolean(done?.has_social_account) },
    {
      key: 'invite',
      title: 'Invitar a alguien',
      detail: 'Sumá a tu equipo al workspace.',
      href: teamPlan ? '/uso/planes' : '/ajustes/miembros',
      done: Boolean(done?.has_invited_member),
      lockedPlan: teamPlan?.name,
    },
  ];
  const count = steps.filter((step) => step.done).length;

  return (
    <Card as="aside" className={styles.side} aria-labelledby="b2-steps">
      <div className={styles.todoHead}>
        <h2 className="t-label" id="b2-steps">
          Primeros pasos
        </h2>
        <span className={cx('t-label muted num', styles.todoCount)}>
          {count} de {steps.length}
        </span>
        <StepSegments className={styles.todoSegs} total={steps.length} done={count} running={false} label={`Primeros pasos completados: ${count} de ${steps.length}`} />
        <IconButton icon="close" size="sm" label="Cerrar la lista de primeros pasos" onClick={close} />
      </div>
      <ol className={styles.todo}>
        {steps.map((step) => (
          <li key={step.key} className={cx(step.lockedPlan && !step.done && styles.todoLocked)}>
            <Link href={step.href}>
              {step.done ? (
                <span className={styles.todoDone} role="img" aria-label="Hecho">
                  <Icon name="check" size={10} />
                </span>
              ) : (
                <span className={styles.todoCircle} role="img" aria-label="Pendiente" />
              )}
              <span className={styles.todoText}>
                <span>{step.title}</span>
                {step.lockedPlan && !step.done ? (
                  <span className={styles.todoLock}>
                    <Icon name="lock" size={12} />
                    Disponible en el plan {step.lockedPlan}
                  </span>
                ) : (
                  <span>{step.detail}</span>
                )}
              </span>
              <span className={styles.todoGo}>
                <Icon name="chevron-right" size={14} />
              </span>
            </Link>
          </li>
        ))}
      </ol>
    </Card>
  );
}

/** B2b · Proyectos vacío: una frase, una acción, tres pedidos de ejemplo y los primeros pasos. */
export function ProjectsEmpty() {
  const { gate, can, reason } = usePermissions();
  const canCreate = can('project.create');
  return (
    <div className={styles.cols}>
      <div className={styles.colMain}>
        <EmptyState
          icon="video"
          title="Todavía no hay proyectos"
          description="Subí un video largo o pegá un link, y la IA encuentra los mejores momentos y los convierte en clips verticales."
          action={
            <Button variant="primary" iconLeft="upload" href="/proyectos/nuevo" {...gate('project.create')}>
              Subí tu primer video
            </Button>
          }
        />
        <section aria-labelledby="b2-ai">
          <div className={styles.aiHead}>
            <h2 className="t-h3" id="b2-ai">
              O pedíselo a la IA con tus palabras
            </h2>
            <p className="t-small muted">Tres pedidos de ejemplo. Elegí uno y sumale tu video.</p>
          </div>
          <Card className={styles.prompts}>
            {AI_EXAMPLES.map((example) => {
              const content = (
                <>
                  <span className={styles.promptIco}>
                    <Icon name={example.icon} />
                  </span>
                  <span className={styles.promptText}>
                    <span className={styles.promptTitle}>{example.title}</span>
                    <span className={styles.promptQ}>«{example.prompt}»</span>
                  </span>
                  <span className={styles.promptGo}>
                    <Icon name={canCreate ? 'chevron-right' : 'lock'} />
                  </span>
                </>
              );
              // Creating spends tokens: a role that cannot create sees the examples, disabled, with the reason.
              return canCreate ? (
                <Link key={example.title} className={styles.prompt} href={promptHref(example.prompt)}>
                  {content}
                </Link>
              ) : (
                <span key={example.title} className={cx(styles.prompt, styles.promptOff)} role="link" aria-disabled="true" title={reason} tabIndex={0}>
                  {content}
                  <span className="sr-only">. {reason}</span>
                </span>
              );
            })}
          </Card>
        </section>
      </div>
      <FirstSteps />
    </div>
  );
}
