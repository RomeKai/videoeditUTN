'use client';

import Link from 'next/link';
import { useState, type FormEvent } from 'react';
import { ApiError } from '@/api/errors';
import type { ScheduledPost, SocialAccount } from '@/api/contracts/social';
import { useUpdatePost } from '@/api/hooks';
import { useBillingDialogs } from '@/dialogs';
import { errorMessage } from '@/lib/error-messages';
import { formatDateTime, formatNumber, formatTimeZone } from '@/lib/format';
import { Badge, Banner, Button, Field, Input, NETWORKS, NetworkChip, Num, Skeleton, Textarea, VideoThumb, cx, useToast } from '@/ui';
import { StatusBadge } from './PostBits';
import type { PostActions } from './PostList';
import styles from './Publicaciones.module.css';
import {
  NETWORK_NOUN,
  accountOf,
  dayKey,
  failureMessage,
  formatHashtags,
  fromInputs,
  isCancellable,
  isEditable,
  isStuck,
  needsReconnect,
  parseHashtags,
  shortDayTime,
  textLength,
  textOrigin,
  timeKey,
  timeZoneLabel,
} from './posts';

const ACCOUNT_STATUS = {
  connected: { label: 'Conectada', tone: 'ok' },
  expired: { label: 'Vencida', tone: 'warn' },
  disconnected: { label: 'Sin conectar', tone: 'neutral' },
} as const;

function EditForm({ post, account, onDone }: { post: ScheduledPost; account?: SocialAccount; onDone: () => void }) {
  const toast = useToast();
  const billing = useBillingDialogs();
  const update = useUpdatePost();
  const scheduled = post.status === 'SCHEDULED';
  const [caption, setCaption] = useState(post.caption);
  const [tags, setTags] = useState(formatHashtags(post.hashtags));
  const [day, setDay] = useState(dayKey(new Date(post.publish_at)));
  const [time, setTime] = useState(timeKey(new Date(post.publish_at)));
  const [errors, setErrors] = useState<{ caption?: string; hashtags?: string; when?: string }>({});

  const hashtags = parseHashtags(tags);
  const limit = account?.limits.caption_max_chars ?? 2200;
  const maxTags = account?.limits.hashtags_max ?? 30;
  const length = textLength(caption, hashtags);
  const over = length - limit;
  const network = NETWORK_NOUN[post.platform];

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    const found: typeof errors = {};
    if (over > 0) found.caption = `Sobran ${formatNumber(over)} caracteres. ${network} no acepta textos de más de ${formatNumber(limit)}, contando los hashtags.`;
    if (hashtags.length > maxTags) found.hashtags = `${network} acepta hasta ${maxTags} hashtags.`;
    const at = scheduled ? fromInputs(day, time) : null;
    if (scheduled && !at) found.when = 'Elegí una fecha y una hora.';
    else if (at && at.getTime() <= Date.now()) found.when = 'Elegí una fecha y una hora que todavía no pasaron.';
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const moved = at && at.getTime() !== new Date(post.publish_at).getTime();
    update.mutate(
      { id: post.id, caption, hashtags, ...(moved && at ? { publish_at: at.toISOString() } : {}) },
      {
        onSuccess: (saved) => {
          toast.success(moved ? `Reprogramada para el ${shortDayTime(saved.publish_at)}` : 'Cambios guardados');
          onDone();
        },
        onError: (error) => {
          if (billing.handleError(error)) return;
          if (error instanceof ApiError && error.status === 400) {
            const fields = error.fieldErrors;
            const mapped = { caption: fields.caption?.[0], hashtags: fields.hashtags?.[0], when: fields.publish_at?.[0] };
            if (mapped.caption || mapped.hashtags || mapped.when) return setErrors(mapped);
          }
          if (error instanceof ApiError && error.isConflict) {
            toast.error('La publicación ya está saliendo: no se puede editar.');
            return onDone();
          }
          toast.error(errorMessage(error, 'No pudimos guardar los cambios.'));
        },
      },
    );
  };

  return (
    <form className={styles.editForm} onSubmit={onSubmit} noValidate aria-label="Editar la publicación">
      {scheduled ? (
        <div>
          <div className={styles.when}>
            <Field label="Fecha" error={errors.when}>
              <Input type="date" className="num" value={day} min={dayKey(new Date())} onChange={(event) => setDay(event.target.value)} data-autofocus />
            </Field>
            <Field label="Hora">
              <Input type="time" className="num" value={time} onChange={(event) => setTime(event.target.value)} />
            </Field>
          </div>
          <p className="t-label subtle" style={{ marginTop: 6 }}>
            Zona horaria: {timeZoneLabel()}, la de tu navegador.
          </p>
        </div>
      ) : null}
      <Field label="Caption" error={errors.caption}>
        <Textarea value={caption} style={{ minHeight: 120 }} onChange={(event) => setCaption(event.target.value)} />
      </Field>
      <Field label="Hashtags" error={errors.hashtags} hint="Separalos con espacios. Cuentan para el límite de caracteres.">
        <Input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="#mendoza #vlog" />
      </Field>
      <div className={styles.countRow}>
        <span>
          <Num>{hashtags.length}</Num> de <Num>{maxTags}</Num> hashtags
        </span>
        <span className={cx('num', over > 0 && styles.countErr)} aria-label={`${formatNumber(length)} de ${formatNumber(limit)} caracteres`}>
          {formatNumber(length)} / {formatNumber(limit)}
        </span>
      </div>
      <div className={styles.detailActions}>
        <Button variant="primary" type="submit" loading={update.isPending}>
          Guardar
        </Button>
        <Button variant="ghost" onClick={onDone} disabled={update.isPending}>
          Cancelar
        </Button>
      </div>
    </form>
  );
}

