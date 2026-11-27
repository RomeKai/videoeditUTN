'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import type { Project } from '@/api/contracts/projects';
import { useBrandKit, useProjectActivity, useWallet } from '@/api/hooks';
import { isModerationError, pipelineErrorMessage } from '@/lib/error-messages';
import { formatDuration, formatTime, plural } from '@/lib/format';
import { useIsMobile } from '@/lib/hooks';
import { countClips } from '@/lib/project-status';
import { usePermissions } from '@/shell/session';
import { Badge, Banner, Button, Card, CardFoot, CardHead, ClipCard, Icon, StageStepper, StatusDot, StepSegments, Tokens, type StageStep } from '@/ui';
import {
  autoRenderNotice,
  buildConfigGroups,
  buildProcessSteps,
  clipDuration,
  configSummary,
  describeClip,
  describeProjectStatus,
  failedStepNumber,
  isWholeVideo,
  pteAppliedLines,
  pteIgnoredLines,
  refundAfterFailure,
  shortDateTime,
  sortClips,
  totalProcessSeconds,
  type ConfigGroup,
  type PteLine,
} from './model';
import { useCurrentProject } from './project-context';
import shell from './ProjectShell.module.css';
import styles from './SummaryScreen.module.css';
import { recreateHref } from './useClipActions';

const SUPPORT_MAIL = 'mailto:soporte@onecreator.app';

/** Extra advice under the error of map §4, where the design has one. */
const ERROR_HINT: Record<string, string> = {
  AINoSpeechDetectedError: 'Probá con un video donde se hable, o subí uno con mejor audio.',
};

