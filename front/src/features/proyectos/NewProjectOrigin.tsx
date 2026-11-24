'use client';

import Link from 'next/link';
import { useId, useRef, type ReactNode } from 'react';
import type { Plan, Pricing } from '@/api/contracts/billing';
import type { LinkProbe } from '@/api/contracts/projects';
import { uploadErrorMessage } from '@/lib/error-messages';
import { formatBytes, formatDuration, formatMinutes } from '@/lib/format';
import { firstPlanWith } from '@/lib/pricing';
import { Button, FileDropZone, Icon, Input, ProgressBar, SegmentedControl, Spinner, TabList, Textarea, VideoThumb, cx, useToast } from '@/ui';
import styles from './NewProject.module.css';
import { extensionsLabel, type FileProblem, type MediaInfo } from './media';

export type OriginTab = 'file' | 'link' | 'ai';
export type VideoKind = 'file' | 'link';

export interface FileState {
  file: File;
  /** null while the browser reads the metadata. */
  info: MediaInfo | null;
  problem: FileProblem | null;
}

export interface LinkState {
  url: string;
  status: 'idle' | 'probing' | 'ok' | 'error';
  probe?: LinkProbe;
  error?: { message: string; hint?: ReactNode };
}

export interface CutUpload {
  percent: number;
  loaded: number;
}

/** Message and help of a file problem (map §4), next to the file field. */
export function describeFileProblem(problem: FileProblem, plan: Plan | null, plans: Plan[], pricing: Pricing | null, duration: number | null): { message: string; hint?: ReactNode } {
  if (problem.code === 'too_heavy') {
    return { message: `El archivo pesa más de ${formatBytes(problem.limitBytes)}.`, hint: 'Exportalo con menos peso y volvé a subirlo.' };
  }
  if (problem.code === 'not_a_video') {
    return { message: uploadErrorMessage('not_a_video'), hint: pricing ? `Aceptamos ${extensionsLabel(pricing.upload_extensions)}.` : undefined };
  }
  if (problem.code === 'too_long') {
    const limit = problem.limitSeconds ?? plan?.max_video_duration_seconds;
    return { message: uploadErrorMessage('too_long', { maxSeconds: limit }), hint: <LongerPlanHint plan={plan} plans={plans} duration={duration} /> };
  }
  return { message: uploadErrorMessage(problem.code) };
}

/** "El plan Estudio acepta videos de hasta 4 h. Ver planes" */
export function LongerPlanHint({ plan, plans, duration }: { plan: Plan | null; plans: Plan[]; duration?: number | null }) {
  const better = plan ? firstPlanWith(plans, (item) => item.max_video_duration_seconds > Math.max(plan.max_video_duration_seconds, duration ?? 0)) : null;
  return (
    <>
      {duration ? (
        <>
          Dura <span className="num">{formatDuration(duration)}</span>.{' '}
        </>
      ) : null}
      {better ? (
        <>
          El plan {better.name} acepta videos de hasta <span className="num">{formatMinutes(better.max_video_duration_seconds)}</span>.{' '}
        </>
      ) : null}
      <Link className="lnk" href="/uso/planes">
        Ver planes
      </Link>
    </>
  );
}

function ErrorLine({ children }: { children: ReactNode }) {
  return (
    <span className={styles.errline} role="alert">
      <Icon name="error" size={14} />
      <span>{children}</span>
    </span>
  );
}

export interface FilePickerProps {
  state: FileState | null;
  onPick: (file: File) => void;
  cut: CutUpload | null;
  onRetry: () => void;
  busy: boolean;
  plan: Plan | null;
  plans: Plan[];
  pricing: Pricing | null;
}

