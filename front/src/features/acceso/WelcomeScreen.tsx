'use client';

import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from 'react';
import type { Me, OnboardingUseCase, WorkspaceSummary } from '@/api/contracts/users';
import { ApiError } from '@/api/errors';
import { useUpdateMe, useUpdateWorkspace } from '@/api/hooks';
import { fieldError } from '@/lib/error-messages';
import { formatMinutes } from '@/lib/format';
import { markWelcomeSeen, usePermissions, usePlan, useSession } from '@/shell/session';
import { Avatar, Badge, Banner, Button, Card, CardFoot, Field, Icon, Input, Num, Tokens, cx } from '@/ui';
import styles from './acceso.module.css';
import { HOME, requestErrorMessage } from './lib';

export type WelcomeStep = 1 | 2 | 3;

const STEP_LABELS = ['Tu workspace', 'Para qué lo usás', 'Tu primer video'] as const;
export const WORKSPACE_NAME_MAX = 40;

interface UseCase {
  value: OnboardingUseCase;
  name: string;
  description: string;
  /** Subtitle style suggested for the first project (map B1, step 2). */
  style: string;
  sample: 'hormozi' | 'dinamico' | 'minimal' | 'vlog';
  icon: ReactNode;
}

const glyph = (children: ReactNode) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    {children}
  </svg>
);

export const USE_CASES: UseCase[] = [
  {
    value: 'podcast',
    name: 'Podcast',
    description: 'Entrevistas, charlas y episodios largos.',
    style: 'Hormozi',
    sample: 'hormozi',
    icon: glyph(
      <>
        <rect x="9" y="3" width="6" height="11" rx="3" />
        <path d="M5 11a7 7 0 0 0 14 0" />
        <path d="M12 18v3" />
      </>,
    ),
  },
  {
    value: 'gaming',
    name: 'Gaming',
    description: 'Streams, partidas y reacciones.',
    style: 'Dinámico',
    sample: 'dinamico',
    icon: glyph(
      <>
        <rect x="2.5" y="7" width="19" height="11" rx="4" />
        <path d="M7 12.5h4" />
        <path d="M9 10.5v4" />
        <path d="M15.5 11.5h.01" />
        <path d="M18 13.5h.01" />
      </>,
    ),
  },
  {
    value: 'education',
    name: 'Educación',
    description: 'Clases, cursos y webinars.',
    style: 'Minimalista',
    sample: 'minimal',
    icon: glyph(
      <>
        <path d="M4 5a2 2 0 0 1 2-2h13v15H6a2 2 0 0 0-2 2z" />
        <path d="M4 20a2 2 0 0 0 2 2h13v-4" />
      </>,
    ),
  },
  {
    value: 'other',
    name: 'Otro',
    description: 'Vlogs, eventos o un poco de todo.',
    style: 'Vlog',
    sample: 'vlog',
    icon: glyph(
      <>
        <circle cx="7.5" cy="16.5" r="3.5" />
        <rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1" />
        <path d="M12 3.5 15.5 10h-7z" />
      </>,
    ),
  },
];

function Steps({ step }: { step: WelcomeStep }) {
  return (
    <ol className={styles.steps} aria-label={`Paso ${step} de 3`}>
      {STEP_LABELS.map((label, index) => {
        const number = index + 1;
        const done = number < step;
        return (
          <FragmentWithBar key={label} bar={index > 0}>
            <li className={cx(done && styles.stepDone, number === step && styles.stepNow)} aria-current={number === step ? 'step' : undefined}>
              <span className={styles.stepNumber}>
                {done ? (
                  <>
                    <Icon name="check" size={11} />
                    <span className="sr-only">Listo:</span>
                  </>
                ) : (
                  number
                )}
              </span>
              {label}
            </li>
          </FragmentWithBar>
        );
      })}
    </ol>
  );
}

function FragmentWithBar({ bar, children }: { bar: boolean; children: ReactNode }) {
  return (
    <>
      {bar ? <li className={styles.stepBar} aria-hidden="true" /> : null}
      {children}
    </>
  );
}

/**
 * B1 · Bienvenida (artboards B1b paso 1, B1 paso 2, B1c paso 3). Every step can be
 * skipped; finishing or skipping calls `markWelcomeSeen` so Proyectos stops sending the user here.
 */
export function WelcomeScreen({ initialStep = 1 }: { initialStep?: WelcomeStep }) {
  const { me, workspace } = useSession();
  // The onboarding layout only renders this with a session; the form starts from the loaded workspace.
  if (!me || !workspace) return null;
  return <WelcomeSteps key={workspace.id} me={me} workspace={workspace} initialStep={initialStep} />;
}