/** Re-renders every second while something runs, so the running step's duration moves. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  return now;
}

function ProcessSteps({ project, now }: { project: Project; now: number }) {
  const steps = buildProcessSteps(project, now);
  const single = isWholeVideo(project);
  const rows: StageStep[] = steps.map((step) => ({
    key: step.collapsed ? 'rest' : step.key,
    state: step.state,
    label: (
      <span className={styles.stepName}>
        {step.label}
        {step.gated ? (
          <Badge tone="outline" size="sm">
            Necesita tu revisión
          </Badge>
        ) : null}
      </span>
    ),
    detail: step.description,
    separatorBefore: step.key === 'render' && !step.collapsed && !single && (!project.auto_render_bypass || project.status === 'awaiting_approval') ? 'Después de tu revisión' : undefined,
    duration: (
      <span className={styles.stepMeta}>
        <span className={styles[`st_${step.state}`]}>{step.stateLabel}</span>
        <span className={styles.colStart}>{step.startedAt ? formatTime(step.startedAt, { seconds: true }) : '—'}</span>
        <span className={styles.colDuration}>{step.durationSeconds !== null ? formatDuration(step.durationSeconds) : '—'}</span>
      </span>
    ),
  }));
  return (
    <>
      <div className={styles.colHead} aria-hidden="true">
        <span />
        <span>Etapa</span>
        <span className={styles.stepMeta}>
          <span>Estado</span>
          <span className={styles.colStart}>Inicio</span>
          <span className={styles.colDuration}>Duración</span>
        </span>
      </div>
      <StageStepper steps={rows} ariaLabel="Etapas del proceso" />
    </>
  );
}

function ProcessCard({ project, folded }: { project: Project; folded: boolean }) {
  const view = describeProjectStatus(project);
  const now = useNow(view.inProgress);
  const failedAt = project.status === 'failed' ? failedStepNumber(buildProcessSteps(project, now)) : null;
  const total = totalProcessSeconds(project);

  if (folded) {
    return (
      <Card as="details" className={styles.fold}>
        <summary>
          <Icon name="chevron-right" className={styles.chev} />
          <h2 className="t-h3">Proceso</h2>
          {total ? (
            <span className="t-small muted">
              Listo en <span className={`num ${styles.strong}`}>{formatDuration(total)}</span>
            </span>
          ) : null}
          <span className={styles.foldCta}>Ver pasos</span>
        </summary>
        <ProcessSteps project={project} now={now} />
      </Card>
    );
  }
  const waitingReview = project.status === 'awaiting_approval';
  return (
    <Card as="section" aria-labelledby="proceso-h">
      <CardHead>
        <h2 className="t-h3" id="proceso-h">
          Proceso
        </h2>
        <span className={styles.headRight}>
          {view.step ? (
            <span className={styles.row}>
              <span className="t-small num">{view.text}</span>
              <StepSegments done={view.step.current - 1} />
            </span>
          ) : null}
          {view.inProgress ? (
            <span className={`${styles.row} t-small muted`}>
              <StatusDot tone="info" live />
              <span className="num">Se actualiza cada 5 s</span>
            </span>
          ) : null}
          {failedAt ? <span className="t-small muted num">Se detuvo en el paso {failedAt}</span> : null}
          {waitingReview ? <span className="t-small muted">Falta tu revisión</span> : null}
        </span>
      </CardHead>
      <ProcessSteps project={project} now={now} />
      {view.inProgress ? (
        <CardFoot>
          <span className="t-small muted">
            {project.status === 'rendering' || isWholeVideo(project)
              ? 'Podés cerrar esta pantalla. Te avisamos por email y en la campana cuando termine.'
              : 'Podés cerrar esta pantalla. Te avisamos por email y en la campana cuando esté listo para revisar.'}
          </span>
        </CardFoot>
      ) : null}
    </Card>
  );
}

function ConfigGroups({ groups, wide, swatches }: { groups: ConfigGroup[]; wide: boolean; swatches: string[] }) {
  return (
    <div className={wide ? `${styles.cfgGroups} ${styles.cfgWide}` : styles.cfgGroups}>
      {groups.map((group) => (
        <div key={group.title} className={styles.cfgGroup}>
          <h3 className="t-over">{group.title}</h3>
          <dl className={styles.cfg}>
            {group.items.map((item) => (
              <div key={item.key} className={item.wide ? styles.span2 : undefined}>
                <dt>{item.label}</dt>
                <dd className="num">
                  {item.key === 'kit' && swatches.length > 0 ? (
                    <span className={styles.swatches} aria-hidden="true">
                      {swatches.map((color, index) => (
                        <span key={index} style={{ background: color }} />
                      ))}
                    </span>
                  ) : null}
                  {item.value}
                </dd>
              </div>
            ))}
          </dl>
        </div>
      ))}
    </div>
  );
}

function PteList({ lines, applied }: { lines: PteLine[]; applied: boolean }) {
  return (
    <ul className={applied ? styles.pteList : `${styles.pteList} ${styles.pteListOne}`}>
      {lines.map((line) => (
        <li key={line.key}>
          <span className={`${styles.pteIcon} ${applied ? styles.pteYes : styles.pteNo}`} aria-hidden="true">
            <Icon name={applied ? 'check' : 'minus'} size={12} />
          </span>
          <span className={styles.pteText}>
            <span className={`${styles.strongText} num`}>{line.title}</span>
            {line.detail ? <span className="t-small muted">{line.detail}</span> : null}
          </span>
        </li>
      ))}
    </ul>
  );
}

function ConfigCard({ project, wide }: { project: Project; wide: boolean }) {
  const isMobile = useIsMobile();
  const kit = useBrandKit(project.brand_kit);
  const swatches = kit.data ? [kit.data.primary_color, kit.data.secondary_color, kit.data.accent_color].filter((color): color is string => Boolean(color)) : [];
  const groups = buildConfigGroups(project);
  const prompt = project.metadata.pte_prompt;
  const pte = project.metadata.pte;
  const criteria = project.metadata.selection_criteria;
  const allGroups = <ConfigGroups groups={groups} wide={wide && !isMobile} swatches={swatches} />;

  let body;
  if (prompt) {
    const applied = pte?.status === 'applied' ? pteAppliedLines(pte.applied) : [];
    const ignored = pte?.status === 'applied' ? pteIgnoredLines(pte.ignored) : [];
    body = (
      <>
        <div className={styles.quote}>
          <span className="t-label subtle">Lo que pediste</span>
          <p className={styles.pteQuote}>«{prompt}»</p>
        </div>
        {!pte ? (
          <p className={`${styles.pteNote} t-small muted`}>La IA arma la configuración al empezar a procesar. Hasta entonces vale lo que elegiste a mano.</p>
        ) : pte.status === 'failed' ? (
          <div className={styles.pteNote}>
            <Banner tone="warn" title="No pudimos interpretar lo que pediste" live="off">
              Usamos la configuración que elegiste a mano. No cambia el costo.
            </Banner>
          </div>
        ) : pte.status === 'empty' ? (
          <p className={`${styles.pteNote} t-small muted`}>El pedido estaba vacío: usamos la configuración que elegiste a mano.</p>
        ) : (
          <div className={styles.pteCols}>
            <div className={styles.pteCol}>
              <h3 className="t-label subtle">Lo que aplicamos</h3>
              {applied.length > 0 ? <PteList lines={applied} applied /> : <p className="t-small muted">No hubo cambios sobre lo que elegiste a mano.</p>}
            </div>
            <div className={styles.pteCol}>
              <h3 className="t-label subtle">Lo que no pudimos aplicar</h3>
              {ignored.length > 0 ? <PteList lines={ignored} applied={false} /> : <p className="t-small muted">Aplicamos todo lo que pediste.</p>}
            </div>
          </div>
        )}
        <details className={styles.inner}>
          <summary>
            <Icon name="chevron-right" size={14} className={styles.chev} />
            <span>Ver toda la configuración</span>
          </summary>
          {allGroups}
        </details>
      </>
    );
  } else {
    const quote =
      isWholeVideo(project) ? null : (
        <div className={styles.quote}>
          <span className="t-label subtle">¿Qué buscás?</span>
          <p>{criteria ? `«${criteria}»` : 'Sin criterio: la IA elige los momentos.'}</p>
        </div>
      );
    body = isMobile ? (
      <details className={styles.inner}>
        <summary>
          <Icon name="chevron-right" size={14} className={styles.chev} />
          <span className="num">{configSummary(project)}</span>
        </summary>
        {quote}
        {allGroups}
      </details>
    ) : (
      <>
        {quote}
        {allGroups}
      </>
    );
  }

  return (
    <Card as="section" aria-labelledby="config-h">
      <CardHead>
        <h2 className="t-h3" id="config-h">
          Configuración aplicada
        </h2>
        {prompt ? (
          <Badge tone="outline" icon="sparkles">
            Creado con IA
          </Badge>
        ) : (
          <span className="t-small muted">Elegida a mano</span>
        )}
      </CardHead>
      {body}
    </Card>
  );
}

function ClipStrip({ project }: { project: Project }) {
  const drafts = project.status === 'awaiting_approval';
  const href = drafts ? `/proyectos/${project.id}/revision` : `/proyectos/${project.id}/clips`;
  return (
    <Card as="section" aria-labelledby="clips-h">
      <CardHead>
        <h2 className="t-h3" id="clips-h">
          {drafts ? 'Clips sugeridos' : 'Clips'}
        </h2>
        <span className="t-small muted">Ordenados por puntaje</span>
      </CardHead>
      <ul className={styles.strip}>
        {sortClips(project.clips).map((clip) => {
          const display = describeClip(clip, project.status);
          return (
            <li key={clip.id}>
              <Link href={href} className={styles.stripLink} aria-label={`${clip.title}: ${display.label}`}>
                <ClipCard
                  size="sm"
                  seed={clip.id}
                  title={clip.title}
                  ratio={project.aspect_ratio}
                  thumbnailUrl={clip.thumbnail_url}
                  duration={clipDuration(clip)}
                  score={clip.virality_score}
                  captionColor={project.add_subtitles ? project.subtitle_color : undefined}
                  state={display.group === 'rendering' ? 'rendering' : display.group === 'failed' ? 'failed' : 'ready'}
                  meta={
                    <>
                      <Badge tone={display.tone} size="sm" dot={!display.icon} icon={display.icon}>
                        {display.label}
                      </Badge>
                      {display.where ? (
                        <span className={`${styles.stripWhere} num`}>
                          {NETWORK_SHORT[display.where.platform]} · {shortDateTime(display.where.publishAt)}
                        </span>
                      ) : null}
                    </>
                  }
                />
              </Link>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

const NETWORK_SHORT = { TIKTOK: 'TikTok', INSTAGRAM_REELS: 'Instagram', YOUTUBE_SHORTS: 'YouTube' } as const;

/** How many times the activity is asked again while the refund of a failed project is not in it yet. */
const REFUND_RETRIES = 5;
const REFUND_RETRY_MS = 3000;