/** The file field: drop zone, then the chosen file with its check, its error or the upload that was cut. */
export function FilePicker({ state, onPick, cut, onRetry, busy, plan, plans, pricing }: FilePickerProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const accept = pricing?.upload_extensions.join(',') ?? 'video/*';
  const formats = pricing ? extensionsLabel(pricing.upload_extensions) : 'MP4 o MOV';

  if (!state) {
    return (
      <FileDropZone
        accept={accept}
        onFiles={(files) => files[0] && onPick(files[0])}
        icon="upload"
        title="Arrastrá tu video acá"
        description={`o elegilo desde tu dispositivo · ${formats}${pricing ? `, hasta ${formatBytes(pricing.upload_max_size_bytes)}` : ''}`}
        inputLabel="Elegir video"
      />
    );
  }

  const { file, info, problem } = state;
  const duration = info?.duration ?? null;
  const described = problem ? describeFileProblem(problem, plan, plans, pricing, duration) : null;
  const failed = Boolean(described || cut);
  const notVideo = problem?.code === 'not_a_video' || problem?.code === 'unreadable' || problem?.code === 'no_duration' || problem?.code === 'no_video_stream';
  const change = (
    <Button size="sm" variant="secondary" disabled={busy} onClick={() => inputRef.current?.click()}>
      {failed ? 'Elegir otro archivo' : 'Cambiar'}
    </Button>
  );

  return (
    <div className={cx(styles.fileRow, failed && styles.isError)} data-testid="file-row">
      <input
        ref={inputRef}
        type="file"
        className="sr-only"
        tabIndex={-1}
        aria-label="Elegir otro video"
        accept={accept}
        onChange={(event) => {
          const next = event.target.files?.[0];
          event.target.value = '';
          if (next) onPick(next);
        }}
      />
      {notVideo ? (
        <span className={styles.fileIco} aria-hidden="true">
          <Icon name="file" size={20} />
        </span>
      ) : (
        <VideoThumb className={styles.fileThumb} seed={file.name} duration={duration} />
      )}
      <div className={styles.fileMain}>
        <span className={styles.fileName} title={file.name}>
          {file.name}
        </span>
        <span className="num t-small muted">
          {cut ? `${formatBytes(cut.loaded)} de ` : ''}
          {formatBytes(file.size)}
          {duration !== null ? ` · ${formatDuration(duration)}` : ''}
        </span>
        {cut ? (
          <>
            <ErrorLine>
              Se cortó la subida al <span className="num">{cut.percent} %</span>.
            </ErrorLine>
            <span className={styles.hint}>Revisá tu conexión y volvé a intentar. No se creó el proyecto ni se reservaron tokens.</span>
          </>
        ) : described ? (
          <>
            <ErrorLine>{described.message}</ErrorLine>
            {described.hint ? <span className={styles.hint}>{described.hint}</span> : null}
          </>
        ) : info === null ? (
          <span className={styles.readLine} role="status">
            <Spinner size={12} /> Leyendo el video…
          </span>
        ) : duration === null ? (
          <span className={styles.hint}>No pudimos leer la duración en este navegador. La medimos cuando lo subas.</span>
        ) : plan ? (
          <span className={styles.okline}>
            <Icon name="check" size={14} />
            <span>
              Entra en tu plan {plan.name}: hasta <span className="num">{formatMinutes(plan.max_video_duration_seconds)}</span> por video
            </span>
          </span>
        ) : null}
      </div>
      {cut ? (
        <div className={styles.fileActs}>
          <Button size="sm" variant="primary" iconLeft="retry" onClick={onRetry}>
            Reintentar
          </Button>
          {change}
        </div>
      ) : (
        change
      )}
      {cut ? <ProgressBar className={styles.fileProgress} tone="neutral" value={cut.percent} label="Subida detenida" /> : null}
    </div>
  );
}

export interface LinkFieldProps {
  state: LinkState;
  onChange: (url: string) => void;
  busy: boolean;
}

/** "Pegar link": the link is recognised (site, title and duration) before creating. */
export function LinkField({ state, onChange, busy }: LinkFieldProps) {
  const id = useId();
  const toast = useToast();
  const paste = async () => {
    try {
      const text = (await navigator.clipboard.readText()).trim();
      if (text) onChange(text);
    } catch {
      toast.show('No pudimos leer el portapapeles. Pegá el link con Ctrl+V.', { tone: 'info' });
    }
  };
  const probe = state.status === 'ok' ? state.probe : undefined;
  const invalid = state.status === 'error';
  return (
    <>
      <div className={styles.field}>
        <label className={styles.fieldLabel} htmlFor={id}>
          Link del video
        </label>
        <div className={styles.linkRow}>
          <Input
            id={id}
            type="url"
            inputMode="url"
            autoComplete="off"
            value={state.url}
            invalid={invalid}
            disabled={busy}
            aria-describedby={`${id}-help`}
            onChange={(event) => onChange(event.target.value)}
            placeholder="https://youtube.com/watch?v=…"
          />
          <Button variant="secondary" onClick={() => void paste()} disabled={busy}>
            Pegar
          </Button>
        </div>
        <span id={`${id}-help`} className={styles.helpStack}>
          {invalid && state.error ? (
            <>
              <ErrorLine>{state.error.message}</ErrorLine>
              {state.error.hint ? <span className={styles.hint}>{state.error.hint}</span> : null}
            </>
          ) : state.status === 'probing' ? (
            <span className={styles.readLine} role="status">
              <Spinner size={12} /> Buscando el video…
            </span>
          ) : (
            <span className={styles.hint}>YouTube, Vimeo y otros sitios públicos.</span>
          )}
        </span>
      </div>
      {probe ? (
        <div className={styles.fileRow} data-testid="link-row">
          <VideoThumb className={styles.fileThumb} seed={probe.video_url} src={probe.thumbnail_url} duration={probe.duration_seconds} />
          <div className={styles.fileMain}>
            <span className={styles.fileName} title={probe.title}>
              {probe.title}
            </span>
            <span className={cx('num t-small muted', styles.row)}>
              <Icon name="link" size={12} />
              <span>
                {probe.provider} · {formatDuration(probe.duration_seconds)}
              </span>
            </span>
          </div>
          <Button size="sm" variant="secondary" disabled={busy} onClick={() => onChange('')}>
            Quitar
          </Button>
        </div>
      ) : null}
    </>
  );
}

