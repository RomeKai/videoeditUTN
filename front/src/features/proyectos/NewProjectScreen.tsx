'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { RenderResolution } from '@/api/contracts/common';
import { ApiError, NetworkError } from '@/api/errors';
import { useBrandKits, useCreateProject, useEstimate, usePlans, usePricing, useProbeLink, useProject, useWallet } from '@/api/hooks';
import { useBillingDialogs } from '@/dialogs';
import { errorMessage, planLimitKind, uploadErrorCode, uploadErrorMessage } from '@/lib/error-messages';
import { formatDuration, formatNumber, formatPercent, formatTokens } from '@/lib/format';
import { useBeforeUnload, useDebounced, useIsMobile } from '@/lib/hooks';
import { estimateCost, firstPlanWith, planAllowsResolution, type CostEstimate } from '@/lib/pricing';
import { LoadError } from '@/shell/LoadError';
import { usePermissions, usePlan, useSession } from '@/shell/session';
import { Banner, Card, Crumbs, Icon, Page, PageHead, RadioCard, RadioGroup, Skeleton, Switch, Badge, cx, useToast } from '@/ui';
import styles from './NewProject.module.css';
import { ClipsSection, CustomizeSection, FormatSection, Section, WhatSection, type CustomizePanel, type SingleEditBlock } from './NewProjectOptions';
import { LongerPlanHint, OriginSection, type CutUpload, type FileState, type LinkState, type OriginTab, type VideoKind } from './NewProjectOrigin';
import { SummaryBar, SummaryCard, type CostView, type SummaryProps, type SummaryRow, type UploadProgress } from './NewProjectSummary';
import { checkFile, checkMedia, extensionsLabel, readMedia } from './media';
import {
  DEFAULT_CONFIG,
  PRESETS,
  buildCreateBody,
  buildPromptBody,
  configFromProject,
  loadLastConfig,
  matchesPreset,
  presetValuesOf,
  saveLastConfig,
  styleLine,
  subtitlesLine,
  titleFromFileName,
  type PresetValues,
  type ProjectConfig,
  type ProjectSourceInput,
} from './new-project-config';
import { linkProvider } from './project-view';

type PresetId = 'last' | 'source' | (typeof PRESETS)[number]['id'];
type Marked = 'resolution' | 'seo' | null;

const FIRST_PRESET = PRESETS[0];
const INITIAL_CONFIG: ProjectConfig = { ...DEFAULT_CONFIG, ...(FIRST_PRESET?.values ?? {}) };
const RESOLUTIONS: RenderResolution[] = ['720p', '1080p', '4K'];
const EMPTY_LINK: LinkState = { url: '', status: 'idle' };

function isAbort(error: unknown): boolean {
  return typeof error === 'object' && error !== null && (error as { name?: string }).name === 'AbortError';
}