export interface PostDetailProps {
  post: ScheduledPost | undefined;
  /** The same clip in other networks. */
  siblings?: ScheduledPost[];
  accounts?: SocialAccount[];
  actions: PostActions;
  mode: 'view' | 'edit';
  onModeChange: (mode: 'view' | 'edit') => void;
  /** The post is gone (cancelled somewhere else) or could not be loaded. */
  error?: unknown;
  /** Inside the side panel: no paddings or borders of its own. */
  inDrawer?: boolean;
}

/** E2 · detail of one publication: clip, network, text, date, state, and what can be done with it. */
export function PostDetail({ post, siblings = [], accounts, actions, mode, onModeChange, error, inDrawer = false }: PostDetailProps) {
  if (!post) {
    return (
      <div className={cx(inDrawer && styles.inDrawer)}>
        <div className={styles.detailBody}>
          {error ? (
            <Banner tone={error instanceof ApiError && error.isNotFound ? 'neutral' : 'err'} title={error instanceof ApiError && error.isNotFound ? 'Esta publicación ya no existe' : errorMessage(error, 'No pudimos cargar la publicación')}>
              {error instanceof ApiError && error.isNotFound ? 'Puede que alguien la haya cancelado.' : null}
            </Banner>
          ) : (
            <div aria-busy="true" aria-label="Cargando la publicación">
              <Skeleton height={150} />
              <Skeleton height={120} style={{ marginTop: 20 }} />
            </div>
          )}
        </div>
      </div>
    );
  }

  const account = accountOf(accounts, post.platform);
  const network = NETWORKS[post.platform];
  const noun = NETWORK_NOUN[post.platform];
  const failed = post.status === 'FAILED';
  const reconnect = needsReconnect(post, accounts);
  const stuck = isStuck(post);
  const editable = isEditable(post.status);
  const origin = textOrigin(post);
  const busy = actions.busyId === post.id;
  const accountStatus = account ? ACCOUNT_STATUS[account.status] : null;

  return (
    <div className={cx(inDrawer && styles.inDrawer)}>
      <div className={styles.detailBody}>
        <div className={styles.dclip}>
          <VideoThumb ratio="9:16" seed={post.video_clip} duration={post.clip.duration_seconds} className={styles.dthumb} />
          <div className={styles.dmeta}>
            <StatusBadge status={post.status} />
            <h3 className="t-h3">{post.clip.title}</h3>
            <Link className={`t-small muted ${styles.ulink}`} href={`/proyectos/${post.clip.project_id}/clips`}>
              {post.clip.project_title}
            </Link>
          </div>
        </div>

        {failed ? (
          <Banner tone="err" live="alert" title={failureMessage(post, accounts)}>
            Falló el <Num>{formatDateTime(post.updated_at)}</Num>. El clip no se publicó.
          </Banner>
        ) : null}
        {stuck ? (
          <Banner tone="warn" title="Está tardando más de lo normal">
            Si sigue así, podés reintentar la publicación.
          </Banner>
        ) : null}
        {post.status === 'DRAFT' ? (
          <Banner tone="neutral" title="Es un borrador">
            Todavía no tiene fecha. Programalo o publicalo cuando el texto esté listo.
          </Banner>
        ) : null}

        {mode === 'edit' && editable ? (
          <EditForm post={post} account={account} onDone={() => onModeChange('view')} />
        ) : (
          <>
            <dl className={styles.dl}>
              <div>
                <dt>Red</dt>
                <dd>
                  <NetworkChip platform={post.platform} size="sm" />
                </dd>
              </div>
              <div>
                <dt>Cuenta</dt>
                <dd>
                  {account?.username ?? 'Sin cuenta'}
                  {accountStatus && account?.status !== 'connected' ? (
                    <Badge tone={accountStatus.tone} size="sm" dot>
                      {accountStatus.label}
                    </Badge>
                  ) : null}
                </dd>
              </div>
              <div>
                <dt>{post.status === 'PUBLISHED' ? 'Publicado' : 'Fecha'}</dt>
                <dd>
                  {post.status === 'DRAFT' ? (
                    <span className="subtle">Sin fecha</span>
                  ) : (
                    <>
                      <Num>{formatDateTime(post.publish_at)}</Num> <span className="subtle num">{formatTimeZone(new Date(post.publish_at))}</span>
                    </>
                  )}
                </dd>
              </div>
            </dl>

            <div className={styles.dtext}>
              <div className={styles.dtextHead}>
                <span className="t-label">Texto</span>
                {post.seo_status === 'pending' ? (
                  <Badge tone="outline" size="sm" icon="sparkles">
                    Generando texto…
                  </Badge>
                ) : origin === 'ai' ? (
                  <Badge tone="outline" size="sm" icon="sparkles">
                    Sugerido por IA
                  </Badge>
                ) : origin === 'edited' ? (
                  <Badge tone="outline" size="sm">
                    Editado por vos
                  </Badge>
                ) : null}
              </div>
              {post.caption ? (
                <p className={styles.dcaption}>{post.caption}</p>
              ) : post.seo_status === 'pending' ? (
                <div aria-busy="true" aria-label="Generando texto">
                  <Skeleton width="92%" height={10} />
                  <Skeleton width="64%" height={10} style={{ marginTop: 6 }} />
                </div>
              ) : (
                <p className="subtle">Sin texto.</p>
              )}
              {post.hashtags.length > 0 ? <p className="t-small muted">{formatHashtags(post.hashtags)}</p> : null}
              {post.caption || post.hashtags.length > 0 ? (
                <p className="t-label subtle num">
                  {formatNumber(textLength(post.caption, post.hashtags))} / {formatNumber(account?.limits.caption_max_chars ?? 2200)} caracteres
                </p>
              ) : null}
            </div>

            {siblings.length > 0 ? (
              <div className={styles.siblings}>
                <span className="t-label">El mismo clip en otras redes</span>
                {siblings.map((sibling) => (
                  <button key={sibling.id} type="button" className={styles.sibling} onClick={() => actions.open(sibling)}>
                    <NetworkChip platform={sibling.platform} size="sm" />
                    <StatusBadge status={sibling.status} size="sm" />
                  </button>
                ))}
              </div>
            ) : null}
          </>
        )}
      </div>

      {mode === 'edit' && editable ? null : (
        <div className={styles.detailFoot}>
          <div className={styles.detailActions}>
            {post.status === 'PUBLISHED' && post.post_url ? (
              <Button variant="primary" href={post.post_url} external iconRight="external">
                Ver publicación
              </Button>
            ) : null}
            {post.status === 'DRAFT' ? (
              <Button variant="primary" iconLeft="send" onClick={() => actions.publish(post.video_clip)} {...actions.publishGate}>
                Publicar o programar
              </Button>
            ) : null}
            {editable ? (
              <Button iconLeft="edit" onClick={() => onModeChange('edit')} {...actions.publishGate}>
                {post.status === 'SCHEDULED' ? 'Editar y reprogramar' : 'Editar texto'}
              </Button>
            ) : null}
            {failed && reconnect ? (
              <Button variant="primary" iconLeft="link" onClick={() => actions.reconnect(post.platform)} {...actions.connectGate}>
                Reconectar
              </Button>
            ) : null}
            {failed || stuck ? (
              <Button
                variant={failed && !reconnect ? 'primary' : 'secondary'}
                iconLeft="retry"
                loading={busy}
                disabled={reconnect}
                onClick={() => actions.retry(post)}
                {...(reconnect ? {} : actions.publishGate)}
              >
                Reintentar
              </Button>
            ) : null}
          </div>
          {failed && reconnect ? (
            <p className="t-small subtle">Reintentar se habilita cuando reconectes la cuenta de {noun}. Editar y Reprogramar ya no están: pasó la hora de salida.</p>
          ) : null}
          {post.status === 'QUEUED' || post.status === 'PROCESSING' ? (
            <p className="t-small subtle">
              {post.status === 'QUEUED' ? `Está en la cola de ${network.shortName}. ` : `Se está publicando en ${network.shortName}. `}
              Esta pantalla se actualiza sola.
            </p>
          ) : null}
          {isCancellable(post.status) ? (
            <Button variant="ghost" size="sm" className={styles.cancelBtn} onClick={() => actions.askCancel(post)} {...actions.publishGate}>
              {post.status === 'DRAFT' ? 'Borrar el borrador' : 'Cancelar publicación'}
            </Button>
          ) : null}
        </div>
      )}
    </div>
  );
}