export interface OriginProps {
  tab: OriginTab;
  onTab: (tab: OriginTab) => void;
  /** Inside "Describir con IA": where the video comes from. */
  aiVideo: VideoKind;
  onAiVideo: (kind: VideoKind) => void;
  prompt: string;
  onPrompt: (value: string) => void;
  promptError: string | null;
  promptMax: number;
  promptRef: React.RefObject<HTMLTextAreaElement | null>;
  file: FilePickerProps;
  link: LinkFieldProps;
  formats: string;
}

const TAB_ITEMS: Array<{ value: OriginTab; label: ReactNode }> = [
  {
    value: 'file',
    label: (
      <>
        <Icon name="upload" size={14} /> Subir archivo
      </>
    ),
  },
  {
    value: 'link',
    label: (
      <>
        <Icon name="link" size={14} /> Pegar link
      </>
    ),
  },
  {
    value: 'ai',
    label: (
      <>
        <Icon name="sparkles" size={14} /> Describir con IA
      </>
    ),
  },
];

const ORIGIN_HINT: Record<OriginTab, (formats: string) => string> = {
  file: (formats) => `El video largo del que salen los clips. ${formats}.`,
  link: () => 'El video largo del que salen los clips. Lo traemos nosotros: no hace falta descargarlo.',
  ai: () => 'Escribí qué querés y sumá el video.',
};

/** Section 1 of C1: the three origins. The chosen file and the pasted link are kept when changing tabs. */
export function OriginSection({ tab, onTab, aiVideo, onAiVideo, prompt, onPrompt, promptError, promptMax, promptRef, file, link, formats }: OriginProps) {
  const promptId = useId();
  return (
    <section className={styles.sec} aria-labelledby="np-origen">
      <div className={styles.secHead}>
        <h2 className="t-h3" id="np-origen">
          Origen
        </h2>
        <p className="t-small muted">{ORIGIN_HINT[tab](formats)}</p>
      </div>
      <TabList items={TAB_ITEMS} value={tab} onChange={onTab} ariaLabel="Origen del video" idBase="np-origen" className={styles.srcTabs} />
      <div role="tabpanel" id={`np-origen-panel-${tab}`} aria-labelledby={`np-origen-tab-${tab}`} className={styles.origin}>
        {tab === 'file' ? <FilePicker {...file} /> : null}
        {tab === 'link' ? <LinkField {...link} /> : null}
        {tab === 'ai' ? (
          <>
            <div className={styles.field}>
              <label className={styles.fieldLabel} htmlFor={promptId}>
                Contanos qué querés
              </label>
              <div className={cx(styles.promptBox, promptError && styles.isError)}>
                <Textarea
                  ref={promptRef}
                  id={promptId}
                  className={styles.promptArea}
                  maxLength={promptMax}
                  value={prompt}
                  invalid={Boolean(promptError)}
                  aria-describedby={`${promptId}-help`}
                  onChange={(event) => onPrompt(event.target.value)}
                  placeholder="Sacá 5 clips de 30 s de las partes más graciosas, subtítulos grandes amarillos"
                />
                <div className={styles.promptFoot}>
                  <span className={styles.hint} id={`${promptId}-help`}>
                    Podés pedir cantidad y duración de clips, formato, resolución, estilo, subtítulos y seguimiento de caras.
                  </span>
                  <span className={cx('num', styles.hint, styles.nowrap)} aria-label={`${prompt.length} de ${promptMax} caracteres`}>
                    {prompt.length} / {promptMax}
                  </span>
                </div>
              </div>
              {promptError ? <ErrorLine>{promptError}</ErrorLine> : null}
            </div>
            <div className={styles.field}>
              <div className={cx(styles.row, styles.between)}>
                <span className={styles.fieldLabel}>Video</span>
                <SegmentedControl<VideoKind>
                  ariaLabel="De dónde sale el video"
                  value={aiVideo}
                  onChange={onAiVideo}
                  options={[
                    { value: 'file', label: 'Archivo' },
                    { value: 'link', label: 'Link' },
                  ]}
                />
              </div>
              {aiVideo === 'file' ? <FilePicker {...file} /> : <LinkField {...link} />}
            </div>
          </>
        ) : null}
      </div>
    </section>
  );
}
