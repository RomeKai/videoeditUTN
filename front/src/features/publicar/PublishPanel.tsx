'use client';

import { useId, useMemo, useRef, useState } from 'react';
import type { Platform } from '@/api/contracts/common';
import type { ScheduledPost, TikTokPrivacy } from '@/api/contracts/social';
import { ApiError } from '@/api/errors';
import { seoPending, usePlans, usePostActions, usePosts, useSocialAccounts } from '@/api/hooks';
import { ConnectNetworkDialog, useBillingDialogs } from '@/dialogs';
import { errorMessage } from '@/lib/error-messages';
import { formatDateTime, formatDuration, formatNumber, formatTime, plural } from '@/lib/format';
import { useIsMobile } from '@/lib/hooks';
import { usePermissions, usePlan, useSession } from '@/shell/session';
import {
  ActionBar,
  Avatar,
  Badge,
  Banner,
  Button,
  Checkbox,
  Drawer,
  Field,
  Icon,
  IconButton,
  Input,
  NETWORKS,
  NetworkChip,
  Num,
  PlanLock,
  SegmentedControl,
  Select,
  Skeleton,
  Spinner,
  Switch,
  VideoThumb,
  cx,
  useToast,
} from '@/ui';
import styles from './Publish.module.css';
import { TextEditor, type TextOrigin } from './TextEditor';
import { NETWORK_NOUN, PLATFORMS, PRIVACY_LABEL, accountOf, addDays, dayKey, formatHashtags, fromInputs, shortDay, textLength, timeZoneLabel } from './posts';
import {
  EMPTY_TIKTOK,
  buildSubmission,
  checkNetwork,
  findClashes,
  joinNetworks,
  outcomeSummary,
  pairKey,
  scheduleFor,
  scheduleSummary,
  tiktokOptions,
  type NetworkCheck,
  type PairFailure,
  type PairKey,
  type PanelClip,
  type SpreadMode,
  type TextValue,
  type TikTokForm,
  type WhenMode,
} from './publish';
import { usePanelClips } from './usePanelClips';
import { usePanelDrafts, type PanelPair } from './usePanelDrafts';

const EMPTY_TEXT: TextValue = { caption: '', hashtags: [] };
/** Statuses of a publication that is on its way out or already out. */
const WENT_OUT = new Set<ScheduledPost['status']>(['SCHEDULED', 'QUEUED', 'PROCESSING', 'PUBLISHED']);
const ACCOUNT_BADGE = {
  connected: { label: 'Conectada', tone: 'ok' },
  expired: { label: 'Vencida', tone: 'warn' },
  disconnected: { label: 'Sin conectar', tone: 'neutral' },
} as const;
const TIKTOK_MUSIC_URL = 'https://www.tiktok.com/legal/page/global/music-usage-confirmation/es';
const TIKTOK_BRANDED_URL = 'https://www.tiktok.com/legal/page/global/bc-policy/es';

export interface PublishPanelProps {
  /** Clips to publish, in the order they were chosen. */
  clipIds: string[];
  /** Networks ticked when the panel opens (the ones connected, by default). */
  platforms?: Platform[];
  onClose: () => void;
}

/**
 * E1 · Publicar: one or several clips, in one or several networks, now or
 * scheduled. Opens over the screen that asked for it (`?publicar=<clipId>[,…]`).
 * Order of the panel (map 9.1.7): clip → networks → when → text and options per network.
 */