function WelcomeSteps({ me, workspace, initialStep }: { me: Me; workspace: WorkspaceSummary; initialStep: WelcomeStep }) {
  const router = useRouter();
  const { can, gate, reason } = usePermissions();
  const { plan } = usePlan();
  const renameWorkspace = useUpdateWorkspace(workspace.id);
  const updateMe = useUpdateMe();
  const [step, setStep] = useState<WelcomeStep>(initialStep);
  const [name, setName] = useState(workspace.name);
  const [nameError, setNameError] = useState<string>();
  const [useCase, setUseCase] = useState<OnboardingUseCase>(me.onboarding_use_case ?? 'podcast');
  const [leaving, setLeaving] = useState<'skip' | 'finish' | null>(null);
  const radios = useRef<Array<HTMLButtonElement | null>>([]);
  const heading = useRef<HTMLHeadingElement | null>(null);
  const shown = useRef(step);

  // A new step announces itself and takes the focus: otherwise it stays on the button that was pressed,
  // and a second Enter would press whatever the next step put in its place.
  useEffect(() => {
    if (shown.current === step) return;
    shown.current = step;
    heading.current?.focus();
  }, [step]);

  const canRename = can('workspace.rename');
  const shownName = name.trim() || workspace.name;
  const chosen = USE_CASES.find((item) => item.value === useCase) ?? (USE_CASES[0] as UseCase);
  const planName = plan?.name ?? workspace.plan?.name ?? null;

  const leave = (kind: 'skip' | 'finish') => {
    setLeaving(kind);
    markWelcomeSeen(me.id);
    if (kind === 'finish') router.push('/proyectos/nuevo');
    else router.replace(HOME);
  };

  const saveName = (event: FormEvent) => {
    event.preventDefault();
    if (renameWorkspace.isPending) return;
    const clean = name.trim();
    if (!canRename || clean === workspace.name) {
      setName(workspace.name);
      setStep(2);
      return;
    }
    if (!clean) {
      setNameError('Ponele un nombre a tu workspace.');
      return;
    }
    renameWorkspace.mutate(clean, {
      onSuccess: () => setStep(2),
      onError: (error) =>
        setNameError(error instanceof ApiError && error.status === 400 ? (fieldError(error, 'name') ?? 'Revisá el nombre y probá de nuevo.') : requestErrorMessage(error)),
    });
  };

  const saveUseCase = () => {
    if (updateMe.isPending) return;
    updateMe.mutate({ onboarding_use_case: useCase }, { onSuccess: () => setStep(3) });
  };

  const onRadioKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const move = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0;
    if (!move) return;
    event.preventDefault();
    const next = (index + move + USE_CASES.length) % USE_CASES.length;
    setUseCase((USE_CASES[next] as UseCase).value);
    radios.current[next]?.focus();
  };

  const skip = (
    <Button variant="ghost" onClick={() => leave('skip')} loading={leaving === 'skip'}>
      Saltear
    </Button>
  );
  const back = (to: WelcomeStep) => (
    <Button variant="ghost" size="sm" iconLeft="arrow-left" onClick={() => setStep(to)}>
      Atrás
    </Button>
  );

  if (step === 1) {
    return (
      <Card className={styles.welcome}>
        <form className={styles.welcomeForm} onSubmit={saveName} noValidate>
        <div className={styles.welcomeBody}>
          <Steps step={1} />
          <div className={styles.title}>
            <h1 className="t-h2" ref={heading} tabIndex={-1}>
              ¿Cómo se llama tu workspace?
            </h1>
            <p className="t-small muted">
              Es el espacio donde viven tus proyectos, tus brand kits y tu saldo de tokens. Puede ser tu nombre, el de tu canal o el de tu equipo.
            </p>
          </div>
          <Field label="Nombre del workspace" hint={canRename ? 'Lo podés cambiar después en Ajustes.' : `Solo un admin puede cambiarlo. ${reason}.`} error={nameError}>
            <Input
              name="workspace"
              autoComplete="organization"
              maxLength={WORKSPACE_NAME_MAX}
              value={name}
              disabled={!canRename}
              onChange={(event) => {
                setName(event.target.value);
                setNameError(undefined);
              }}
            />
          </Field>
          <div className={styles.wsPreview}>
            <Avatar name={shownName} kind="workspace" seed={workspace.id} />
            <span className={styles.wsPreviewText}>
              <span className={styles.wsPreviewName}>{shownName}</span>
              <span className="t-label muted" style={{ fontWeight: 400 }}>
                Así se va a ver arriba, al lado del logo.
              </span>
            </span>
            {planName ? (
              <Badge tone="outline" size="sm">
                {planName}
              </Badge>
            ) : null}
          </div>
        </div>
        <CardFoot className={styles.welcomeFoot}>
          <span />
          <span className={styles.footActions}>
            {skip}
            <Button variant="primary" type="submit" iconRight="chevron-right" loading={renameWorkspace.isPending}>
              Continuar
            </Button>
          </span>
        </CardFoot>
        </form>
      </Card>
    );
  }

  if (step === 2) {
    return (
      <Card className={styles.welcome}>
        <div className={styles.welcomeBody}>
          <Steps step={2} />
          <div className={styles.title}>
            <h1 className="t-h2" id="welcome-use" ref={heading} tabIndex={-1}>
              ¿Para qué lo vas a usar?
            </h1>
            <p className="t-small muted">
              Con esto elegimos el estilo de subtítulos sugerido para tu primer proyecto en {workspace.name}. Lo podés cambiar cuando quieras.
            </p>
          </div>
          {updateMe.isError ? <Banner tone="err">{requestErrorMessage(updateMe.error)}</Banner> : null}
          <div className={styles.uses} role="radiogroup" aria-labelledby="welcome-use">
            {USE_CASES.map((item, index) => {
              const checked = item.value === useCase;
              return (
                <button
                  key={item.value}
                  ref={(node) => {
                    radios.current[index] = node;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={checked}
                  tabIndex={checked ? 0 : -1}
                  className={styles.use}
                  onClick={() => setUseCase(item.value)}
                  onKeyDown={(event) => onRadioKey(event, index)}
                >
                  <span className={styles.useIcon}>{item.icon}</span>
                  <span>
                    <span className={styles.useName}>{item.name}</span>
                    <span className={styles.useDesc}>{item.description}</span>
                  </span>
                  <span className={styles.suggested}>
                    <span className={cx(styles.mini, styles[item.sample])} aria-hidden="true" />
                    <span>
                      Estilo sugerido<span className="sr-only">: </span>
                      <b>{item.style}</b>
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
        <CardFoot className={styles.welcomeFoot}>
          {back(1)}
          <span className={styles.footActions}>
            {skip}
            <Button variant="primary" iconRight="chevron-right" loading={updateMe.isPending} onClick={saveUseCase}>
              Continuar
            </Button>
          </span>
        </CardFoot>
      </Card>
    );
  }

  return (
    <Card className={styles.welcome}>
      <div className={styles.welcomeBody}>
        <Steps step={3} />
        <div className={styles.title}>
          <h1 className="t-h2" ref={heading} tabIndex={-1}>
            Todo listo para tu primer video
          </h1>
          <p className="t-small muted">Esto es lo que elegiste. Tu primer proyecto arranca con esta configuración y la podés cambiar antes de crearlo.</p>
        </div>
        <dl className={styles.summary}>
          <div>
            <dt>Workspace</dt>
            <dd>
              <Avatar name={workspace.name} kind="workspace" seed={workspace.id} />
              <span>
                {workspace.name}
                <span className={styles.note}>
                  {planName ? `Plan ${planName} · ` : null}
                  <Tokens value={workspace.available_balance} />
                </span>
              </span>
            </dd>
            <Button variant="ghost" size="sm" aria-label="Cambiar el nombre del workspace" onClick={() => setStep(1)}>
              Cambiar
            </Button>
          </div>
          <div>
            <dt>Lo vas a usar para</dt>
            <dd>
              <span className={styles.summaryIcon}>{chosen.icon}</span>
              <span>
                {chosen.name}
                <span className={styles.note}>{chosen.description}</span>
              </span>
            </dd>
            <Button variant="ghost" size="sm" aria-label="Cambiar para qué lo vas a usar" onClick={() => setStep(2)}>
              Cambiar
            </Button>
          </div>
          <div>
            <dt>Estilo sugerido</dt>
            <dd>
              <span className={cx(styles.mini, styles[chosen.sample])} aria-hidden="true" />
              <span>
                {chosen.style}
                <span className={styles.note}>Lo podés cambiar al crear el proyecto.</span>
              </span>
            </dd>
            <span />
          </div>
        </dl>
        <ul className={styles.next} aria-label="Qué sigue">
          <li>
            <Icon name="upload" size={14} />
            <span>
              Subí un archivo o pegá un link de YouTube.
              {plan ? (
                <>
                  {' '}
                  Con el plan {plan.name}, hasta <Num>{formatMinutes(plan.max_video_duration_seconds)}</Num> por video.
                </>
              ) : null}
            </span>
          </li>
          <li>
            <Icon name="tokens" size={14} />
            <span>Antes de crear el proyecto ves cuántos tokens cuesta.</span>
          </li>
        </ul>
      </div>
      <CardFoot className={styles.welcomeFoot}>
        {back(2)}
        <span className={styles.footActions}>
          {skip}
          <Button variant="primary" iconLeft="upload" loading={leaving === 'finish'} onClick={() => leave('finish')} {...gate('project.create')}>
            Subí tu primer video
          </Button>
        </span>
      </CardFoot>
    </Card>
  );
}
