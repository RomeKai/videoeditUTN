'use client';

import Link from 'next/link';
import { useState, type ReactNode } from 'react';
import { useBrandKit } from '@/api/hooks';
import { LABELS } from '@/lib/project-status';
import { usePermissions } from '@/shell/session';
import { Badge, Banner, Button, Card, CardHead, Duration, EmptyState, Icon, Spinner, StatusDot, Tokens, Tooltip, VideoPlayer, VideoThumb, type IconName } from '@/ui';
import { RESOLUTION_PIXELS, clipDuration, describeClip, ratioLabel, subtitlesSummary } from './model';
import { useCurrentProject } from './project-context';
import shell from './ProjectShell.module.css';
import { RerenderDialog, useRerenderEstimate } from './RerenderDialog';
import styles from './ResultScreen.module.css';
import { recreateHref, usePublish } from './useClipActions';

interface ActionRowProps {
  icon: IconName;
  title: string;
  description: string;
  href?: string;
  onClick?: () => void;
  /** Right side: the cost of the action. */
  aside?: ReactNode;
  disabledReason?: string;
}

function ActionRow({ icon, title, description, href, onClick, aside, disabledReason }: ActionRowProps) {
  const content = (
    <>
      <span className={styles.actionIcon} aria-hidden="true">
        <Icon name={icon} />
      </span>
      <span className={styles.actionBody}>
        <span className={styles.actionTitle}>{title}</span>
        <span className={styles.actionDesc}>{description}</span>
      </span>
      {aside}
      <Icon name="chevron-right" className={styles.actionChevron} />
    </>
  );
  if (disabledReason) {
    return (
      <Tooltip content={disabledReason} className={styles.actionTip}>
        <button type="button" className={`${styles.action} ${styles.actionDisabled}`} aria-disabled="true">
          {content}
        </button>
      </Tooltip>
    );
  }
  return href ? (
    <Link href={href} className={styles.action}>
      {content}
    </Link>
  ) : (
    <button type="button" className={styles.action} onClick={onClick}>
      {content}
    </button>
  );
}