/** Tokens that came back when the project failed: from `cost` when the backend sends it, from the activity otherwise. */
function useRefunded(project: Project): number {
  const fromActivity = !project.cost;
  const activity = useProjectActivity(fromActivity ? project.id : null);
  const { refetch } = activity;
  const refunded = fromActivity && activity.data ? refundAfterFailure(activity.data) : 0;
  // The activity may be in the cache from before the failure, and the refund is written just after it.
  const failedAt = project.pipeline_error_at;
  const tries = useRef(0);
  useEffect(() => {
    tries.current = 0;
    if (fromActivity) void refetch();
  }, [fromActivity, failedAt, refetch]);
  const waiting = fromActivity && refunded === null;
  const { dataUpdatedAt } = activity;
  useEffect(() => {
    if (!waiting || tries.current >= REFUND_RETRIES) return;
    const id = window.setTimeout(() => {
      tries.current += 1;
      void refetch();
    }, REFUND_RETRY_MS);
    return () => window.clearTimeout(id);
  }, [waiting, dataUpdatedAt, refetch]);
  if (!fromActivity) return project.cost?.state === 'refunded' ? project.cost.amount : 0;
  return refunded ?? 0;
}

function FailedBanner({ project }: { project: Project }) {
  const { gate, can } = usePermissions();
  const code = project.pipeline_error_code;
  // Money is only mentioned when it is known. The backend does not send `cost` yet (gap 4): without it,
  // the refund is read from the project's activity, which the backend does have.
  const refunded = useRefunded(project);
  // Re-dispatched without tokens for what was left: the clips are still there, so there is nothing to create again.
  const unpaid = code === 'AutoRenderNotPaid';
  return (
    <Banner
      tone="err"
      className={styles.notice}
      title={pipelineErrorMessage(code)}
      action={
        unpaid ? (
          <div className={styles.bannerActions}>
            <Button variant="primary" iconLeft="tokens" href="/uso/comprar" {...gate('billing.manage')}>
              Comprar tokens
            </Button>
            {project.clips.length > 0 ? (
              <Button variant="secondary" href={`/proyectos/${project.id}/clips`}>
                Ver clips
              </Button>
            ) : null}
          </div>
        ) : (
          <div className={styles.bannerActions}>
            <Button variant="primary" iconLeft="retry" href={recreateHref(project.id)} {...gate('project.create')}>
              Crear de nuevo
            </Button>
            <span className="t-label muted">Con la misma configuración</span>
          </div>
        )
      }
    >
      {ERROR_HINT[code] ? <p>{ERROR_HINT[code]}</p> : null}
      <p className={styles.refund}>
        {refunded > 0 ? (
          <span>
            Te devolvimos <Tokens value={refunded} className={styles.strong} />.
          </span>
        ) : null}
        {refunded > 0 && can('billing.manage') ? (
          <Link className={styles.arrowLink} href="/uso/movimientos">
            Ver en Movimientos <Icon name="chevron-right" size={14} />
          </Link>
        ) : null}
        <a className={styles.arrowLink} href={SUPPORT_MAIL}>
          {isModerationError(code) ? 'Escribinos' : 'Escribir a soporte'} <Icon name="mail" size={14} />
        </a>
      </p>
    </Banner>
  );
}