function isUrl(text: string): boolean {
  try {
    const url = new URL(text);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

/**
 * C1 · Nuevo proyecto: one screen, no wizard. Origin, what to do, format, clips,
 * style, subtitles and intelligence at the left; the summary with the estimated
 * cost and the button fixed at the right (at the bottom on phones).
 *
 * URL parameters (links from other screens):
 * - `?desde=<projectId>`: starts from that project's configuration ("Crear de nuevo", "Duplicar configuración").
 * - `?origen=link|ia` and `?prompt=<texto>`: opens that origin tab, with the text filled in.
 */
export function NewProjectScreen() {
  const router = useRouter();
  const params = useSearchParams();
  const toast = useToast();
  const mobile = useIsMobile();
  const { me } = useSession();
  const { plan } = usePlan();
  const { gate, can } = usePermissions();
  const billing = useBillingDialogs();
  const pricingQuery = usePricing();
  const pricing = pricingQuery.data ?? null;
  const plans = usePlans().data ?? [];
  const wallet = useWallet();
  const brandKitsQuery = useBrandKits();
  const brandKits = useMemo(() => brandKitsQuery.data ?? [], [brandKitsQuery.data]);
  const create = useCreateProject();
  const probeLink = useProbeLink();
  const sourceId = params.get('desde');
  const sourceProject = useProject(sourceId);

  /* ── Form state ───────────────────────────────────────────────────────── */
  const [tab, setTab] = useState<OriginTab>(() => (params.get('origen') === 'ia' || params.get('prompt') ? 'ai' : params.get('origen') === 'link' ? 'link' : 'file'));
  const [aiVideo, setAiVideo] = useState<VideoKind>('file');
  const [prompt, setPrompt] = useState(() => params.get('prompt') ?? '');
  const [promptError, setPromptError] = useState<string | null>(null);
  const [fileState, setFileState] = useState<FileState | null>(null);
  const [link, setLink] = useState<LinkState>(EMPTY_LINK);
  const [config, setConfig] = useState<ProjectConfig>(INITIAL_CONFIG);
  const [lastConfig, setLastConfig] = useState<ProjectConfig | null>(null);
  const [preset, setPreset] = useState<{ id: PresetId; name: string; values: PresetValues }>({
    id: FIRST_PRESET?.id ?? 'podcast',
    name: FIRST_PRESET?.title ?? 'Podcast',
    values: presetValuesOf(INITIAL_CONFIG),
  });
  const [openPanel, setOpenPanel] = useState<CustomizePanel | null>(null);
  const [marked, setMarked] = useState<Marked>(null);
  const [upload, setUpload] = useState<UploadProgress | null>(null);
  const [cut, setCut] = useState<CutUpload | null>(null);
  const [submitError, setSubmitError] = useState<ReactNode | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const uploadRef = useRef<{ startedAt: number; loaded: number }>({ startedAt: 0, loaded: 0 });
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const formatRef = useRef<HTMLDivElement>(null);
  const fileToken = useRef(0);
  const touched = useRef(false);

  const set = useCallback((patch: Partial<ProjectConfig>) => {
    touched.current = true;
    setConfig((current) => ({ ...current, ...patch }));
    if ('render_resolution' in patch) setMarked((current) => (current === 'resolution' ? null : current));
    if ('use_seo' in patch) setMarked((current) => (current === 'seo' ? null : current));
  }, []);

  // Remembered configuration: proposed the next time, unless the screen was opened from a project.
  const userId = me?.id;
  useEffect(() => {
    if (!userId) return;
    const stored = loadLastConfig(userId);
    setLastConfig(stored);
    if (stored && !sourceId && !touched.current) {
      setConfig(stored);
      setPreset({ id: 'last', name: 'Mi última configuración', values: presetValuesOf(stored) });
    }
  }, [userId, sourceId]);

  // "Crear de nuevo" / "Duplicar configuración": the project's own configuration is the starting point.
  const sourceData = sourceProject.data;
  useEffect(() => {
    if (!sourceData || touched.current) return;
    const copied = configFromProject(sourceData);
    setConfig(copied);
    setPreset({ id: 'source', name: sourceData.title, values: presetValuesOf(copied) });
  }, [sourceData]);

  /* ── Source ───────────────────────────────────────────────────────────── */
  const sourceKind: VideoKind = tab === 'ai' ? aiVideo : tab;
  const isAi = tab === 'ai';
  const isLink = sourceKind === 'link';
  const busy = create.isPending || create.isSuccess;

  const pickFile = useCallback(
    (file: File) => {
      const token = (fileToken.current += 1);
      setCut(null);
      setSubmitError(null);
      const early = pricing ? checkFile(file, pricing) : null;
      if (early) {
        setFileState({ file, info: { duration: null, width: null, height: null }, problem: early });
        return;
      }
      setFileState({ file, info: null, problem: null });
      void readMedia(file).then((info) => {
        if (fileToken.current !== token) return;
        setFileState({ file, info, problem: null });
      });
    },
    [pricing],
  );

  // Plan and system limits are checked against what the browser read; they follow the plan if it changes.
  const mediaProblem = useMemo(
    () => (fileState?.info && !fileState.problem && pricing ? checkMedia(fileState.info, pricing, plan) : null),
    [fileState, pricing, plan],
  );
  const shownFile = useMemo<FileState | null>(() => (fileState && mediaProblem ? { ...fileState, problem: mediaProblem } : fileState), [fileState, mediaProblem]);

  const url = link.url.trim();
  const debouncedUrl = useDebounced(url, 500);
  const probeToken = useRef(0);
  const { mutateAsync: probe } = probeLink;
  const planRef = useRef({ plan, plans });
  planRef.current = { plan, plans };
  useEffect(() => {
    const token = (probeToken.current += 1);
    if (!debouncedUrl) return;
    if (!isUrl(debouncedUrl)) {
      setLink((current) => (current.url.trim() === debouncedUrl ? { ...current, status: 'error', probe: undefined, error: { message: 'Pegá el link completo, empezando por https://' } } : current));
      return;
    }
    setLink((current) => (current.url.trim() === debouncedUrl ? { ...current, status: 'probing', probe: undefined, error: undefined } : current));
    probe(debouncedUrl).then(
      (result) => {
        if (probeToken.current !== token) return;
        setLink((current) => (current.url.trim() === debouncedUrl ? { ...current, status: 'ok', probe: result, error: undefined } : current));
      },
      (error: unknown) => {
        if (probeToken.current !== token) return;
        const current = planRef.current;
        let failure: NonNullable<LinkState['error']> = { message: errorMessage(error, 'No pudimos revisar ese link. Probá de nuevo.') };
        if (error instanceof ApiError && error.status === 403 && error.code === 'duration') {
          const limit = typeof error.data.limit === 'number' ? error.data.limit : current.plan?.max_video_duration_seconds;
          failure = { message: uploadErrorMessage('too_long', { maxSeconds: limit }), hint: <LongerPlanHint plan={current.plan} plans={current.plans} /> };
        } else if (error instanceof ApiError && error.status === 400) {
          failure =
            error.code === 'unavailable'
              ? { message: 'No pudimos acceder a ese video: es privado, no existe o pide iniciar sesión.' }
              : { message: 'Todavía no podemos traer videos de ese sitio. Probá con un link de YouTube o Vimeo, o subí el archivo.' };
        }
        setLink((state) => (state.url.trim() === debouncedUrl ? { ...state, status: 'error', probe: undefined, error: failure } : state));
      },
    );
  }, [debouncedUrl, probe]);

  const changeLink = useCallback((next: string) => {
    setSubmitError(null);
    setLink(next.trim() ? { url: next, status: 'idle' } : EMPTY_LINK);
  }, []);

  const duration = isLink ? (link.status === 'ok' ? (link.probe?.duration_seconds ?? null) : null) : (shownFile?.info?.duration ?? null);
  const fileReady = Boolean(shownFile && shownFile.info && !shownFile.problem);
  const sourceReady = isLink ? link.status === 'ok' : fileReady;
  const source = useMemo<ProjectSourceInput | null>(() => {
    if (isLink) return link.status === 'ok' && link.probe ? { kind: 'link', url: link.probe.video_url, title: link.probe.title } : null;
    return shownFile && shownFile.info && !shownFile.problem ? { kind: 'file', file: shownFile.file, title: titleFromFileName(shownFile.file.name) } : null;
  }, [isLink, link, shownFile]);

  /* ── Effective configuration: what the plan and the source allow ───────── */
  const singleMax = pricing?.single_edit_max_duration_seconds ?? 600;
  const singleBlock: SingleEditBlock = isLink ? 'link' : duration !== null && duration > singleMax ? 'long' : null;
  const defaultKit = brandKits.find((item) => item.is_default) ?? null;
  const kit = config.brand_kit === undefined ? defaultKit : (brandKits.find((item) => item.id === config.brand_kit) ?? null);
  const seoAllowed = plan ? plan.has_seo_optimization : true;
  const effective = useMemo<ProjectConfig>(
    () => ({
      ...config,
      project_type: singleBlock ? 'repurpose' : config.project_type,
      render_resolution: isLink || !planAllowsResolution(plan, config.render_resolution) ? '720p' : config.render_resolution,
      use_seo: seoAllowed && config.use_seo,
      brand_kit: kit?.id ?? null,
    }),
    [config, singleBlock, isLink, plan, seoAllowed, kit],
  );
  const single = effective.project_type === 'single_edit';

  /* ── Cost ─────────────────────────────────────────────────────────────── */
  const available = wallet.data?.available_balance ?? null;
  const estimateFor = useCallback(
    (resolution: RenderResolution): CostEstimate | null => {
      if (!pricing || !sourceReady) return null;
      if (isLink) return estimateCost({ durationSeconds: 0, resolution: '720p', useSeo: effective.use_seo, source: 'link' }, pricing, plan);
      if (duration === null) return null;
      return estimateCost({ durationSeconds: duration, resolution, useSeo: effective.use_seo, source: 'file' }, pricing, plan);
    },
    [pricing, sourceReady, isLink, duration, effective.use_seo, plan],
  );
  const allowedResolutions = RESOLUTIONS.filter((resolution) => planAllowsResolution(plan, resolution));
  const topResolution = allowedResolutions[allowedResolutions.length - 1] ?? '720p';
  const estimate = estimateFor(isAi ? '720p' : effective.render_resolution);
  const estimateTop = isAi && !isLink ? estimateFor(topResolution) : null;
  const ranged = Boolean(estimate && estimateTop && estimateTop.total > estimate.total);
  const required = estimate?.total ?? null;
  const missing = required !== null && available !== null && required > available ? Math.round((required - available) * 100) / 100 : null;

  let cost: CostView;
  if (!sourceReady) {
    const tooLong = shownFile?.problem?.code === 'too_long';
    cost = {
      kind: 'none',
      reason: tooLong
        ? 'Lo calculamos cuando elijas un video que entre en tu plan.'
        : isLink
          ? 'Lo calculamos cuando reconozcamos el link.'
          : shownFile?.problem
            ? 'Lo calculamos cuando elijas un video que podamos leer.'
            : 'Elegí el video para ver cuánto cuesta.',
    };
  } else if (!estimate) {
    cost = { kind: 'none', reason: 'No pudimos leer la duración en este navegador. Te mostramos el costo al crear el proyecto.' };
  } else if (ranged && estimateTop) {
    cost = { kind: 'range', min: estimate.total, max: estimateTop.total, note: 'Según la resolución que pida tu texto. El costo final queda en el proyecto.' };
  } else {
    cost = { kind: 'exact', total: estimate.total };
  }

  const eta = useEstimate(
    estimate && !isLink && duration !== null
      ? { source: 'file', duration_seconds: Math.floor(duration), render_resolution: isAi ? '720p' : effective.render_resolution, use_seo: effective.use_seo, max_clips: effective.max_clips }
      : null,
  );

  const planDiscount = plan && plan.base_discount_rate > 0 ? `Descuento ${plan.name} ${formatPercent(plan.base_discount_rate)}` : null;
  const detail: SummaryRow[] = [];
  if (estimate && pricing) {
    if (isLink) {
      detail.push({ key: 'flat', label: 'Proyecto desde un link', value: <span className="num">{formatNumber(estimate.base)}</span> });
      if (effective.use_seo) detail.push({ key: 'seo', label: planDiscount ? `SEO con ${planDiscount.toLowerCase()}` : 'SEO', value: <span className="num">+{formatNumber(estimate.addOns)}</span> });
    } else if (duration !== null) {
      const seconds = Math.floor(duration);
      detail.push({
        key: 'base',
        label: (
          <span className="num">
            {formatNumber(seconds, 0)} s × {formatNumber(pricing.base_cost_per_second_720p)}
          </span>
        ),
        value: <span className="num">{formatNumber(seconds * pricing.base_cost_per_second_720p)}</span>,
      });
      if (!ranged) {
        detail.push({
          key: 'res',
          label: (
            <>
              Resolución <span className="num">{effective.render_resolution}</span>
            </>
          ),
          value: <span className="num">×{formatNumber(estimate.multiplier)}</span>,
        });
      }
      if (effective.use_seo) detail.push({ key: 'seo', label: 'SEO', value: <span className="num">+{formatNumber(estimate.addOns)}</span> });
      if (!ranged) {
        detail.push(
          planDiscount
            ? { key: 'discount', label: planDiscount, value: <span className="num">−{formatNumber(estimate.discount)}</span> }
            : { key: 'discount', label: 'Descuento del plan', value: <span className="muted">No incluye</span> },
        );
      } else {
        if (planDiscount) detail.push({ key: 'discount', label: planDiscount, value: <span className="muted">incluido</span> });
        for (const resolution of allowedResolutions) {
          const item = estimateFor(resolution);
          if (item) {
            detail.push({
              key: `en-${resolution}`,
              label: (
                <>
                  En <span className="num">{resolution}</span>
                </>
              ),
              value: <span className="num">{formatNumber(item.total)}</span>,
            });
          }
        }
      }
    }
  }

  /* ── Summary rows ─────────────────────────────────────────────────────── */
  const videoName = isLink ? (link.status === 'ok' ? (link.probe?.title ?? null) : null) : (shownFile?.file.name ?? null);
  const tooLongLimit = shownFile?.problem?.code === 'too_long' ? (shownFile.problem.limitSeconds ?? null) : null;
  const provider = isLink ? linkProvider(link.probe?.video_url ?? url) : null;
  const seoCost = pricing?.feature_costs.seo_optimization ?? 5;
  const rows: SummaryRow[] = [
    { key: 'video', label: 'Video', value: videoName ? <span className={styles.strong}>{videoName}</span> : <span className="muted">Sin elegir</span> },
    ...(isLink && link.status === 'ok' ? [{ key: 'origin', label: 'Origen', value: provider ? `Link de ${provider}` : 'Link' }] : []),
    {
      key: 'duration',
      label: 'Duración',
      value:
        duration === null ? (
          <span className="muted">—</span>
        ) : tooLongLimit !== null ? (
          <>
            <span className={cx('num', styles.errText)}>{formatDuration(duration)}</span> <span className="num muted">· máx. {formatDuration(tooLongLimit)}</span>
          </>
        ) : (
          <span className="num">{formatDuration(duration)}</span>
        ),
    },
    { key: 'resolution', label: 'Resolución', value: isAi && !isLink ? 'La define tu texto' : <span className="num">{effective.render_resolution}</span> },
    {
      key: 'extras',
      label: 'Extras',
      value: effective.use_seo ? (
        <span>
          SEO <span className="num muted">+{formatTokens(seoCost)}</span>
        </span>
      ) : (
        <span className="muted">{isAi ? 'Ninguno: SEO apagado' : 'Ninguno'}</span>
      ),
    },
    ...(plan ? [{ key: 'plan', label: 'Plan', value: `${plan.name} · ${plan.has_watermark ? 'con' : 'sin'} marca de agua` }] : []),
  ];
  const watermarkPlan = plan?.has_watermark ? (firstPlanWith(plans, (item) => !item.has_watermark)?.name ?? null) : undefined;

  /* ── Create ───────────────────────────────────────────────────────────── */
  const openPlanField = useCallback((kind: ReturnType<typeof planLimitKind>) => {
    if (kind === 'resolution') {
      setMarked('resolution');
      formatRef.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' });
    } else if (kind === 'seo') {
      setMarked('seo');
      setOpenPanel('intelligence');
    } else if (kind === 'duration') {
      setFileState((current) => (current ? { ...current, problem: { code: 'too_long', byPlan: true } } : current));
    }
  }, []);

  const submit = () => {
    if (busy) return;
    setSubmitError(null);
    setMarked(null);
    setCut(null);
    const text = prompt.trim();
    if (isAi && !text) {
      setPromptError('Contanos qué querés');
      promptRef.current?.focus();
      return;
    }
    if (!source || !userId) return;
    const body = isAi ? buildPromptBody(source, effective.use_seo) : buildCreateBody(source, effective);
    const controller = new AbortController();
    abortRef.current = controller;
    const uploading = source.kind === 'file';
    if (uploading) {
      uploadRef.current = { startedAt: Date.now(), loaded: 0 };
      setUpload({ loaded: 0, total: source.file.size, speed: 0 });
    }
    create.mutate(
      {
        body,
        prompt: isAi ? text : undefined,
        signal: controller.signal,
        onUploadProgress: uploading
          ? (loaded, total) => {
              const elapsed = (Date.now() - uploadRef.current.startedAt) / 1000;
              uploadRef.current.loaded = loaded;
              setUpload({ loaded, total, speed: elapsed > 0.5 ? loaded / elapsed : 0 });
            }
          : undefined,
      },
      {
        onSuccess: (project) => {
          if (!isAi) saveLastConfig(userId, effective);
          router.push(`/proyectos/${project.id}`);
        },
        onError: (error) => {
          const sent = uploadRef.current.loaded;
          const size = uploading ? source.file.size : 0;
          setUpload(null);
          if (isAbort(error)) {
            toast.show('Cancelaste la subida. No se creó el proyecto.', { tone: 'info' });
            return;
          }
          if (billing.handleError(error, { concept: 'Costo del proyecto', onChangeOptions: () => openPlanField(planLimitKind(error)) })) return;
          if (error instanceof NetworkError) {
            if (uploading) setCut({ loaded: sent, percent: size > 0 ? Math.min(99, Math.floor((sent / size) * 100)) : 0 });
            else setSubmitError('No pudimos conectar. Revisá tu conexión y probá de nuevo.');
            return;
          }
          if (error instanceof ApiError && error.status === 400) {
            const typeError = error.fieldErrors.project_type?.[0];
            const promptIssue = error.fieldErrors.prompt?.[0];
            const linkIssue = error.fieldErrors.video_url?.[0];
            const code = uploadErrorCode(error);
            if (typeError) setSubmitError(typeError);
            else if (promptIssue) setPromptError(/obligatorio|blank|required/i.test(promptIssue) ? 'Contanos qué querés' : promptIssue);
            else if (linkIssue) setLink((current) => ({ ...current, status: 'error', probe: undefined, error: { message: linkIssue } }));
            else if (code && uploading) setFileState((current) => (current ? { ...current, problem: { code } } : current));
            else setSubmitError(errorMessage(error, 'No pudimos crear el proyecto. Probá de nuevo.'));
            return;
          }
          setSubmitError(errorMessage(error, 'No pudimos crear el proyecto. Probá de nuevo.'));
        },
      },
    );
  };

  const cancel = () => abortRef.current?.abort();
  useBeforeUnload(create.isPending);
  // Leaving the screen stops an upload that nobody is waiting for.
  useEffect(() => {
    const ref = abortRef;
    return () => ref.current?.abort();
  }, []);

  const roleBlocked = !can('project.create');
  let blocked: ReactNode | null = null;
  if (roleBlocked) blocked = 'Tu rol no puede crear proyectos. Pedile acceso a un admin.';
  else if (shownFile?.problem?.code === 'too_long' && !isLink) {
    blocked = (
      <>
        El video supera el máximo de tu plan. Elegí otro archivo o{' '}
        <Link className="lnk" href="/uso/planes">
          mirá los planes
        </Link>
        .
      </>
    );
  } else if (!isLink && shownFile?.problem) blocked = 'Elegí otro archivo para seguir.';
  else if (!isLink && shownFile && !shownFile.info) blocked = 'Estamos leyendo el video…';
  else if (!sourceReady) blocked = isLink ? (link.status === 'probing' ? 'Estamos buscando el video…' : 'Pegá el link del video para seguir.') : 'Elegí el video para seguir.';

  const phase = create.isSuccess ? 'opening' : create.isPending ? (upload ? 'uploading' : 'creating') : 'idle';
  const summary: SummaryProps = {
    rows,
    watermarkPlan,
    cost,
    detail,
    available,
    after: required !== null && available !== null && missing === null ? { label: ranged ? <>Saldo después, en <span className="num">720p</span></> : 'Saldo después', value: available - required } : null,
    missing: blocked ? null : missing,
    required,
    etaSeconds: eta.data?.eta_seconds ?? null,
    note: (
      <>
        {isLink
          ? 'Los proyectos por link tienen un precio fijo. '
          : duration !== null
            ? 'Estimación con la duración que leímos de tu archivo. '
            : ''}
        Reservamos los tokens al crear y, si el proyecto falla, te los devolvemos.
      </>
    ),
    cta: {
      phase,
      upload,
      onCreate: submit,
      onCancel: cancel,
      blocked,
      roleReason: gate('project.create').disabledReason,
      canBuy: can('billing.manage'),
      hint: isAi
        ? 'La IA arma la configuración con tu texto. La ves en el proyecto.'
        : single || effective.auto_render_bypass
          ? 'Se renderiza sin pasar por la revisión.'
          : 'No se renderiza nada hasta que apruebes los clips.',
      error: submitError,
    },
  };

  /* ── Presets ──────────────────────────────────────────────────────────── */
  const presetCards: Array<{ id: PresetId; title: string; lines: [string, string] }> = [
    ...(sourceData ? [{ id: 'source' as const, title: `Como «${sourceData.title}»`, lines: [styleLine(configFromProject(sourceData)), subtitlesLine(configFromProject(sourceData))] as [string, string] }] : []),
    ...(lastConfig ? [{ id: 'last' as const, title: 'Mi última configuración', lines: [styleLine(lastConfig), subtitlesLine(lastConfig)] as [string, string] }] : []),
    ...PRESETS.map((item) => ({ id: item.id, title: item.title, lines: [styleLine(item.values), item.subtitles] as [string, string] })),
  ];
  const pickPreset = (id: string) => {
    touched.current = true;
    if (id === 'last' && lastConfig) {
      setConfig((current) => ({ ...lastConfig, project_type: current.project_type, selection_criteria: current.selection_criteria }));
      setPreset({ id: 'last', name: 'Mi última configuración', values: presetValuesOf(lastConfig) });
      return;
    }
    if (id === 'source' && sourceData) {
      const copied = configFromProject(sourceData);
      setConfig((current) => ({ ...copied, project_type: current.project_type, selection_criteria: current.selection_criteria }));
      setPreset({ id: 'source', name: sourceData.title, values: presetValuesOf(copied) });
      return;
    }
    const chosen = PRESETS.find((item) => item.id === id);
    if (!chosen) return;
    setConfig((current) => ({ ...current, ...chosen.values }));
    setPreset({ id: chosen.id, name: chosen.title, values: chosen.values });
  };

  const formats = pricing ? extensionsLabel(pricing.upload_extensions) : 'MP4 o MOV';
  const fileProps = { state: shownFile, onPick: pickFile, cut, onRetry: submit, busy, plan, plans, pricing };
  const linkProps = { state: link, onChange: changeLink, busy };

  return (
    <Page flush={mobile}>
      <Crumbs items={[{ label: 'Proyectos', href: '/proyectos' }, { label: 'Nuevo proyecto' }]} />
      <PageHead className={styles.head} title="Nuevo proyecto" description="Todo en una pantalla: elegí el video, ajustá lo que quieras y creá el proyecto." />
      {roleBlocked ? (
        <Banner className={styles.topBanner} tone="warn" title="Tu rol no puede crear proyectos">
          Podés ver las opciones, pero crear un proyecto gasta tokens. Pedile acceso a un admin.
        </Banner>
      ) : null}
      {pricingQuery.isError ? <LoadError className={styles.topBanner} error={pricingQuery.error} onRetry={() => void pricingQuery.refetch()} fullPageForMissing={false} /> : null}
      {sourceId && sourceProject.isError ? (
        <Banner className={styles.topBanner} tone="warn" title="No pudimos traer la configuración de ese proyecto">
          Seguís con tu última configuración.
        </Banner>
      ) : null}
      <div className={styles.grid}>
        <form
          className={styles.form}
          aria-label="Configuración del proyecto"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <OriginSection
            tab={tab}
            onTab={(next) => {
              setTab(next);
              setSubmitError(null);
            }}
            aiVideo={aiVideo}
            onAiVideo={setAiVideo}
            prompt={prompt}
            onPrompt={(value) => {
              setPrompt(value);
              if (promptError) setPromptError(null);
            }}
            promptError={promptError}
            promptMax={pricing?.prompt_max_chars ?? 1000}
            promptRef={promptRef}
            file={fileProps}
            link={linkProps}
            formats={formats}
          />

          {isAi ? (
            <Section id="np-config" title="Configuración" hint="La arma la IA a partir de tu texto.">
              <div className={styles.aiNote}>
                <ol className={styles.aiSteps}>
                  <li>
                    <span className={cx('num', styles.aiN)} aria-hidden="true">
                      1
                    </span>
                    <span>{isLink ? 'Traemos el video y leemos lo que escribiste.' : 'Subimos el video y leemos lo que escribiste.'}</span>
                  </li>
                  <li>
                    <span className={cx('num', styles.aiN)} aria-hidden="true">
                      2
                    </span>
                    <span>La IA define formato, resolución, clips, estilo, subtítulos e inteligencia.</span>
                  </li>
                  <li>
                    <span className={cx('num', styles.aiN)} aria-hidden="true">
                      3
                    </span>
                    <span className="fg">En el proyecto ves qué aplicó y qué no pudo aplicar, con el motivo.</span>
                  </li>
                </ol>
              </div>
              <div className={styles.swCard}>
                <div className={styles.swRow}>
                  <div className={styles.swText}>
                    <span className={cx(styles.rcTitle, styles.row)}>
                      SEO{' '}
                      {seoAllowed ? (
                        <Badge tone="outline" size="sm">
                          <span className="num">+{formatTokens(seoCost)}</span>
                        </Badge>
                      ) : null}
                    </span>
                    <span className={styles.rcDesc}>
                      {seoAllowed
                        ? `${effective.use_seo ? 'Prendido' : 'Apagado'}. El SEO no se activa desde el texto: si lo querés, prendelo acá.`
                        : 'Títulos, descripciones y hashtags pensados para cada red.'}
                    </span>
                    {seoAllowed ? null : (
                      <span className={styles.lockLine}>
                        <Icon name="lock" size={12} />
                        <span>
                          Disponible en el plan {firstPlanWith(plans, (item) => item.has_seo_optimization)?.name ?? 'Creador'} ·{' '}
                          <Link className="lnk" href="/uso/planes">
                            Ver planes
                          </Link>
                        </span>
                      </span>
                    )}
                  </div>
                  <Switch ariaLabel={seoAllowed ? `SEO, suma ${formatTokens(seoCost)}` : 'SEO, no disponible en tu plan'} checked={effective.use_seo} disabled={!seoAllowed} onChange={(value) => set({ use_seo: value })} />
                </div>
              </div>
            </Section>
          ) : (
            <>
              <WhatSection value={effective.project_type} onChange={(value) => set({ project_type: value })} block={singleBlock} maxSeconds={singleMax} />
              <Section id="np-preset" title="Punto de partida" hint="Define estilo, layout, subtítulos e inteligencia. Después podés personalizar cada uno.">
                <RadioGroup
                  ariaLabel="Punto de partida"
                  value={matchesPreset(config, preset.values) ? preset.id : undefined}
                  onChange={pickPreset}
                  layout="cards"
                  columns={presetCards.length}
                  className={styles.presets}
                >
                  {presetCards.map((card) => (
                    <RadioCard
                      key={card.id}
                      value={card.id}
                      title={card.title}
                      description={
                        <>
                          <span className={styles.presetLine}>{card.lines[0]}</span>
                          <span className={styles.presetLine}>{card.lines[1]}</span>
                        </>
                      }
                    />
                  ))}
                </RadioGroup>
              </Section>
              <div ref={formatRef}>
                <FormatSection config={effective} set={set} plan={plan} plans={plans} pricing={pricing} duration={duration} isLink={isLink} marked={marked === 'resolution'} />
              </div>
              {single ? null : <ClipsSection config={effective} set={set} maxCriteria={pricing?.selection_criteria_max_chars ?? 500} />}
              <CustomizeSection
                config={effective}
                set={set}
                presetName={preset.name}
                modified={!matchesPreset(config, preset.values)}
                open={openPanel}
                onOpen={setOpenPanel}
                brandKits={brandKits}
                kit={kit}
                plan={plan}
                plans={plans}
                seoCost={seoCost}
                seoMarked={marked === 'seo'}
              />
            </>
          )}
          {mobile ? <p className={styles.hint}>{summary.note}</p> : null}
        </form>
        {mobile ? null : pricingQuery.isPending ? (
          <aside className={styles.side} aria-label="Resumen del proyecto">
            <Card padded>
              <div role="status" aria-label="Cargando el resumen" aria-busy="true" className={styles.sideSkel}>
                <Skeleton width="40%" height={18} />
                <Skeleton height={12} />
                <Skeleton height={12} width="70%" />
                <Skeleton height={32} width="55%" />
                <Skeleton height={44} />
              </div>
            </Card>
          </aside>
        ) : (
          <SummaryCard {...summary} />
        )}
      </div>
      {mobile ? <SummaryBar {...summary} /> : null}
    </Page>
  );
}