/** D5 · Resultado: the single video of a whole-video project, its details and what to do with it. */
export function ResultScreen({ projectId }: { projectId: string }) {
  const project = useCurrentProject();
  const { gate } = usePermissions();
  const publish = usePublish();
  const kit = useBrandKit(project?.brand_kit);
  const [rerendering, setRerendering] = useState(false);
  const clip = project?.clips[0];
  const duration = clip ? clipDuration(clip) : 0;
  const { estimate } = useRerenderEstimate(project ?? { metadata: {} }, duration);

  if (!project) return null;
  const base = `/proyectos/${projectId}`;

  if (!clip) {
    return project.status === 'failed' ? (
      <EmptyState
        icon="video"
        title="Este proyecto no llegó a generar el video"
        description="El proceso falló antes de editarlo."
        action={
          <Button variant="secondary" href={base}>
            Ver qué pasó
          </Button>
        }
      />
    ) : (
      <EmptyState
        icon="video"
        title="Estamos preparando tu video"
        description="No hay revisión ni vista previa: el video final aparece acá apenas termina."
        action={
          <Button variant="secondary" href={base}>
            Ver el proceso
          </Button>
        }
      />
    );
  }

  const display = describeClip(clip, project.status);
  const resolution = project.metadata.render_resolution ?? '720p';
  const pixels = RESOLUTION_PIXELS[resolution]?.[project.aspect_ratio];
  const swatches = kit.data ? [kit.data.primary_color, kit.data.secondary_color, kit.data.accent_color].filter((color): color is string => Boolean(color)) : [];
  const noVoice = display.ready && project.add_subtitles && (project.metadata.no_speech === true || (project.transcript_data !== null && project.transcript_data.length === 0));
  const notReady = display.ready ? undefined : display.group === 'failed' ? 'El video no se pudo generar' : 'Disponible cuando el video esté listo';
  const allowed = (action: 'post.publish' | 'clip.edit' | 'project.review') => gate(action).disabledReason;

  return (
    <>
      {display.group === 'failed' ? (
        // The backend never runs a refunded whole video again: the way forward is a new project, or the paid re-render below.
        <Banner
          tone="err"
          className={styles.notice}
          title="El video no se pudo generar"
          action={
            <Button size="sm" variant="secondary" iconLeft="retry" href={recreateHref(project.id)} {...gate('project.create')}>
              Crear de nuevo
            </Button>
          }
        >
          {project.cost?.state === 'refunded' ? (
            <>
              Te devolvimos <Tokens value={project.cost.amount} />.{' '}
            </>
          ) : null}
          Podés crear el proyecto de nuevo o volver a renderizarlo acá abajo: te mostramos el costo antes de confirmar.
        </Banner>
      ) : null}
      <div className={styles.result}>
        <Card as="section" aria-label="Video editado" className={styles.playerCard}>
          {display.ready ? (
            <div className={styles.stage}>
              <div className={styles.tags} aria-hidden="true">
                <Badge size="sm" className={styles.tag}>
                  {project.aspect_ratio}
                </Badge>
                <Badge size="sm" className={styles.tag}>
                  {resolution}
                </Badge>
              </div>
              <VideoPlayer
                className={styles.player}
                src={clip.preview_url}
                seed={clip.id}
                ratio={project.aspect_ratio}
                duration={duration}
                captionColor={project.add_subtitles ? project.subtitle_color : undefined}
                label={`Reproductor: ${project.title}`}
              />
            </div>
          ) : (
            <div className={styles.stage} aria-busy={display.group === 'rendering' ? true : undefined}>
              <VideoThumb ratio={project.aspect_ratio} seed={clip.id} captions={false} className={styles.pending}>
                {display.group === 'rendering' ? (
                  <span className={styles.wait}>
                    <Spinner size={16} />
                    Estamos editando tu video…
                  </span>
                ) : (
                  <span className={styles.wait}>
                    <Icon name="error" size={20} />
                    No se pudo generar
                  </span>
                )}
              </VideoThumb>
            </div>
          )}
          {display.group === 'rendering' ? (
            <p className={`${styles.live} t-small muted`}>
              <StatusDot tone="info" live />
              <span className="num">Se actualiza cada 5 s. No hay vista previa: el video final sale enseguida.</span>
            </p>
          ) : null}
        </Card>

        <div className={styles.side}>
          <Banner tone="neutral" live="off">
            Este proyecto no tiene revisión: editamos el video completo.
          </Banner>

          <Card as="section" aria-labelledby="detalles-h">
            <CardHead>
              <h2 className="t-h3" id="detalles-h">
                Detalles
              </h2>
              <span className="t-small muted">Video completo</span>
            </CardHead>
            <dl className={styles.details}>
              <div>
                <dt>Duración</dt>
                <dd>
                  <Duration seconds={duration} />
                </dd>
              </div>
              <div>
                <dt>Formato</dt>
                <dd className="num">{ratioLabel(project.aspect_ratio)}</dd>
              </div>
              <div>
                <dt>Resolución</dt>
                <dd className="num">
                  {resolution}
                  {pixels ? <span className="muted">{pixels}</span> : null}
                </dd>
              </div>
              <div>
                <dt>Estilo</dt>
                <dd>{LABELS.editing_style[project.editing_style]}</dd>
              </div>
              <div>
                <dt>Brand kit</dt>
                <dd>
                  {project.brand_kit ? (
                    <>
                      {swatches.length > 0 ? (
                        <span className={styles.swatches} aria-hidden="true">
                          {swatches.map((color, index) => (
                            <span key={index} style={{ background: color }} />
                          ))}
                        </span>
                      ) : null}
                      <Link className={shell.link} href={`/brand-kits/${project.brand_kit}`}>
                        {project.brand_kit_name ?? 'Brand kit'}
                      </Link>
                    </>
                  ) : (
                    'Sin brand kit'
                  )}
                </dd>
              </div>
              <div>
                <dt>Subtítulos</dt>
                <dd>
                  {project.add_subtitles ? 'Activados' : 'Desactivados'}
                  {project.add_subtitles ? <small className="num">{subtitlesSummary(project)}</small> : null}
                  {noVoice ? <small>No encontramos voz en el video: se editó igual, sin subtítulos.</small> : null}
                </dd>
              </div>
            </dl>
          </Card>

          <Card as="section" aria-labelledby="acciones-h">
            <CardHead>
              <h2 className="t-h3" id="acciones-h">
                Qué podés hacer
              </h2>
            </CardHead>
            <div className={styles.actions}>
              <ActionRow
                icon="send"
                title="Publicar"
                description="En TikTok, Instagram Reels o YouTube Shorts, ahora o programado."
                onClick={() => publish([clip.id])}
                disabledReason={notReady ?? allowed('post.publish')}
              />
              <ActionRow
                icon="edit"
                title="Ajustar recorte"
                description="Cambiá el inicio, el fin o el título."
                href={`${base}/clips/${clip.id}`}
                disabledReason={display.group === 'rendering' ? notReady : allowed('clip.edit')}
              />
              <ActionRow
                icon="retry"
                title="Volver a renderizar con otro estilo"
                description="Te mostramos el costo antes de confirmar."
                onClick={() => setRerendering(true)}
                aside={estimate ? <Tokens value={estimate.total} className={styles.actionCost} /> : null}
                disabledReason={display.group === 'rendering' ? notReady : allowed('project.review')}
              />
            </div>
          </Card>
        </div>
      </div>
      <RerenderDialog open={rerendering} onClose={() => setRerendering(false)} project={project} durationSeconds={duration} />
    </>
  );
}