function Notices({ project }: { project: Project }) {
  const { can } = usePermissions();
  const wallet = useWallet();
  const notice = autoRenderNotice(project, wallet.data ? wallet.data.reservations.map((item) => item.transaction_id) : null);
  const clips = countClips(project.clips);
  const whole = isWholeVideo(project);
  return (
    <>
      {notice && notice.kind === 'reserved' ? (
        <Banner tone="info" className={styles.notice} title={notice.title}>
          {notice.text}
        </Banner>
      ) : null}
      {notice && notice.kind !== 'reserved' ? (
        <Banner tone="warn" className={styles.notice} title={notice.title}>
          {notice.text}{' '}
          {can('billing.manage') ? (
            <Link className={shell.link} href={notice.kind === 'insufficient_funds' ? '/uso/comprar' : '/uso/planes'}>
              {notice.kind === 'insufficient_funds' ? 'Comprar tokens' : 'Ver planes'}
            </Link>
          ) : null}
        </Banner>
      ) : null}
      {project.status === 'awaiting_approval' && !notice ? (
        <Banner
          tone="neutral"
          className={styles.notice}
          title={clips.total > 0 ? `Encontramos ${plural(clips.total, 'clip', 'clips')}. Falta tu revisión.` : 'No encontramos momentos para sugerir'}
        >
          {clips.total > 0
            ? 'Elegí cuáles renderizar y ajustá el recorte. El render no arranca solo y te mostramos el costo antes de confirmar.'
            : 'Podés elegir los momentos vos desde la transcripción, en la revisión.'}
        </Banner>
      ) : null}
      {project.status === 'partial' ? (
        <Banner tone="warn" className={styles.notice} title={describeProjectStatus(project).text}>
          {whole ? (
            <>
              {project.cost?.state === 'refunded' ? (
                <>
                  Te devolvimos <Tokens value={project.cost.amount} />.{' '}
                </>
              ) : null}
              Este video no se puede reintentar: creá el proyecto de nuevo o volvé a renderizarlo desde Resultado.
            </>
          ) : (
            'Los clips que fallaron no se cobraron. Podés reintentarlos desde Clips: cada reintento cuesta el render de ese clip.'
          )}
        </Banner>
      ) : null}
    </>
  );
}

/** D1 · Resumen: process timeline, applied configuration and, once there are clips, the strip that leads to them. */
export function SummaryScreen() {
  const project = useCurrentProject();
  if (!project) return null;
  const failed = project.status === 'failed';
  const hasResults = ['awaiting_approval', 'rendering', 'completed', 'partial'].includes(project.status);
  const final = project.status === 'completed' || project.status === 'partial';

  if (hasResults) {
    return (
      <div className={styles.stack}>
        <Notices project={project} />
        {!isWholeVideo(project) && project.clips.length > 0 ? <ClipStrip project={project} /> : null}
        <ProcessCard project={project} folded={final} />
        <ConfigCard project={project} wide />
      </div>
    );
  }
  return (
    <>
      {failed ? <FailedBanner project={project} /> : <Notices project={project} />}
      <div className={styles.cols}>
        <div className={styles.main}>
          <ProcessCard project={project} folded={false} />
        </div>
        <div className={styles.side}>
          <ConfigCard project={project} wide={false} />
        </div>
      </div>
    </>
  );
}
