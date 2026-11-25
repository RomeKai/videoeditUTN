'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useId, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react';
import type { BrandKit } from '@/api/contracts/brandkits';
import { ApiError } from '@/api/errors';
import { useBrandKit, useCreateBrandKit, useDeleteBrandKit, useUpdateBrandKit } from '@/api/hooks';
import { ConfirmDialog, UnsavedChangesDialog } from '@/dialogs';
import { errorMessage } from '@/lib/error-messages';
import { formatBytes, formatDuration } from '@/lib/format';
import { LoadError } from '@/shell/LoadError';
import { usePermissions, usePlan } from '@/shell/session';
import {
  ActionBar,
  Badge,
  Banner,
  Button,
  Card,
  CardHead,
  Crumbs,
  Field,
  Icon,
  Input,
  Num,
  Page,
  SegmentedControl,
  Skeleton,
  Switch,
  Textarea,
  cx,
  thumbHue,
  useToast,
} from '@/ui';
import styles from './BrandKits.module.css';
import { KitPreview, LogoMark, useFontFace, useObjectUrl, type PreviewRatio } from './KitPreview';
import {
  COLOR_FIELDS,
  COLOR_LABEL,
  EMPTY_KIT,
  FILE_RULES,
  MAX_BUMPER_SECONDS,
  POSITIONS,
  POSITION_LABEL,
  buildInput,
  changedParts,
  currentFile,
  describeChanges,
  draftFrom,
  fileName,
  fontLabel,
  isHex,
  kitColor,
  loadableUrl,
  normalizeHex,
  validateDraft,
  validateFile,
  type ColorField,
  type FileField,
  type KitDraft,
  type KitErrors,
  type KitSource,
} from './kit';
import { useUnsavedGuard } from './useUnsavedGuard';

const NEW_ID = 'nuevo';

/** Button that opens the file picker of one of the kit's files. */
function FilePick({
  field,
  label,
  onPick,
  children,
  disabled,
  disabledReason,
}: {
  field: FileField;
  /** Accessible name of the hidden input: "Fuente principal". */
  label: string;
  onPick: (file: File) => void;
  children: ReactNode;
  disabled?: boolean;
  disabledReason?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        className={styles.hiddenInput}
        accept={FILE_RULES[field].accept}
        aria-label={label}
        tabIndex={-1}
        disabled={disabled}
        onChange={(event) => {
          const file = event.target.files?.[0];
          // Picking the same file again must fire the change too.
          event.target.value = '';
          if (file) onPick(file);
        }}
      />
      <Button size="sm" iconLeft="upload" disabled={disabled} disabledReason={disabledReason} onClick={() => input.current?.click()}>
        {children}
      </Button>
    </>
  );
}

/** Seconds of a video file, read in the browser. Null when it cannot be read. */
function readVideoDuration(file: File): Promise<number | null> {
  return new Promise((resolve) => {
    if (typeof URL.createObjectURL !== 'function') return resolve(null);
    const url = URL.createObjectURL(file);
    const video = document.createElement('video');
    const done = (value: number | null) => {
      URL.revokeObjectURL(url);
      resolve(value);
    };
    video.preload = 'metadata';
    video.onloadedmetadata = () => done(Number.isFinite(video.duration) ? video.duration : null);
    video.onerror = () => done(null);
    window.setTimeout(() => done(null), 4000);
    video.src = url;
  });
}