export function PublishPanel({ clipIds, platforms: initialPlatforms, onClose }: PublishPanelProps) {
  const uid = useId();
  const toast = useToast();
  const billing = useBillingDialogs();
  const isMobile = useIsMobile();
  const { workspace } = useSession();
  const { can, gate, reason: roleReason } = usePermissions();
  const planInfo = usePlan();
  const plans = usePlans();
  const accountsQuery = useSocialAccounts();
  const panel = usePanelClips(clipIds);
  const actions = usePostActions();

  const mayPublish = can('post.publish');
  // While the plan loads nothing is shown as locked.
  const allows = (feature: 'scheduling' | 'crossposting' | 'seo') => planInfo.isLoading || planInfo.allows(feature);
  const planWith = (field: 'allow_scheduling' | 'allow_crossposting') =>
    [...(plans.data ?? [])].sort((a, b) => a.sort_order - b.sort_order).find((plan) => plan[field])?.name ?? 'Creador';
  const accounts = accountsQuery.data;
  const many = clipIds.length > 1;

  /* ── State ─────────────────────────────────────────────────────────────── */
  const [picked, setPicked] = useState<Platform[] | null>(initialPlatforms?.length ? initialPlatforms : null);
  const [when, setWhen] = useState<WhenMode>('now');
  const [day, setDay] = useState(() => dayKey(addDays(new Date(), 1)));
  const [time, setTime] = useState('18:00');
  const [spread, setSpread] = useState<SpreadMode>('daily');
  const [orderState, setOrderState] = useState<string[] | null>(null);
  const [texts, setTexts] = useState<Record<PairKey, TextValue>>({});
  const [tiktok, setTiktok] = useState<TikTokForm>(EMPTY_TIKTOK);
  const [tabState, setTabState] = useState<Platform | null>(null);
  const [previewIndex, setPreviewIndex] = useState(0);
  const [editingClip, setEditingClip] = useState<string | null>(null);
  const [connecting, setConnecting] = useState<Platform | null>(null);
  const [submitting, setSubmitting] = useState(false);
  /** Pairs that already went out, with the post the server returned: a retry never sends them again. */
  const [sent, setSent] = useState<Record<PairKey, ScheduledPost>>({});
  const sentRef = useRef<Record<PairKey, ScheduledPost>>({});
  /** The `Idempotency-Key` of the batch being created, kept until it goes through. */
  const createKey = useRef<{ signature: string; key: string } | null>(null);
  /** Pairs of the last attempt that did not go out, with the reason. */
  const [failures, setFailures] = useState<PairFailure[]>([]);
  const [regenerating, setRegenerating] = useState<Set<PairKey>>(() => new Set());
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  /* ── Derived ───────────────────────────────────────────────────────────── */
  const connected = useMemo(() => PLATFORMS.filter((platform) => accountOf(accounts, platform)?.status === 'connected'), [accounts]);
  const crossposting = allows('crossposting');
  const selected = useMemo(() => {
    const wanted = picked ?? (crossposting ? connected : connected.slice(0, 1));
    const usable = PLATFORMS.filter((platform) => wanted.includes(platform) && connected.includes(platform));
    return crossposting ? usable : usable.slice(0, 1);
  }, [picked, connected, crossposting]);
  const tab = tabState && selected.includes(tabState) ? tabState : (selected[0] ?? null);

  const order = useMemo(() => {
    const known = orderState?.filter((id) => clipIds.includes(id)) ?? [];
    return [...known, ...clipIds.filter((id) => !known.includes(id))];
  }, [orderState, clipIds]);
  const clips = useMemo(() => order.map((id) => panel.clips.find((clip) => clip.id === id)).filter((clip): clip is PanelClip => Boolean(clip)), [order, panel.clips]);
  const ready = !panel.isLoading && clips.length === clipIds.length && Boolean(accounts);
  const notRendered = clips.filter((clip) => !clip.ready);

  const start = useMemo(() => (when === 'later' ? fromInputs(day, time) : null), [when, day, time]);
  const dates = useMemo(() => scheduleFor(order, when, start, many ? spread : 'together'), [order, when, start, many, spread]);
  const inPast = when === 'later' && start !== null && start.getTime() <= Date.now() + 60_000;

  /* ── AI text: creating the publication as a draft is what generates it ─── */
  // Only for what is on screen: the clips of the network tab that is open. The rest get their text when they are published.
  const wantsSeo = mayPublish && !planInfo.isLoading && planInfo.allows('seo');
  const wanted = useMemo<PanelPair[]>(
    () => (tab ? clips.filter((clip) => clip.seo && clip.ready).map((clip) => ({ clipId: clip.id, platform: tab })) : []),
    [clips, tab],
  );
  const drafts = usePanelDrafts({ clipIds, wanted, enabled: wantsSeo && !submitting });
  const { draftOf } = drafts;
  const textOf = (clipId: string, platform: Platform): TextValue => {
    const own = texts[pairKey(clipId, platform)];
    if (own) return own;
    const draft = draftOf(clipId, platform);
    return draft ? { caption: draft.caption, hashtags: draft.hashtags } : EMPTY_TEXT;
  };
  const isGenerating = (clipId: string, platform: Platform): boolean => {
    const key = pairKey(clipId, platform);
    const draft = draftOf(clipId, platform);
    if (texts[key] || !draft || !seoPending(draft)) return false;
    return !draft.caption || regenerating.has(key);
  };
  const originOf = (clipId: string, platform: Platform): TextOrigin => {
    const draft = draftOf(clipId, platform);
    if (texts[pairKey(clipId, platform)]) return draft?.generated_caption ? 'edited' : 'manual';
    return draft?.generated_caption && draft.caption === draft.generated_caption ? 'ai' : 'manual';
  };
  const setText = (clipId: string, platform: Platform, value: TextValue) => setTexts((current) => ({ ...current, [pairKey(clipId, platform)]: value }));

  const checks = selected.map((platform) => checkNetwork({ platform, account: accountOf(accounts, platform), clips, textOf: (clipId) => textOf(clipId, platform), tiktok }));
  const checkOf = (platform: Platform): NetworkCheck | undefined => checks.find((check) => check.platform === platform);
  const failing = checks.find((check) => !check.ok);
  // Only what is already scheduled around the chosen dates: a workspace can have more than a page of publications.
  const range = useMemo(() => {
    const times = [...dates.values()].filter((date): date is Date => Boolean(date)).map((date) => Math.floor(date.getTime() / 60_000) * 60_000);
    if (times.length === 0) return null;
    return { from: new Date(Math.min(...times)).toISOString(), to: new Date(Math.max(...times) + 59_999).toISOString() };
  }, [dates]);
  const scheduledQuery = usePosts({ status: 'SCHEDULED', page_size: 100, ...(range ?? {}) }, { enabled: range !== null });
  const scheduled = range ? scheduledQuery.data?.results : undefined;
  const clashes = useMemo(() => findClashes(dates, selected, scheduled ?? []), [dates, selected, scheduled]);
  const pending = useMemo<PanelPair[]>(
    () => order.flatMap((clipId) => selected.map((platform) => ({ clipId, platform }))).filter((pair) => !sent[pairKey(pair.clipId, pair.platform)]),
    [order, selected, sent],
  );
  const sentCount = Object.keys(sent).length;
  const total = pending.length;

  /* ── Close ─────────────────────────────────────────────────────────────── */
  const close = () => {
    if (submitting) return;
    // Drafts this panel created only to get the AI text are not left behind. The ones that went out are not drafts any more.
    void drafts.discardAll();
    if (sentCount > 0) {
      const done = outcomeSummary({ platforms: PLATFORMS, clips, sent: Object.keys(sent) as PairKey[], failures: [], when });
      toast.success(done.title, { action: { label: 'Ver', href: '/publicaciones' } });
    }
    onClose();
  };

  /* ── Why the button is disabled ────────────────────────────────────────── */
  const focusField = (id: string, platform?: Platform) => {
    if (platform) setTabState(platform);
    // The field may be in a tab that is about to render.
    window.setTimeout(() => {
      const element = document.getElementById(id);
      element?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
      element?.focus({ preventScroll: true });
    }, 0);
  };
  const ids = { date: `${uid}-date`, privacy: `${uid}-privacy`, caption: `${uid}-caption`, nets: `${uid}-nets` };
  let blocker: { text: string; severe: boolean; onFix?: () => void } | null = null;
  if (!ready) blocker = null;
  else if (notRendered.length > 0) {
    blocker = { text: many ? `«${notRendered[0]?.title}» todavía no está renderizado` : 'El clip todavía no está renderizado', severe: true };
  } else if (connected.length === 0) blocker = { text: 'Conectá una red para publicar', severe: false, onFix: () => focusField(ids.nets) };
  else if (selected.length === 0) blocker = { text: 'Elegí al menos una red', severe: false, onFix: () => focusField(ids.nets) };
  else if (when === 'later' && !start) blocker = { text: 'Elegí la fecha y la hora', severe: false, onFix: () => focusField(ids.date) };
  else if (inPast) blocker = { text: 'Elegí una fecha y una hora que todavía no pasaron', severe: true, onFix: () => focusField(ids.date) };
  else if (failing?.failing) {
    const requirement = failing.failing;
    const target = requirement.key === 'privacy' || requirement.key === 'disclosure' ? ids.privacy : ids.caption;
    blocker = { text: requirement.action, severe: requirement.severe, onFix: () => focusField(target, failing.platform) };
  }
  const canSubmit = ready && !blocker && mayPublish;

  /* ── Submit ────────────────────────────────────────────────────────────── */
  const reasonOf = (error: unknown): string => {
    if (error instanceof ApiError && error.code === 'account_not_connected') {
      return errorMessage(error, 'La cuenta de la red no está conectada. Reconectala para publicar.');
    }
    if (error instanceof ApiError && error.status === 400) {
      return Object.values(error.fieldErrors)[0]?.[0] ?? errorMessage(error, 'Revisá los datos y probá de nuevo.');
    }
    if (error instanceof ApiError && error.status === 403) return roleReason;
    return errorMessage(error, 'Probá de nuevo.');
  };

  const submit = async () => {
    if (!canSubmit || submitting) return;
    setSubmitting(true);
    setFailures([]);
    // Drafts still being created must exist before deciding what is new and what is a draft.
    await drafts.whenIdle();
    // Only what did not go out yet: a retry after a partial failure never sends a publication twice.
    const pairs = order.flatMap((clipId) => selected.map((platform) => ({ clipId, platform }))).filter((pair) => !sentRef.current[pairKey(pair.clipId, pair.platform)]);
    const { creates, updates } = buildSubmission({
      pairs,
      dates,
      textOf,
      hasText: (clipId, platform) => Boolean(texts[pairKey(clipId, platform)]) || (Boolean(drafts.read(clipId, platform)) && !isGenerating(clipId, platform)),
      draftOf: drafts.read,
      tiktok: tiktokOptions(tiktok, accountOf(accounts, 'TIKTOK')),
    });
    const out: Record<PairKey, ScheduledPost> = {};
    const failed: PairFailure[] = [];
    let billed = false;
    let accountGone = false;
    const fail = (clipId: string, platform: Platform, error: unknown) => {
      if (error instanceof ApiError && error.code === 'account_not_connected') accountGone = true;
      failed.push({ clipId, platform, message: reasonOf(error) });
    };

    // New publications first: the batch is all or nothing, so a plan or account error there changes nothing.
    if (creates.length > 0) {
      try {
        const first = creates[0];
        // One key for this exact batch: if the answer is lost (a timeout) and the person presses again,
        // the same key goes out and the server answers with what it already created, instead of publishing twice.
        const signature = JSON.stringify(creates);
        if (createKey.current?.signature !== signature) createKey.current = { signature, key: crypto.randomUUID() };
        const { key } = createKey.current;
        const posts = creates.length === 1 && first ? [await actions.create(first, key)] : await actions.createBulk(creates, key);
        createKey.current = null;
        for (const post of posts) out[pairKey(post.video_clip, post.platform)] = post;
      } catch (error) {
        if (billing.handleError(error)) billed = true;
        else for (const item of creates) fail(item.video_clip, item.platform, error);
      }
    }
    // Then each draft, one by one: the ones that go out are recorded even if a later one fails.
    for (const item of billed ? [] : updates) {
      const { clipId, platform, draft, request } = item;
      try {
        const post = await actions.update(request);
        out[pairKey(clipId, platform)] = post;
        drafts.markSent(draft);
      } catch (error) {
        if (billing.handleError(error)) {
          billed = true;
          break;
        }
        const stale = error instanceof ApiError && (error.status === 404 || (error.status === 409 && error.code !== 'account_not_connected'));
        if (!stale) {
          fail(clipId, platform, error);
          continue;
        }
        // The draft changed under the panel (published from another tab, or deleted): ask what it is now.
        try {
          const current = await actions.get(draft.id);
          if (WENT_OUT.has(current.status)) {
            out[pairKey(clipId, platform)] = current;
            drafts.markSent(draft);
          } else {
            // Still a draft, or a draft whose AI text failed and the backend marked FAILED: it did not go out.
            // One that is no longer a draft cannot be sent with PATCH: the next attempt creates the publication.
            if (current.status !== 'DRAFT') drafts.drop(draft);
            fail(clipId, platform, error);
          }
        } catch {
          // It no longer exists: the next attempt creates the publication from scratch.
          drafts.drop(draft);
          fail(clipId, platform, error);
        }
      }
    }

    const went = Object.keys(out).length;
    if (went > 0) {
      sentRef.current = { ...sentRef.current, ...out };
      setSent(sentRef.current);
      void actions.invalidate();
    }
    if (accountGone) void accountsQuery.refetch();
    setSubmitting(false);
    if (billed || failed.length > 0 || went < pairs.length) {
      setFailures(failed);
      return;
    }

    const count = Object.keys(sentRef.current).length;
    // What went out, which after a partial failure can be less than what is ticked now.
    const networks = joinNetworks(PLATFORMS.filter((platform) => clips.some((clip) => sentRef.current[pairKey(clip.id, platform)])));
    const firstClip = clips[0];
    const message =
      when === 'now'
        ? many
          ? `Se están publicando ${clips.length} clips en ${networks}`
          : `«${firstClip?.title}» se está publicando en ${networks}`
        : many
          ? `${count} publicaciones programadas en ${networks}`
          : `«${firstClip?.title}» quedó programado para el ${formatDateTime(start as Date)}`;
    toast.success(message, { action: { label: 'Ver', href: '/publicaciones' } });
    // Drafts of a network that was unticked afterwards are not part of what went out.
    void drafts.discardAll();
    onClose();
  };

  const onRegenerate = (clipId: string, platform: Platform) => {
    const draft = draftOf(clipId, platform);
    if (!draft) return;
    const key = pairKey(clipId, platform);
    const stop = () =>
      setRegenerating((current) => {
        const next = new Set(current);
        next.delete(key);
        return next;
      });
    setRegenerating((current) => new Set(current).add(key));
    // The new text replaces what is in the box, including what the user typed.
    setTexts((current) => {
      const next = { ...current };
      delete next[key];
      return next;
    });
    actions.regenerate(draft.id).then(
      (post) => drafts.put(post),
      (error: unknown) => {
        stop();
        if (!billing.handleError(error)) toast.error(errorMessage(error, 'No pudimos generar el texto de nuevo.'));
      },
    );
  };
  const regenerateFor = (clip: PanelClip, platform: Platform) =>
    clip.seo && allows('seo') && mayPublish && draftOf(clip.id, platform) ? () => onRegenerate(clip.id, platform) : undefined;

  const togglePlatform = (platform: Platform, checked: boolean) => {
    if (!crossposting) return setPicked(checked ? [platform] : []);
    setPicked(checked ? [...selected, platform] : selected.filter((item) => item !== platform));
  };
  const moveClip = (clipId: string, to: number) => {
    const next = order.filter((id) => id !== clipId);
    next.splice(Math.max(0, Math.min(to, next.length)), 0, clipId);
    setOrderState(next);
  };

  /* ── Render ────────────────────────────────────────────────────────────── */
  const title = many ? `Publicar ${clipIds.length} clips` : 'Publicar clip';
  const zone = timeZoneLabel(start ?? new Date());
  const firstClip = clips[0];
  const totalDuration = clips.reduce((sum, clip) => sum + clip.duration, 0);
  const projectTitles = [...new Set(clips.map((clip) => clip.projectTitle))];
  const resolutions = [...new Set(clips.map((clip) => clip.resolution).filter(Boolean))];
  const previewClip = clips[Math.min(previewIndex, Math.max(0, clips.length - 1))];
  // After a partial failure with nothing left to send (the failed network was unticked or disconnected) the button only closes.
  const publishLabel = sentCount > 0 && pending.length === 0 ? 'Listo' : when === 'now' ? 'Publicar' : 'Programar';
  const brandedOn = tiktok.disclose && tiktok.brandedContent;
  const outcome = failures.length > 0 ? outcomeSummary({ platforms: PLATFORMS, clips, sent: Object.keys(sent) as PairKey[], failures, when }) : null;
  const whenSummary = when === 'now' ? 'ahora' : many ? scheduleSummary([...dates.values()]) : start ? formatDateTime(start) : 'elegí la fecha';
  const summary = many ? (
    <>
      <Num>{plural(total, 'publicación', 'publicaciones')}</Num> · <Num>{whenSummary}</Num>
    </>
  ) : (
    <>
      <Num>{plural(selected.length, 'red', 'redes')}</Num> · <Num>{whenSummary}</Num>
    </>
  );

  const footer = (
    <div className={styles.foot}>
      {selected.includes('TIKTOK') ? (
        <p className={styles.legal}>
          Al publicar, aceptás la{' '}
          {brandedOn ? (
            <>
              <a className="lnk" href={TIKTOK_BRANDED_URL} target="_blank" rel="noopener noreferrer">
                política de contenido de marca
              </a>{' '}
              y la{' '}
            </>
          ) : null}
          <a className="lnk" href={TIKTOK_MUSIC_URL} target="_blank" rel="noopener noreferrer">
            confirmación de uso de música de TikTok
          </a>
          .
        </p>
      ) : null}
      <ActionBar
        className={styles.bar}
        summary={summary}
        detail={
          blocker ? (
            <span className={cx(styles.reason, blocker.severe && styles.reasonErr)} id={`${uid}-reason`}>
              <Icon name={blocker.severe ? 'error' : 'warning'} size={14} />
              {blocker.onFix ? (
                <button type="button" className={styles.linkBtn} onClick={blocker.onFix}>
                  {blocker.text}
                </button>
              ) : (
                <span>{blocker.text}</span>
              )}
            </span>
          ) : undefined
        }
      >
        {isMobile ? null : (
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancelar
          </Button>
        )}
        <Button
          variant="primary"
          size="lg"
          loading={submitting}
          disabled={!ready || Boolean(blocker)}
          aria-describedby={blocker ? `${uid}-reason` : undefined}
          onClick={() => void submit()}
          {...(ready && !blocker ? gate('post.publish') : {})}
        >
          {publishLabel}
        </Button>
      </ActionBar>
    </div>
  );

  const loadFailed = Boolean(panel.error) || accountsQuery.isError;

  const tiktokAccount = accountOf(accounts, 'TIKTOK');
  const privacyOptions = (tiktokAccount?.tiktok?.privacy_options ?? []) as TikTokPrivacy[];

  const networkPanel = (platform: Platform) => {
    const account = accountOf(accounts, platform);
    const check = checkOf(platform);
    const noun = NETWORK_NOUN[platform];
    const readyTexts = clips.filter((clip) => !isGenerating(clip.id, platform)).length;
    return (
      <div className={styles.tk} role="tabpanel" id={`${uid}-panel-${platform}`} aria-labelledby={`${uid}-tab-${platform}`}>
        <p className={styles.acct}>
          <Avatar name={workspace?.name ?? noun} kind="workspace" size="sm" seed={workspace?.id} />
          <span>
            {many ? `Los ${clips.length} se publican en ` : 'Se publica en '}
            <b>{account?.username ?? noun}</b> · {workspace?.name ? `${workspace.name}, ` : ''}cuenta de {noun}
          </span>
        </p>

        {platform === 'TIKTOK' ? (
          <Field
            label={many ? 'Quién puede ver estos videos' : 'Quién puede ver este video'}
            labelAside={<span className={styles.secNote}>Obligatorio</span>}
            htmlFor={ids.privacy}
            hint={`${many ? `Vale para los ${clips.length} clips. ` : ''}TikTok pide que la elijas en cada publicación, por eso no viene completada. Ves las opciones que permite tu cuenta.`}
          >
            <Select
              id={ids.privacy}
              required
              value={tiktok.privacy}
              placeholder="Elegí una opción"
              options={privacyOptions.map((option) => ({
                value: option,
                label: option === 'SELF_ONLY' && brandedOn ? `${PRIVACY_LABEL[option]} (no disponible con contenido de marca)` : PRIVACY_LABEL[option],
                disabled: option === 'SELF_ONLY' && brandedOn,
              }))}
              onChange={(event) => setTiktok((current) => ({ ...current, privacy: event.target.value as TikTokPrivacy }))}
            />
          </Field>
        ) : null}

        {many ? (
          <div className={styles.textBlock} style={{ gap: 6 }}>
            <div className={styles.thead}>
              <span className={styles.label} id={`${uid}-texts-${platform}`}>
                Caption y hashtags de cada clip
              </span>
              <span className={styles.secNote}>
                <Num>{readyTexts}</Num> de <Num>{clips.length}</Num> listos
              </span>
            </div>
            <ul className={cx(styles.rows, styles.texts)} aria-labelledby={`${uid}-texts-${platform}`}>
              {clips.map((clip, index) => {
                const text = textOf(clip.id, platform);
                const generating = isGenerating(clip.id, platform);
                const origin = originOf(clip.id, platform);
                const editing = editingClip === clip.id;
                const limit = account?.limits.caption_max_chars ?? 2200;
                const length = textLength(text.caption, text.hashtags);
                return (
                  <li key={clip.id} aria-busy={generating || undefined}>
                    <VideoThumb ratio="9:16" xs seed={clip.id} className={styles.textsThumb} />
                    <div className={styles.tbody}>
                      {editing ? (
                        <>
                          <TextEditor
                            platform={platform}
                            account={account}
                            label={clip.title}
                            value={text}
                            onChange={(value) => setText(clip.id, platform, value)}
                            origin={origin}
                            generating={generating}
                            onRegenerate={regenerateFor(clip, platform)}
                            captionId={index === 0 ? ids.caption : undefined}
                          />
                          <div>
                            <Button size="sm" variant="ghost" onClick={() => setEditingClip(null)}>
                              Listo
                            </Button>
                          </div>
                        </>
                      ) : (
                        <>
                          <div className={styles.thead}>
                            <span className={styles.rtitle}>{clip.title}</span>
                            {generating ? (
                              <span className={styles.genLabel} role="status">
                                <Spinner size={12} />
                                Generando texto…
                              </span>
                            ) : (
                              <>
                                {origin === 'ai' ? (
                                  <span className={styles.ai}>
                                    <Icon name="sparkles" size={12} />
                                    Sugerido por IA
                                  </span>
                                ) : origin === 'edited' ? (
                                  <span className={cx(styles.ai, styles.aiEdited)}>Editado por vos</span>
                                ) : null}
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  iconLeft="edit"
                                  className={styles.regen}
                                  aria-label={`Editar el texto de «${clip.title}»`}
                                  id={index === 0 ? ids.caption : undefined}
                                  onClick={() => setEditingClip(clip.id)}
                                >
                                  {text.caption || text.hashtags.length > 0 ? 'Editar' : 'Escribir'}
                                </Button>
                              </>
                            )}
                          </div>
                          {generating ? (
                            <>
                              <div className={styles.textBlock} style={{ gap: 6, padding: '4px 0 2px' }} aria-hidden="true">
                                <Skeleton width="92%" height={8} />
                                <Skeleton width="64%" height={8} />
                              </div>
                              <p className={styles.tmeta}>
                                <span>Si {when === 'now' ? 'publicás' : 'programás'} ahora, el texto se completa solo apenas esté listo.</span>
                              </p>
                            </>
                          ) : (
                            <>
                              <p className={styles.tcap}>{text.caption || 'Sin texto todavía.'}</p>
                              <p className={styles.tmeta}>
                                <span>{formatHashtags(text.hashtags.slice(0, 3))}{text.hashtags.length > 3 ? <span className="subtle"> +{text.hashtags.length - 3}</span> : null}</span>
                                <span className={cx('num', length > limit && styles.countErr)}>
                                  {formatNumber(length)} / {formatNumber(limit)}
                                </span>
                              </p>
                            </>
                          )}
                        </>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        ) : firstClip ? (
          <TextEditor
            platform={platform}
            account={account}
            value={textOf(firstClip.id, platform)}
            onChange={(value) => setText(firstClip.id, platform, value)}
            origin={originOf(firstClip.id, platform)}
            generating={isGenerating(firstClip.id, platform)}
            onRegenerate={regenerateFor(firstClip, platform)}
            captionId={ids.caption}
          />
        ) : null}

        {previewClip ? (
          <div className={styles.textBlock} style={{ gap: 6 }}>
            <div className={styles.thead}>
              <span className={styles.label}>Vista previa</span>
              {many ? (
                <span className={styles.pager}>
                  <IconButton icon="chevron-left" size="sm" label="Clip anterior" disabled={previewIndex === 0} onClick={() => setPreviewIndex((index) => Math.max(0, index - 1))} />
                  <span className="num">
                    Clip {Math.min(previewIndex, clips.length - 1) + 1} de {clips.length}
                  </span>
                  <IconButton icon="chevron-right" size="sm" label="Clip siguiente" disabled={previewIndex >= clips.length - 1} onClick={() => setPreviewIndex((index) => Math.min(clips.length - 1, index + 1))} />
                </span>
              ) : null}
            </div>
            <div className={styles.preview}>
              <VideoThumb ratio="9:16" seed={previewClip.id} duration={previewClip.duration} className={styles.previewThumb} />
              <div className={styles.post}>
                <p className={styles.secNote}>
                  Así se va a ver en {noun}
                  {dates.get(previewClip.id) ? (
                    <>
                      {' · '}
                      <Num>
                        {shortDay(dates.get(previewClip.id) as Date)}, {formatTime(dates.get(previewClip.id) as Date)}
                      </Num>
                    </>
                  ) : null}
                </p>
                <p className={styles.postUser}>{account?.username ?? noun}</p>
                {isGenerating(previewClip.id, platform) ? (
                  <p className="t-small subtle">El texto se está generando.</p>
                ) : (
                  <>
                    <p className={styles.postCap}>{textOf(previewClip.id, platform).caption || <span className="subtle">Sin texto.</span>}</p>
                    {textOf(previewClip.id, platform).hashtags.length > 0 ? (
                      <p className="t-small muted">
                        {formatHashtags(textOf(previewClip.id, platform).hashtags.slice(0, 3))}
                        {textOf(previewClip.id, platform).hashtags.length > 3 ? <span className="subtle"> … más</span> : null}
                      </p>
                    ) : null}
                  </>
                )}
              </div>
            </div>
          </div>
        ) : null}

        {platform === 'TIKTOK' ? (
          <>
            <fieldset className={styles.fieldset}>
              <legend>Permitir a otros usuarios</legend>
              <div className={styles.opts}>
                <Checkbox label="Comentarios" checked={tiktok.comment && !tiktokAccount?.tiktok?.comment_disabled} disabled={Boolean(tiktokAccount?.tiktok?.comment_disabled)} onChange={(event) => setTiktok((current) => ({ ...current, comment: event.target.checked }))} />
                <Checkbox label="Dúos" checked={tiktok.duet && !tiktokAccount?.tiktok?.duet_disabled} disabled={Boolean(tiktokAccount?.tiktok?.duet_disabled)} onChange={(event) => setTiktok((current) => ({ ...current, duet: event.target.checked }))} />
                <Checkbox label="Stitch" checked={tiktok.stitch && !tiktokAccount?.tiktok?.stitch_disabled} disabled={Boolean(tiktokAccount?.tiktok?.stitch_disabled)} onChange={(event) => setTiktok((current) => ({ ...current, stitch: event.target.checked }))} />
              </div>
              <p className={styles.hint} style={{ marginTop: 8 }}>
                Vienen desmarcados{many ? ` y valen para los ${clips.length} clips` : ''}. Si tu cuenta de TikTok tiene alguno apagado, acá aparece deshabilitado.
              </p>
            </fieldset>

            <div>
              <div className={styles.commercial}>
                <div>
                  <span className={styles.label} id={`${uid}-commercial`}>
                    Divulgar contenido comercial
                  </span>
                  <p className={styles.hint} style={{ marginTop: 2 }}>
                    Activalo si {many ? 'los videos promocionan' : 'el video promociona'} una marca, un producto o un servicio. Al activarlo elegís «Tu marca» o «Contenido de marca».
                  </p>
                </div>
                <Switch checked={tiktok.disclose} onChange={(value) => setTiktok((current) => ({ ...current, disclose: value }))} ariaLabel="Divulgar contenido comercial" />
              </div>
              {tiktok.disclose ? (
                <div className={styles.optsCol} style={{ marginTop: 12 }} role="group" aria-labelledby={`${uid}-commercial`}>
                  <Checkbox
                    label="Tu marca"
                    description={`Promocionás tu propio negocio. ${many ? 'Los videos se etiquetan' : 'El video se etiqueta'} como «Contenido promocional».`}
                    checked={tiktok.brandOrganic}
                    onChange={(event) => setTiktok((current) => ({ ...current, brandOrganic: event.target.checked }))}
                  />
                  <Checkbox
                    label="Contenido de marca"
                    description={
                      tiktok.privacy === 'SELF_ONLY' && !tiktok.brandedContent
                        ? 'No se puede con la privacidad «Solo yo»: TikTok pide que el contenido de marca sea visible para otros.'
                        : `Promocionás a un tercero. ${many ? 'Los videos se etiquetan' : 'El video se etiqueta'} como «Colaboración pagada».`
                    }
                    checked={tiktok.brandedContent}
                    disabled={tiktok.privacy === 'SELF_ONLY' && !tiktok.brandedContent}
                    onChange={(event) => setTiktok((current) => ({ ...current, brandedContent: event.target.checked }))}
                  />
                </div>
              ) : null}
            </div>
          </>
        ) : null}

        {check ? (
          <div className={styles.reqs} aria-label={`Requisitos de ${NETWORKS[platform].name}`}>
            <p className={cx(styles.checkline, check.ok ? styles.checkOk : check.failing?.severe ? styles.checkErr : styles.checkWarn)}>
              <Icon name={check.ok ? 'check-circle' : check.failing?.severe ? 'error' : 'warning'} size={16} />
              <span>
                <b>
                  <Num>{check.passed}</Num> de <Num>{check.total}</Num> requisitos
                </b>
                {check.failing ? ` · ${check.failing.missing}` : ' · listo para publicar'}
              </span>
            </p>
            {check.requirements.some((item) => item.ok) ? (
              <p className={cx(styles.reqOk, 'num')}>
                {many ? `Los ${clips.length} clips ya cumplen: ` : 'Ya cumple: '}
                {check.requirements
                  .filter((item) => item.ok)
                  .map((item) => item.met)
                  .join(' · ')}
              </p>
            ) : null}
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <>
      <Drawer open onClose={close} title={title} footer={footer} className={styles.pub} dismissable={!submitting}>
        <div className={styles.bleed}>
          {loadFailed ? (
            <div className={styles.loading}>
              <Banner
                tone="err"
                title={panel.error instanceof ApiError && panel.error.isNotFound ? 'No encontramos el clip' : errorMessage(panel.error ?? accountsQuery.error, 'No pudimos cargar esto')}
                action={
                  panel.error instanceof ApiError && panel.error.isNotFound ? undefined : (
                    <Button
                      size="sm"
                      iconLeft="retry"
                      onClick={() => {
                        panel.refetch();
                        if (accountsQuery.isError) void accountsQuery.refetch();
                      }}
                    >
                      Reintentar
                    </Button>
                  )
                }
              >
                {panel.error instanceof ApiError && panel.error.isNotFound ? 'Puede que lo hayan borrado. Cerrá el panel y elegí otro clip.' : null}
              </Banner>
            </div>
          ) : !ready ? (
            <div className={styles.loading} aria-busy="true" aria-label="Cargando el panel de publicación">
              <Skeleton height={48} />
              <Skeleton width="30%" height={18} />
              <Skeleton height={52} />
              <Skeleton height={52} />
              <Skeleton height={52} />
              <Skeleton width="30%" height={18} />
              <Skeleton height={120} />
            </div>
          ) : (
            <>
              <div className={styles.clipStrip}>
                {many ? (
                  <span className={styles.stack3} aria-hidden="true">
                    {clips.slice(0, 3).map((clip) => (
                      <VideoThumb key={clip.id} ratio="9:16" xs seed={clip.id} className={styles.stackThumb} />
                    ))}
                  </span>
                ) : (
                  <VideoThumb ratio="9:16" xs seed={firstClip?.id} className={styles.xsThumb} />
                )}
                <div>
                  <p className={styles.clipTitle}>{many ? `${clips.length} clips seleccionados` : firstClip?.title}</p>
                  <p className={cx(styles.secNote, 'num')}>
                    {projectTitles.length === 1 ? projectTitles[0] : `${projectTitles.length} proyectos`} · {formatDuration(totalDuration)}
                    {many ? ' en total' : ''}
                    {resolutions.length === 1 ? ` · ${resolutions[0]}` : ''}
                  </p>
                </div>
              </div>

              {notRendered.length > 0 ? (
                <div className={styles.sec}>
                  <Banner tone="err" title={many ? 'Hay clips sin renderizar' : 'Este clip todavía no está renderizado'}>
                    {many ? `${notRendered.map((clip) => `«${clip.title}»`).join(', ')}: solo se publican los clips que ya terminaron de generarse.` : 'Solo se publican los clips que ya terminaron de generarse.'}
                  </Banner>
                </div>
              ) : null}
              {!mayPublish ? (
                <div className={styles.sec}>
                  <Banner tone="info" icon="lock" title="Tu rol no publica">
                    Podés ver cómo quedaría, pero publicar y programar es para Editores y Admins. Pedile acceso a un admin.
                  </Banner>
                </div>
              ) : null}

              <section className={styles.sec} aria-labelledby={`${uid}-redes`}>
                <div className={styles.secTitle}>
                  <h3 className="t-h3" id={`${uid}-redes`}>
                    Redes
                  </h3>
                  <span className={styles.secNote}>
                    {crossposting ? (
                      <>
                        <Num>{plural(selected.length, 'elegida', 'elegidas')}</Num>
                        {many ? ` · valen para los ${clips.length} clips` : planInfo.plan ? ` · el plan ${planInfo.plan.name} permite publicar en varias a la vez` : ''}
                      </>
                    ) : (
                      'Tu plan permite una red por publicación'
                    )}
                  </span>
                </div>
                {crossposting ? null : <PlanLock plan={planWith('allow_crossposting')} />}
                <div className={styles.netList} id={ids.nets} tabIndex={-1}>
                  {PLATFORMS.map((platform) => {
                    const account = accountOf(accounts, platform);
                    const status = account?.status ?? 'disconnected';
                    const badge = ACCOUNT_BADGE[status];
                    const on = selected.includes(platform);
                    const usable = status === 'connected';
                    const name = NETWORKS[platform].name;
                    return (
                      <div key={platform} className={cx(styles.netRow, on && styles.netRowOn)}>
                        <Checkbox
                          className={cx(styles.netPick, !usable && styles.netPickOff)}
                          checked={on}
                          disabled={!usable || submitting || clips.some((clip) => Boolean(sent[pairKey(clip.id, platform)]))}
                          aria-label={usable ? `Publicar en ${name}` : `${name}: ${status === 'expired' ? 'reconectá' : 'conectá'} la cuenta para elegirla`}
                          onChange={(event) => togglePlatform(platform, event.target.checked)}
                          label={
                            <span className={styles.netLabel}>
                              <NetworkChip platform={platform} hideName />
                              <span className={styles.netTxt}>
                                <span>{name}</span>
                                <span>{account?.username ?? 'Sin cuenta conectada'}</span>
                              </span>
                            </span>
                          }
                        />
                        <Badge tone={badge.tone} dot>
                          {badge.label}
                        </Badge>
                        {usable ? null : (
                          <span className={styles.netFix}>
                            <Button size="sm" iconRight="external" aria-label={`${status === 'expired' ? 'Reconectar' : 'Conectar'} ${name}, se abre en otra ventana`} onClick={() => setConnecting(platform)} {...gate('social.manage')}>
                              {status === 'expired' ? 'Reconectar' : 'Conectar'}
                            </Button>
                            <span className={styles.netFixHint}>Se abre en otra ventana.</span>
                          </span>
                        )}
                      </div>
                    );
                  })}
                </div>
              </section>

              <section className={styles.sec} aria-labelledby={`${uid}-cuando`}>
                <div className={styles.secTitle}>
                  <h3 className="t-h3" id={`${uid}-cuando`}>
                    Cuándo
                  </h3>
                  <SegmentedControl
                    ariaLabel="Cuándo publicar"
                    value={when}
                    onChange={setWhen}
                    block={isMobile}
                    touch={isMobile}
                    options={[
                      { value: 'now', label: 'Ahora' },
                      { value: 'later', label: 'Programar', lockedReason: allows('scheduling') ? undefined : `Disponible en el plan ${planWith('allow_scheduling')}` },
                    ]}
                  />
                </div>
                {allows('scheduling') ? null : <PlanLock plan={planWith('allow_scheduling')} />}
                {when === 'later' ? (
                  <>
                    {many ? (
                      <div className={styles.mode}>
                        <span className={styles.label} id={`${uid}-mode`}>
                          Cómo salen los <Num>{clips.length}</Num> clips
                        </span>
                        <SegmentedControl
                          ariaLabel={`Cómo salen los ${clips.length} clips`}
                          value={spread}
                          onChange={setSpread}
                          options={[
                            { value: 'together', label: 'Todos juntos' },
                            { value: 'daily', label: 'Uno por día' },
                          ]}
                        />
                      </div>
                    ) : null}
                    <div className={styles.when}>
                      <Field label={many && spread === 'daily' ? 'Desde' : 'Fecha'} htmlFor={ids.date} error={inPast ? 'Esa fecha y hora ya pasaron.' : undefined}>
                        <Input id={ids.date} type="date" className="num" value={day} min={dayKey(new Date())} onChange={(event) => setDay(event.target.value)} />
                      </Field>
                      <Field label="Hora">
                        <Input type="time" className="num" value={time} onChange={(event) => setTime(event.target.value)} />
                      </Field>
                    </div>
                  </>
                ) : (
                  <p className={styles.hint}>
                    {many ? `Los ${clips.length} clips salen` : 'Sale'} apenas confirmás: {selected.length > 1 ? 'cada red lo publica' : 'la red lo publica'} en unos segundos.
                  </p>
                )}
                {many && when === 'later' ? (
                  <ol className={styles.rows} aria-label="Orden y fecha de cada clip">
                    {clips.map((clip, index) => {
                      const date = dates.get(clip.id);
                      return (
                        <li
                          key={clip.id}
                          className={cx(dragId === clip.id && styles.rowDragging, overId === clip.id && dragId !== clip.id && styles.rowOver)}
                          draggable
                          onDragStart={(event) => {
                            event.dataTransfer.effectAllowed = 'move';
                            event.dataTransfer.setData('text/plain', clip.title);
                            setDragId(clip.id);
                          }}
                          onDragOver={(event) => {
                            if (!dragId) return;
                            event.preventDefault();
                            if (overId !== clip.id) setOverId(clip.id);
                          }}
                          onDrop={(event) => {
                            event.preventDefault();
                            if (dragId && dragId !== clip.id) moveClip(dragId, index);
                            setDragId(null);
                            setOverId(null);
                          }}
                          onDragEnd={() => {
                            setDragId(null);
                            setOverId(null);
                          }}
                        >
                          <span className={styles.grip} aria-hidden="true">
                            <Icon name="list" size={14} />
                          </span>
                          <span className={cx(styles.ord, 'num')}>{index + 1}</span>
                          <VideoThumb ratio="9:16" xs seed={clip.id} className={styles.rowThumb} />
                          <span className={styles.rtitle}>{clip.title}</span>
                          <span className={cx(styles.rdate, 'num')}>
                            {date ? (
                              <>
                                <b>{shortDay(date)}</b> · {formatTime(date)}
                              </>
                            ) : (
                              '—'
                            )}
                          </span>
                          <span className={styles.moves}>
                            <IconButton icon="chevron-up" size="sm" label={`Subir «${clip.title}»`} disabled={index === 0} onClick={() => moveClip(clip.id, index - 1)} />
                            <IconButton icon="chevron-down" size="sm" label={`Bajar «${clip.title}»`} disabled={index === clips.length - 1} onClick={() => moveClip(clip.id, index + 1)} />
                          </span>
                        </li>
                      );
                    })}
                  </ol>
                ) : null}
                {when === 'later' ? (
                  <p className={styles.hintLine}>
                    <Icon name="globe" size={14} />
                    <span>
                      Zona horaria: {zone}, la de tu navegador.
                      {many ? (spread === 'daily' ? ' Arrastrá un clip, o usá las flechas, para cambiar qué día sale.' : '') : selected.length > 1 ? ` Vale para las ${selected.length} redes.` : ''}
                    </span>
                  </p>
                ) : null}
              </section>

              {tab ? (
                <section className={styles.sec} aria-labelledby={`${uid}-texto`}>
                  <h3 className="t-h3" id={`${uid}-texto`}>
                    Texto y opciones por red
                  </h3>
                  <div className={styles.ntabs} role="tablist" aria-label="Red">
                    {selected.map((platform, index) => {
                      const check = checkOf(platform);
                      const missing = check ? check.total - check.passed : 0;
                      const isSelected = platform === tab;
                      return (
                        <button
                          key={platform}
                          type="button"
                          role="tab"
                          id={`${uid}-tab-${platform}`}
                          aria-selected={isSelected}
                          aria-controls={`${uid}-panel-${platform}`}
                          tabIndex={isSelected ? 0 : -1}
                          className={styles.ntab}
                          onClick={() => setTabState(platform)}
                          onKeyDown={(event) => {
                            if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return;
                            event.preventDefault();
                            const next = selected[(index + (event.key === 'ArrowRight' ? 1 : selected.length - 1)) % selected.length] as Platform;
                            setTabState(next);
                            document.getElementById(`${uid}-tab-${next}`)?.focus();
                          }}
                        >
                          <NetworkChip platform={platform} size="sm" />
                          {check?.ok ? (
                            <Badge tone="ok" size="sm" icon="check">
                              Listo
                            </Badge>
                          ) : check && check.overBy > 0 ? (
                            <Badge tone="err" size="sm">
                              {check.overBy === 1 ? 'Sobra' : 'Sobran'} <Num>{formatNumber(check.overBy)}</Num>
                            </Badge>
                          ) : (
                            <Badge tone={check?.failing?.severe ? 'err' : 'warn'} size="sm">
                              {missing === 1 ? 'Falta' : 'Faltan'} <Num>{missing}</Num>
                            </Badge>
                          )}
                        </button>
                      );
                    })}
                  </div>
                  {networkPanel(tab)}
                </section>
              ) : null}

              {many && selected.length > 0 ? (
                <section className={styles.sec} aria-labelledby={`${uid}-rev`}>
                  <div className={styles.secTitle}>
                    <h3 className="t-h3" id={`${uid}-rev`}>
                      Revisá el calendario
                    </h3>
                    <span className={cx(styles.secNote, 'num')}>
                      {clips.length} clips × {plural(selected.length, 'red', 'redes')} = {plural(total, 'publicación', 'publicaciones')}
                    </span>
                  </div>
                  <ol className={styles.rev}>
                    {clips.map((clip) => {
                      const date = dates.get(clip.id);
                      return (
                        <li key={clip.id}>
                          <span className={cx(styles.revDay, 'num')}>
                            <b>{date ? shortDay(date) : 'Ahora'}</b>
                            {date ? <span className="muted">{formatTime(date)}</span> : null}
                          </span>
                          <span className={styles.revClip}>
                            <VideoThumb ratio="9:16" xs seed={clip.id} className={styles.rowThumb} />
                            <span>{clip.title}</span>
                          </span>
                          <div className={styles.revPubs}>
                            {selected.map((platform) => {
                              const check = checkOf(platform);
                              const failingKey = check?.failing?.key;
                              return (
                                <p key={platform} className={styles.revPub}>
                                  <NetworkChip platform={platform} size="sm" />
                                  {check?.ok ? (
                                    <Badge tone="info" size="sm" icon={when === 'now' ? 'pub-queued' : 'pub-scheduled'}>
                                      {when === 'now' ? 'Se publica' : 'Se programa'}
                                    </Badge>
                                  ) : (
                                    <Badge tone={check?.failing?.severe ? 'err' : 'warn'} size="sm">
                                      {failingKey === 'privacy' ? 'Falta la privacidad' : failingKey === 'text' ? 'Sobra texto' : failingKey === 'duration' ? 'Dura demasiado' : failingKey === 'hashtags' ? 'Sobran hashtags' : 'Falta un dato'}
                                    </Badge>
                                  )}
                                </p>
                              );
                            })}
                            {selected.some((platform) => isGenerating(clip.id, platform)) ? (
                              <p className={styles.revPub}>
                                <span className={styles.genLabel}>
                                  <Spinner size={12} />
                                  El texto de este clip se está generando.
                                </span>
                              </p>
                            ) : null}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                </section>
              ) : null}

              {clashes.length > 0 || outcome ? (
                <div className={styles.sec}>
                  {clashes.map((clash) => (
                    <p key={`${clash.platform}${clash.at.getTime()}${clash.sameClip ? 'same' : ''}`} className={styles.clash}>
                      <Icon name="warning" size={14} />
                      <span>
                        {clash.sameClip ? (
                          <>
                            «{clash.title}» ya está programado en {NETWORK_NOUN[clash.platform]} el <Num>{shortDay(clash.at)} a las {formatTime(clash.at)}</Num>: saldría dos veces.{' '}
                          </>
                        ) : (
                          <>
                            El <Num>{shortDay(clash.at)} a las {formatTime(clash.at)}</Num> ya sale «{clash.title}» en {NETWORK_NOUN[clash.platform]}: van a salir dos publicaciones a la misma hora.{' '}
                          </>
                        )}
                        <button type="button" className={styles.linkBtn} onClick={() => focusField(ids.date)}>
                          Cambiar la hora
                        </button>
                      </span>
                    </p>
                  ))}
                  {outcome ? (
                    <Banner tone={outcome.partial ? 'warn' : 'err'} live="alert" title={outcome.title}>
                      {outcome.detail}
                    </Banner>
                  ) : null}
                </div>
              ) : null}
            </>
          )}
        </div>
      </Drawer>
      <ConnectNetworkDialog platform={connecting} onClose={() => setConnecting(null)} />
    </>
  );
}