function ColorInput({
  field,
  value,
  error,
  disabled,
  onChange,
}: {
  field: ColorField;
  value: string;
  error?: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const meta = COLOR_LABEL[field];
  return (
    <Field label={meta.label} hint={meta.hint} error={error}>
      <div className={styles.cinput}>
        <input
          type="color"
          className={styles.cswatch}
          value={isHex(value) ? value : '#000000'}
          disabled={disabled}
          aria-label={`Elegir el color de ${meta.label.toLowerCase()}, actual ${value}`}
          onChange={(event) => onChange(event.target.value.toUpperCase())}
        />
        <Input
          className="num"
          value={value}
          spellCheck={false}
          maxLength={7}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          onBlur={(event) => onChange(normalizeHex(event.target.value))}
        />
      </div>
    </Field>
  );
}

function BumperTile({
  field,
  title,
  kit,
  draft,
  duration,
  error,
  locked,
  lockedReason,
  onPick,
  onClear,
}: {
  field: 'intro_video' | 'outro_video';
  title: string;
  kit: KitSource;
  draft: KitDraft;
  duration: number | null;
  error?: string;
  locked: boolean;
  lockedReason?: string;
  onPick: (file: File) => void;
  onClear: () => void;
}) {
  const { file, url } = currentFile(kit, draft, field);
  const blob = useObjectUrl(file);
  const src = blob ?? loadableUrl(url);
  const video = useRef<HTMLVideoElement>(null);
  const [playing, setPlaying] = useState(false);
  const has = Boolean(file || url);
  const lower = title.toLowerCase();
  return (
    <div className={styles.mtile}>
      {has ? (
        <div className={styles.mplayer}>
          {src ? (
            <video ref={video} src={src} muted playsInline preload="metadata" onEnded={() => setPlaying(false)} onPause={() => setPlaying(false)} onPlay={() => setPlaying(true)} />
          ) : (
            <span className={styles.pvFrame} style={{ '--f1': thumbHue(`${field}${fileName(url)}`) } as React.CSSProperties} />
          )}
          {src ? (
            <button
              type="button"
              className={styles.mplay}
              aria-label={playing ? `Pausar ${lower}` : `Reproducir ${lower}`}
              onClick={() => {
                const element = video.current;
                if (!element) return;
                if (element.paused) void element.play().catch(() => setPlaying(false));
                else element.pause();
              }}
            >
              <Icon name={playing ? 'pause' : 'play'} size={14} />
            </button>
          ) : null}
        </div>
      ) : (
        <div className={cx(styles.mplayer, styles.mplayerEmpty)} aria-hidden="true">
          <Icon name="video" size={20} />
        </div>
      )}
      <div className={styles.minfo}>
        <span className={styles.rowLabel}>{title}</span>
        {has ? (
          <>
            <span className={`t-small muted ${styles.fname}`}>{file ? file.name : fileName(url)}</span>
            <span className="t-label subtle num">
              {file ? [duration !== null ? formatDuration(Math.round(duration)) : null, formatBytes(file.size)].filter(Boolean).join(' · ') : 'Guardado'}
            </span>
          </>
        ) : (
          <>
            <span className="t-small muted">Sin video</span>
            <span className="t-label subtle">{field === 'intro_video' ? 'Va al principio de cada clip.' : 'Va al final de cada clip.'}</span>
          </>
        )}
        {error ? (
          <span className={styles.fileError} role="alert">
            {error}
          </span>
        ) : null}
        <div className={styles.mactions}>
          <FilePick field={field} label={title} onPick={onPick} disabled={locked && !lockedReason} disabledReason={lockedReason}>
            {has ? 'Reemplazar' : `Subir ${lower}`}
          </FilePick>
          {has ? (
            <Button size="sm" variant="ghost" onClick={onClear} disabled={locked}>
              Quitar
            </Button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function EditorSkeleton() {
  return (
    <Page>
      <div aria-busy="true" aria-label="Cargando el brand kit">
        <Skeleton width={160} height={14} className={styles.crumbs} />
        <div className={styles.head}>
          <h1 className="t-h1">
            <Skeleton width={220} height={28} />
          </h1>
        </div>
        <div className={styles.split}>
          <div className={styles.formcol}>
            {[120, 132, 148, 160].map((height) => (
              <Card key={height} padded>
                <Skeleton width="30%" height={18} />
                <Skeleton height={height} style={{ marginTop: 16 }} />
              </Card>
            ))}
          </div>
          <div className={styles.prevcol}>
            <Card padded>
              <Skeleton height={420} />
            </Card>
          </div>
        </div>
      </div>
    </Page>
  );
}

function EditorForm({ kit }: { kit: BrandKit | null }) {
  const router = useRouter();
  const toast = useToast();
  const { can, reason, role } = usePermissions();
  const { plan } = usePlan();
  const create = useCreateBrandKit();
  const update = useUpdateBrandKit(kit?.id ?? '');
  const remove = useDeleteBrandKit();
  const source: KitSource = kit ?? EMPTY_KIT;
  const isNew = !kit;
  // No role yet means the session is still loading, not that the user is a Lector.
  const mayManage = role === null || can('brandkit.manage');
  const locked = !mayManage;
  const lockedReason = locked ? reason : undefined;

  const [draft, setDraft] = useState<KitDraft>(() => draftFrom(source));
  const [errors, setErrors] = useState<KitErrors>({});
  const [fileErrors, setFileErrors] = useState<Partial<Record<FileField, string>>>({});
  const [durations, setDurations] = useState<Partial<Record<FileField, number | null>>>({});
  const [serverError, setServerError] = useState<string | null>(null);
  const [ratio, setRatio] = useState<PreviewRatio>('9:16');
  const [safeZones, setSafeZones] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const defaultLabelId = useId();
  const safeLabelId = useId();
  const posLabelId = useId();

  const parts = useMemo(() => changedParts(source, draft), [source, draft]);
  const dirty = parts.length > 0;
  const saving = create.isPending || update.isPending;
  const reset = () => {
    setDraft(draftFrom(source));
    setErrors({});
    setFileErrors({});
    setServerError(null);
  };
  const guard = useUnsavedGuard(dirty && !saving);

  const set = <K extends keyof KitDraft>(key: K, value: KitDraft[K]) => {
    setDraft((current) => ({ ...current, [key]: value }));
    if (key in errors) setErrors((current) => ({ ...current, [key]: undefined }));
  };
  const setFile = (field: FileField, value: File | null | undefined) => {
    setDraft((current) => {
      const files = { ...current.files };
      // Removing a file that was never saved is the same as not touching it.
      if (value === undefined || (value === null && !source[field])) delete files[field];
      else files[field] = value;
      return { ...current, files };
    });
    setFileErrors((current) => ({ ...current, [field]: undefined }));
  };
  const pick = (field: FileField) => (file: File) => {
    const problem = validateFile(field, file);
    if (problem) {
      setFileErrors((current) => ({ ...current, [field]: problem }));
      return;
    }
    setFile(field, file);
    if (field === 'intro_video' || field === 'outro_video') {
      void readVideoDuration(file).then((seconds) => {
        setDurations((current) => ({ ...current, [field]: seconds }));
        if (seconds !== null && seconds > MAX_BUMPER_SECONDS + 0.5) {
          setFile(field, undefined);
          setFileErrors((current) => ({ ...current, [field]: `El video dura ${formatDuration(Math.round(seconds))}. Subí uno de hasta ${MAX_BUMPER_SECONDS} s.` }));
        }
      });
    }
  };

  const primaryFont = currentFile(source, draft, 'primary_font');
  const secondaryFont = currentFile(source, draft, 'secondary_font');
  const logo = currentFile(source, draft, 'watermark_logo');
  const logoBlob = useObjectUrl(logo.file);
  const fontFamily = useFontFace(primaryFont.file ?? loadableUrl(primaryFont.url));
  const secondaryFamily = useFontFace(secondaryFont.file ?? loadableUrl(secondaryFont.url));
  const hasLogo = Boolean(logo.file || logo.url);
  const colors = { text: draft.primary_color, background: draft.secondary_color, accent: draft.accent_color };
  const safeColors = {
    text: isHex(colors.text) ? colors.text : kitColor(source, 'primary_color'),
    background: isHex(colors.background) ? colors.background : kitColor(source, 'secondary_color'),
    accent: isHex(colors.accent) ? colors.accent : kitColor(source, 'accent_color'),
  };
  const title = draft.name.trim() || (isNew ? 'Nuevo brand kit' : source.name);
  const fontName = primaryFont.file ? fontLabel(primaryFont.file.name) : fontLabel(primaryFont.url);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    if (locked || saving) return;
    const found = validateDraft(draft);
    setErrors(found);
    setServerError(null);
    if (Object.values(found).some(Boolean)) return;
    const input = buildInput(source, draft, { create: isNew });
    const onError = (error: unknown) => {
      if (error instanceof ApiError && error.status === 400) {
        const fields = error.fieldErrors;
        setErrors({
          name: fields.name?.[0],
          primary_color: fields.primary_color?.[0],
          secondary_color: fields.secondary_color?.[0],
          accent_color: fields.accent_color?.[0],
        });
        const files: Partial<Record<FileField, string>> = {};
        for (const field of ['primary_font', 'secondary_font', 'watermark_logo', 'intro_video', 'outro_video'] as const) {
          if (fields[field]?.[0]) files[field] = fields[field][0];
        }
        setFileErrors(files);
        if (Object.keys(fields).length > 0) return;
      }
      setServerError(errorMessage(error, 'No pudimos guardar el brand kit.'));
    };
    if (isNew) {
      create.mutate({ ...input, name: draft.name.trim() }, {
        onSuccess: (created) => {
          toast.success(`Se creó «${created.name}»`);
          guard.allowNext();
          router.replace(`/brand-kits/${created.id}`);
        },
        onError,
      });
    } else {
      update.mutate(input, {
        onSuccess: (saved) => {
          setDraft(draftFrom(saved));
          toast.success('Cambios guardados');
        },
        onError,
      });
    }
  };

  const fontRow = (field: 'primary_font' | 'secondary_font', label: string, family: string | undefined, emptyHint: string) => {
    const { file, url } = currentFile(source, draft, field);
    const has = Boolean(file || url);
    const name = file ? file.name : fileName(url);
    return (
      <div className={styles.frow}>
        <span className={cx(styles.faa, !has && styles.faaEmpty)} style={{ fontFamily: family ? `'${family}', var(--font)` : undefined }} aria-hidden="true">
          Aa
        </span>
        <span className={styles.fmeta}>
          <span className="t-label subtle">{label}</span>
          {has ? <span className={styles.strong}>{fontLabel(name) || name}</span> : <span className="muted">Sin archivo</span>}
        </span>
        <span className={cx('t-small', has ? 'muted num' : 'subtle', styles.ffile)}>{has ? (file ? `${name} · ${formatBytes(file.size)}` : name) : emptyHint}</span>
        <span className={styles.factions}>
          <FilePick field={field} label={`Fuente ${label.toLowerCase()}`} onPick={pick(field)} disabled={locked && !lockedReason} disabledReason={lockedReason}>
            {has ? 'Reemplazar' : 'Subir fuente'}
          </FilePick>
          {has ? (
            <Button size="sm" variant="ghost" disabled={locked} onClick={() => setFile(field, null)}>
              Quitar
            </Button>
          ) : null}
        </span>
        {fileErrors[field] ? (
          <span className={styles.fileError} role="alert">
            {fileErrors[field]}
          </span>
        ) : null}
      </div>
    );
  };

  return (
    <div className={styles.editor}>
      <Page flush={dirty || isNew}>
        <Crumbs className={styles.crumbs} items={[{ label: 'Brand kits', href: '/brand-kits' }, { label: title }]} />
        <div className={styles.head}>
          <div className={styles.headTitle}>
            <h1 className="t-h1">{title}</h1>
            {source.is_default && !isNew ? <Badge tone="outline">Por defecto</Badge> : null}
          </div>
          <div className={styles.defrow}>
            <span className={styles.deftxt}>
              <span className={styles.rowLabel} id={defaultLabelId}>
                Usar por defecto
              </span>
              <span className={styles.rowHint}>Se aplica a los proyectos nuevos. Solo puede haber uno.</span>
            </span>
            <Switch checked={draft.is_default} onChange={(value) => set('is_default', value)} ariaLabel="Usar por defecto" disabled={locked} />
          </div>
        </div>

        {locked ? (
          <Banner tone="info" icon="lock" title="Solo lectura" className={styles.crumbs}>
            Tu rol puede ver los brand kits, no editarlos. Pedile acceso a un admin.
          </Banner>
        ) : null}
        {serverError ? (
          <Banner tone="err" live="alert" title={serverError} className={styles.crumbs} />
        ) : null}

        <div className={styles.split}>
          <form className={styles.formcol} aria-label={isNew ? 'Nuevo brand kit' : 'Editar brand kit'} id="brand-kit-form" onSubmit={onSubmit} noValidate>
            <Card as="section">
              <CardHead title="Datos" />
              <div className={styles.stack}>
                <Field label="Nombre" error={errors.name}>
                  <Input value={draft.name} maxLength={100} disabled={locked} placeholder="Estudio Norte" onChange={(event) => set('name', event.target.value)} />
                </Field>
                <Field label="Descripción" optional>
                  <Textarea value={draft.description} disabled={locked} style={{ minHeight: 64 }} onChange={(event) => set('description', event.target.value)} />
                </Field>
              </div>
            </Card>

            <Card as="section">
              <CardHead title="Colores de los subtítulos" actions={<span className={styles.headNote}>Valor hex</span>} />
              <div className={styles.colors}>
                {COLOR_FIELDS.map((field) => (
                  <ColorInput key={field} field={field} value={draft[field]} error={errors[field]} disabled={locked} onChange={(value) => set(field, value)} />
                ))}
              </div>
            </Card>

            <Card as="section">
              <CardHead
                title="Fuentes"
                actions={
                  <span className={styles.headNote}>
                    Archivos .ttf u .otf, hasta <Num>5 MB</Num>
                  </span>
                }
              />
              <div className={styles.frows}>
                {fontRow('primary_font', 'Principal', fontFamily, 'Si no subís una, se usa la del sistema.')}
                {fontRow('secondary_font', 'Secundaria', secondaryFamily, 'Si no subís una, se usa la principal.')}
              </div>
            </Card>

            <Card as="section">
              <CardHead
                title="Logo de marca de agua"
                actions={
                  <span className={styles.headNote}>
                    PNG con fondo transparente, hasta <Num>2 MB</Num>
                  </span>
                }
              />
              <div className={styles.logoGrid}>
                <div className={styles.logoFile}>
                  <span className={styles.rowLabel}>Archivo</span>
                  <div className={styles.filerow}>
                    {hasLogo ? (
                      <LogoMark name={title} src={logoBlob ?? logo.url} colors={safeColors} />
                    ) : (
                      <span className={styles.logoEmpty} aria-hidden="true">
                        <Icon name="brand-kit" size={16} />
                      </span>
                    )}
                    <span className={styles.fmeta}>
                      {hasLogo ? (
                        <>
                          <span className={cx(styles.strong, styles.fname)}>{logo.file ? logo.file.name : fileName(logo.url)}</span>
                          <span className="t-label subtle num">{logo.file ? formatBytes(logo.file.size) : 'Guardado'}</span>
                        </>
                      ) : (
                        <>
                          <span className="muted">Sin logo</span>
                          <span className="t-label subtle">Los clips salen sin marca de agua propia.</span>
                        </>
                      )}
                    </span>
                    <FilePick field="watermark_logo" label="Logo de marca de agua" onPick={pick('watermark_logo')} disabled={locked && !lockedReason} disabledReason={lockedReason}>
                      {hasLogo ? 'Reemplazar' : 'Subir logo'}
                    </FilePick>
                    {hasLogo ? (
                      <Button size="sm" variant="ghost" disabled={locked} onClick={() => setFile('watermark_logo', null)}>
                        Quitar
                      </Button>
                    ) : null}
                  </div>
                  {fileErrors.watermark_logo ? (
                    <span className={styles.fileError} role="alert">
                      {fileErrors.watermark_logo}
                    </span>
                  ) : null}
                </div>
                <div className={styles.logoPos}>
                  <span className={styles.rowLabel} id={posLabelId}>
                    Posición
                  </span>
                  <div className={styles.posrow}>
                    <div className={styles.pospick} role="radiogroup" aria-labelledby={posLabelId}>
                      <span className={styles.pvFrame} />
                      {POSITIONS.map((position) => (
                        <button
                          key={position}
                          type="button"
                          role="radio"
                          aria-checked={draft.watermark_position === position}
                          aria-label={POSITION_LABEL[position]}
                          className={cx(styles.pp, styles[`pp_${position}`])}
                          disabled={locked}
                          onClick={() => set('watermark_position', position)}
                        />
                      ))}
                    </div>
                    <span className={styles.postxt}>
                      <span className={`t-small ${styles.strong}`}>{POSITION_LABEL[draft.watermark_position]}</span>
                      <span className="t-label subtle">Elegí una esquina del cuadro. Se adapta a cada formato.</span>
                    </span>
                  </div>
                </div>
              </div>
            </Card>

            <Card as="section">
              <CardHead
                title="Intro y outro"
                actions={
                  <span className={styles.headNote}>
                    Video corto en MP4, hasta <Num>{MAX_BUMPER_SECONDS} s</Num> y <Num>50 MB</Num>
                  </span>
                }
              />
              <div className={styles.mgrid}>
                {(['intro_video', 'outro_video'] as const).map((field) => (
                  <BumperTile
                    key={field}
                    field={field}
                    title={field === 'intro_video' ? 'Intro' : 'Outro'}
                    kit={source}
                    draft={draft}
                    duration={durations[field] ?? null}
                    error={fileErrors[field]}
                    locked={locked}
                    lockedReason={lockedReason}
                    onPick={pick(field)}
                    onClear={() => setFile(field, null)}
                  />
                ))}
              </div>
            </Card>

            {!isNew ? (
              <Card as="section" className={styles.danger}>
                <div className={styles.deftxt}>
                  <span className={styles.rowLabel}>Borrar brand kit</span>
                  <span className={styles.rowHint}>Los proyectos que lo usan quedan sin kit. Los clips ya renderizados no cambian.</span>
                </div>
                <Button variant="danger" iconLeft="trash" disabledReason={lockedReason} onClick={() => setConfirmDelete(true)}>
                  Borrar
                </Button>
              </Card>
            ) : null}
          </form>

          <aside className={styles.prevcol} aria-label="Vista previa">
            <Card>
              <CardHead
                title="Vista previa"
                actions={
                  <SegmentedControl
                    ariaLabel="Formato de la vista previa"
                    value={ratio}
                    onChange={setRatio}
                    options={[
                      { value: '9:16', label: '9:16' },
                      { value: '1:1', label: '1:1' },
                      { value: '16:9', label: '16:9' },
                    ]}
                  />
                }
              />
              <div className={styles.pvBody}>
                <KitPreview
                  className={styles.pvBig}
                  colors={safeColors}
                  name={title}
                  fontFamily={fontFamily}
                  logo={hasLogo ? { src: logoBlob ?? logo.url } : null}
                  position={draft.watermark_position}
                  ratio={ratio}
                  safeZones={safeZones}
                  planWatermark={Boolean(plan?.has_watermark)}
                  hue={kit ? thumbHue(kit.id) : undefined}
                />
                <div className={styles.saferow}>
                  <span className={styles.deftxt}>
                    <span className={styles.rowLabel} id={safeLabelId}>
                      Zonas seguras
                    </span>
                    <span className={styles.rowHint}>
                      {ratio === '9:16' ? 'Lo rayado lo tapan los botones y el texto de cada red.' : 'Se ven en el formato 9:16, el de las redes.'}
                    </span>
                  </span>
                  <Switch checked={safeZones && ratio === '9:16'} onChange={setSafeZones} ariaLabel="Zonas seguras" disabled={ratio !== '9:16'} />
                </div>
                <p className={`t-small subtle ${styles.pvNote}`}>
                  Fotograma de muestra {fontName ? `con ${fontName}` : 'con la fuente del sistema'}
                  {hasLogo ? ` y el logo ${POSITION_LABEL[draft.watermark_position].toLowerCase()}` : ', sin logo'}. Se actualiza mientras editás.
                </p>
                {plan?.has_watermark ? (
                  <Banner tone="neutral" icon="lock" className={styles.pvNote}>
                    Tu plan agrega la marca de agua de OneCreator a cada clip, además de tu logo.{' '}
                    <Link className="lnk" href="/uso/planes">
                      Ver planes
                    </Link>
                  </Banner>
                ) : null}
              </div>
            </Card>
          </aside>
        </div>
      </Page>

      {dirty || isNew ? (
        <ActionBar
          className={styles.savebar}
          summary={isNew ? 'Brand kit nuevo' : 'Cambios sin guardar'}
          detail={isNew ? 'Todavía no se guardó. Podés seguir editándolo después de crearlo.' : describeChanges(parts)}
        >
          {isNew ? (
            <Button variant="ghost" href="/brand-kits">
              Cancelar
            </Button>
          ) : (
            <Button variant="ghost" onClick={reset} disabled={saving}>
              Descartar
            </Button>
          )}
          <Button variant="primary" size="lg" type="submit" form="brand-kit-form" loading={saving} disabledReason={lockedReason}>
            {isNew ? 'Crear brand kit' : 'Guardar'}
          </Button>
        </ActionBar>
      ) : null}

      <UnsavedChangesDialog open={guard.asking} onStay={guard.stay} onDiscard={guard.discard} what={isNew ? 'los datos del brand kit nuevo' : `los cambios de «${source.name}»`} />
      <ConfirmDialog
        open={confirmDelete}
        onClose={() => {
          setConfirmDelete(false);
          remove.reset();
        }}
        tone="danger"
        icon="trash"
        title={`¿Borrar «${source.name}»?`}
        description={
          source.is_default
            ? 'Es tu kit por defecto: los proyectos nuevos van a salir sin brand kit hasta que elijas otro. Los proyectos que lo usaban quedan sin kit.'
            : 'Los proyectos que lo usaban quedan sin brand kit. Los clips ya renderizados no cambian.'
        }
        confirmLabel="Borrar brand kit"
        loading={remove.isPending}
        error={remove.isError ? errorMessage(remove.error, 'No pudimos borrar el brand kit.') : undefined}
        onConfirm={() => {
          if (!kit) return;
          remove.mutate(kit.id, {
            onSuccess: () => {
              toast.success(`Se borró «${kit.name}»`);
              guard.allowNext();
              router.replace('/brand-kits');
            },
          });
        }}
      />
    </div>
  );
}

/** F2 · Editar brand kit. `/brand-kits/nuevo` creates one. */
export function BrandKitEditor({ id }: { id: string }) {
  const isNew = id === NEW_ID;
  const query = useBrandKit(isNew ? null : id);
  if (isNew) return <EditorForm key={NEW_ID} kit={null} />;
  if (query.isError && !query.data) {
    return (
      <Page>
        <LoadError error={query.error} onRetry={() => void query.refetch()} />
      </Page>
    );
  }
  if (!query.data) return <EditorSkeleton />;
  return <EditorForm key={query.data.id} kit={query.data} />;
}
