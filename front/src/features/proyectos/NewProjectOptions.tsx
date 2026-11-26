'use client';

import Link from 'next/link';
import { useId, useState, type ReactNode } from 'react';
import type { Plan, Pricing } from '@/api/contracts/billing';
import type { BrandKit } from '@/api/contracts/brandkits';
import type { RenderResolution } from '@/api/contracts/common';
import type { AspectRatio, EditingStyle, Layout, PipCorner, Position, ProjectType, SubtitlePosition, SubtitleSize } from '@/api/contracts/projects';
import { formatMinutes, formatNumber, formatTokens, normalizeHex } from '@/lib/format';
import { estimateCost, firstPlanWith, planAllowsResolution } from '@/lib/pricing';
import { LABELS } from '@/lib/project-status';
import { Badge, Icon, Input, PlanLock, RadioCard, RadioGroup, SegmentedControl, Select, Slider, Stepper, Switch, Textarea, cx } from '@/ui';
import { CaptionSample, StyleSample } from './CaptionSample';
import { LayoutDiagram } from './LayoutDiagram';
import styles from './NewProject.module.css';
import {
  LAYOUT_HINTS,
  MANUAL_CAMERA_DEFAULT,
  MAX_CLIPS,
  PIP_CORNERS,
  SUBTITLE_COLORS,
  TRENDING_COLORS,
  TRENDING_TEXT_MAX,
  WORDS_PER_LINE,
  followsFaces,
  hasAdvanced,
  manualCameraActive,
  styleLine,
  usesCameraSide,
  usesGameplayPosition,
  subtitlesSummary,
  type ProjectConfig,
} from './new-project-config';

export type SetConfig = (patch: Partial<ProjectConfig>) => void;

export function Section({ id, title, hint, aside, children }: { id: string; title: string; hint?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className={styles.sec} aria-labelledby={id}>
      <div className={styles.secHead}>
        <h2 className="t-h3" id={id}>
          {title}
        </h2>
        {hint ? <p className="t-small muted">{hint}</p> : null}
        {aside}
      </div>
      {children}
    </section>
  );
}

function Labeled({ label, hint, error, children, className }: { label: string; hint?: ReactNode; error?: ReactNode; children: ReactNode; className?: string }) {
  const id = useId();
  return (
    <div className={cx(styles.field, className)}>
      <span className={styles.fieldLabel} id={id}>
        {label}
      </span>
      {children}
      {error ? (
        <span className={styles.errline} role="alert">
          <Icon name="error" size={14} />
          <span>{error}</span>
        </span>
      ) : hint ? (
        <span className={styles.hint}>{hint}</span>
      ) : null}
    </div>
  );
}

/* ── Qué querés hacer ───────────────────────────────────────────────────── */

export type SingleEditBlock = 'link' | 'long' | null;

export function WhatSection({
  value,
  onChange,
  block,
  maxSeconds,
}: {
  value: ProjectType;
  onChange: (value: ProjectType) => void;
  block: SingleEditBlock;
  maxSeconds: number;
}) {
  const reason =
    block === 'link'
      ? 'Para editar el video completo, subí el archivo'
      : block === 'long'
        ? `Para editar el video completo, el video puede durar hasta ${formatMinutes(maxSeconds)}`
        : null;
  return (
    <Section id="np-que" title="Qué querés hacer">
      <RadioGroup ariaLabel="Qué querés hacer" value={value} onChange={(next) => onChange(next as ProjectType)} layout="cards" columns={2}>
        <RadioCard value="repurpose" title={LABELS.project_type.repurpose} description="La IA elige los mejores momentos y arma un clip corto con cada uno." />
        <RadioCard
          value="single_edit"
          title={LABELS.project_type.single_edit}
          description="Un solo video con el estilo, los subtítulos y el formato que elijas. No usa la sección Clips."
          disabled={block !== null}
        >
          {reason ? (
            <span className={styles.lockNote}>
              <Icon name="lock" size={12} />
              {reason}
            </span>
          ) : null}
        </RadioCard>
      </RadioGroup>
    </Section>
  );
}

/* ── Formato ────────────────────────────────────────────────────────────── */

const RATIOS: Array<{ value: AspectRatio; label: string; width: number; height: number }> = [
  { value: '9:16', label: 'Vertical', width: 13, height: 22 },
  { value: '1:1', label: 'Cuadrado', width: 18, height: 18 },
  { value: '16:9', label: 'Horizontal', width: 24, height: 14 },
];
const RESOLUTIONS: RenderResolution[] = ['720p', '1080p', '4K'];
export const LINK_RESOLUTION_NOTE = 'Los proyectos por link se procesan en 720p. Para 1080p o 4K, subí el archivo.';

export function FormatSection({
  config,
  set,
  plan,
  plans,
  pricing,
  duration,
  isLink,
  marked,
}: {
  config: ProjectConfig;
  set: SetConfig;
  plan: Plan | null;
  plans: Plan[];
  pricing: Pricing | null;
  duration: number | null;
  isLink: boolean;
  marked: boolean;
}) {
  const baseTotal =
    pricing && duration !== null && !isLink ? estimateCost({ durationSeconds: duration, resolution: '720p', useSeo: config.use_seo }, pricing, plan).total : null;
  const locked = RESOLUTIONS.filter((resolution) => !planAllowsResolution(plan, resolution)).map((resolution) => ({
    resolution,
    plan: firstPlanWith(plans, (item) => planAllowsResolution(item, resolution))?.name ?? null,
  }));
  const planNote = plan ? `Tu plan ${plan.name} incluye ${plan.max_resolution === '720p' ? '720p' : `hasta ${plan.max_resolution}`}.` : undefined;

  const describe = (resolution: RenderResolution): string => {
    if (resolution === '720p') return 'Incluido';
    if (isLink) return 'Solo con archivo';
    if (pricing && baseTotal !== null && duration !== null) {
      const total = estimateCost({ durationSeconds: duration, resolution, useSeo: config.use_seo }, pricing, plan).total;
      return `+${formatTokens(total - baseTotal)}`;
    }
    return pricing ? `×${formatNumber(pricing.resolution_multipliers[resolution])}` : '';
  };

  return (
    <Section id="np-formato" title="Formato" hint={planNote}>
      <div className={styles.twoCol}>
        <Labeled label="Formato de salida">
          {(
            <RadioGroup ariaLabel="Formato de salida" value={config.aspect_ratio} onChange={(value) => set({ aspect_ratio: value as AspectRatio })} layout="cards" columns={3} className={styles.optCards}>
              {RATIOS.map((ratio) => (
                <RadioCard
                  key={ratio.value}
                  value={ratio.value}
                  title={<span className="num">{ratio.value}</span>}
                  description={ratio.label}
                  media={
                    <span className={styles.ratioIco} aria-hidden="true">
                      <span style={{ width: ratio.width, height: ratio.height }} />
                    </span>
                  }
                />
              ))}
            </RadioGroup>
          )}
        </Labeled>
        <Labeled
          label="Resolución"
          error={marked ? 'Tu plan no incluye esta resolución. Elegí otra o mirá los planes.' : undefined}
          hint={
            isLink ? (
              LINK_RESOLUTION_NOTE
            ) : locked.length > 0 ? (
              <span className={styles.lockLine}>
                <Icon name="lock" size={12} />
                <span>
                  {locked.length === 1 ? 'Disponible en el plan ' : 'Disponible en los planes '}
                  {locked.map((item, index) => (
                    <span key={item.resolution}>
                      {index > 0 ? ' y ' : ''}
                      {item.plan ?? 'superior'} (<span className="num">{item.resolution}</span>)
                    </span>
                  ))}
                  {' · '}
                  <Link className="lnk" href="/uso/planes">
                    Ver planes
                  </Link>
                </span>
              </span>
            ) : undefined
          }
        >
          {(
            <RadioGroup
              ariaLabel="Resolución"
              value={config.render_resolution}
              onChange={(value) => set({ render_resolution: value as RenderResolution })}
              layout="cards"
              columns={3}
              className={styles.optCards}
            >
              {RESOLUTIONS.map((resolution) => {
                const lockedBy = locked.find((item) => item.resolution === resolution);
                if (lockedBy) {
                  return (
                    <RadioCard
                      key={resolution}
                      value={resolution}
                      disabled
                      title={
                        <span className={cx('num', styles.rcTop)}>
                          <Icon name="lock" size={12} />
                          <span aria-hidden="true">{resolution}</span>
                          <span className="sr-only">{`${resolution}, disponible en el plan ${lockedBy.plan ?? 'superior'}`}</span>
                        </span>
                      }
                      description={<span aria-hidden="true">Plan {lockedBy.plan ?? 'superior'}</span>}
                    />
                  );
                }
                return (
                  <RadioCard
                    key={resolution}
                    value={resolution}
                    disabled={isLink && resolution !== '720p'}
                    title={<span className="num">{resolution}</span>}
                    description={<span className="num">{describe(resolution)}</span>}
                  />
                );
              })}
            </RadioGroup>
          )}
        </Labeled>
      </div>
    </Section>
  );
}

/* ── Clips ──────────────────────────────────────────────────────────────── */

export function ClipsSection({ config, set, maxCriteria, error }: { config: ProjectConfig; set: SetConfig; maxCriteria: number; error?: string | null }) {
  const criteriaId = useId();
  const maxId = useId();
  return (
    <Section id="np-clips" title="Clips" aside={<Badge tone="outline" size="sm">Solo con «Sacar clips»</Badge>}>
      <div className={styles.clipsRow}>
        <div className={styles.field}>
          <label className={styles.fieldLabel} htmlFor={maxId}>
            Cantidad máxima
          </label>
          <Stepper
            id={maxId}
            value={config.max_clips}
            min={MAX_CLIPS.min}
            max={MAX_CLIPS.max}
            onChange={(value) => set({ max_clips: value })}
            decrementLabel="Un clip menos"
            incrementLabel="Un clip más"
          />
          <span className={styles.hint}>
            De <span className="num">{MAX_CLIPS.min}</span> a <span className="num">{MAX_CLIPS.max}</span>
          </span>
        </div>
        <Labeled label="Nivel de IA" hint="Cómo elige los momentos. Inteligente analiza más a fondo.">
          {(
            <SegmentedControl
              ariaLabel="Nivel de IA"
              value={config.intelligence_level}
              onChange={(value) => set({ intelligence_level: value })}
              options={[
                { value: 'fast', label: LABELS.intelligence_level.fast },
                { value: 'smart', label: LABELS.intelligence_level.smart },
              ]}
            />
          )}
        </Labeled>
        <div className={cx(styles.field, styles.grow)}>
          <label className={styles.fieldLabel} htmlFor={criteriaId}>
            ¿Qué buscás? <span className={styles.optional}>(opcional)</span>
          </label>
          <Textarea
            id={criteriaId}
            className={styles.criteria}
            maxLength={maxCriteria}
            value={config.selection_criteria}
            invalid={Boolean(error)}
            aria-describedby={`${criteriaId}-help`}
            onChange={(event) => set({ selection_criteria: event.target.value })}
            placeholder="Por ejemplo: momentos con consejos concretos, sin la parte de la publicidad"
          />
          <span className={cx(styles.hint, styles.hintRow)} id={`${criteriaId}-help`}>
            {error ? (
              <span className={styles.errline} role="alert">
                <Icon name="error" size={14} />
                <span>{error}</span>
              </span>
            ) : (
              <span>Si lo dejás vacío, la IA elige los momentos que mejor funcionan solos.</span>
            )}
            <span className="num" aria-label={`${config.selection_criteria.length} de ${maxCriteria} caracteres`}>
              {config.selection_criteria.length} / {maxCriteria}
            </span>
          </span>
        </div>
      </div>
    </Section>
  );
}

/* ── Personalizar ───────────────────────────────────────────────────────── */

export type CustomizePanel = 'style' | 'subtitles' | 'intelligence';

function Accordion({
  name,
  value,
  open,
  onToggle,
  children,
}: {
  name: string;
  value: ReactNode;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <div className={styles.acc}>
      <button type="button" className={styles.accHead} aria-expanded={open} aria-controls={`${id}-body`} id={`${id}-head`} onClick={onToggle}>
        <span className={cx(styles.chev, open && styles.chevOpen)}>
          <Icon name="chevron-right" size={14} />
        </span>
        <span className={styles.accName}>{name}</span>
        <span className={styles.accVal}>{value}</span>
        <span className={styles.accCta}>{open ? 'Cerrar' : 'Cambiar'}</span>
      </button>
      {open ? (
        <div className={styles.accBody} id={`${id}-body`} role="region" aria-labelledby={`${id}-head`}>
          {children}
        </div>
      ) : null}
    </div>
  );
}

function ControlRow({ label, description, children }: { label: string; description?: ReactNode; children: ReactNode }) {
  return (
    <div className={styles.ctlRow}>
      <span className={styles.swText}>
        <span className={styles.fieldLabel}>{label}</span>
        {description ? <span className={styles.rcDesc}>{description}</span> : null}
      </span>
      {children}
    </div>
  );
}

const STYLES: Array<{ value: Exclude<EditingStyle, 'custom'>; description: string }> = [
  { value: 'dynamic', description: 'Cortes rápidos, zoom en los remates y palabras resaltadas.' },
  { value: 'minimalist', description: 'Cortes limpios y subtítulos chicos, sin efectos.' },
  { value: 'vlog', description: 'Ritmo relajado, subtítulos en caja y transiciones suaves.' },
  { value: 'hormozi', description: 'Subtítulos enormes en el centro, una idea por pantalla.' },
];
const LAYOUTS: Layout[] = ['fill', 'fit', 'blurred', 'split', 'pip', 'versus', 'active', 'reaction', 'cinematic', 'podcast', 'trending'];
const POSITIONS: Array<{ value: Position; label: string }> = [
  { value: 'left', label: LABELS.position.left },
  { value: 'center', label: LABELS.position.center },
  { value: 'right', label: LABELS.position.right },
];

const ADVANCED_SUMMARY: Partial<Record<Layout, string>> = {
  split: 'Posición del gameplay, lado de la cámara y cámara manual.',
  reaction: 'Posición del contenido, lado de la cámara y cámara manual.',
  pip: 'Esquina y lado de la cámara, y cámara manual.',
  versus: 'Cámara manual: encuadra igual a las dos personas, una por mitad.',
  podcast: 'Cámara manual: encuadra igual a las dos personas, una por mitad.',
};

/** Color and text of the Trending layout: always open, the layout is about them. */
function TrendingOptions({ config, set }: { config: ProjectConfig; set: SetConfig }) {
  const textId = useId();
  return (
    <div className={styles.adv}>
      <div className={styles.advBody}>
        <ControlRow label="Color del borde" description="El otro extremo del gradiente es el mismo color con otro tono.">
          <div className={styles.colors} role="group" aria-label="Color del borde">
            {TRENDING_COLORS.map((color) => (
              <button
                key={color.value}
                type="button"
                className={styles.colorBtn}
                aria-pressed={config.trending_color.toLowerCase() === color.value.toLowerCase()}
                aria-label={color.label}
                title={color.label}
                onClick={() => set({ trending_color: color.value })}
              >
                <span style={{ background: color.value }} />
              </button>
            ))}
          </div>
        </ControlRow>
        <ControlRow label="Texto arriba del video" description={`Opcional, hasta ${TRENDING_TEXT_MAX} caracteres. Los emojis no se dibujan.`}>
          <Input
            id={textId}
            aria-label="Texto arriba del video"
            value={config.trending_text}
            maxLength={TRENDING_TEXT_MAX}
            placeholder="Lo que nadie te cuenta"
            autoComplete="off"
            onChange={(event) => set({ trending_text: event.target.value })}
          />
        </ControlRow>
      </div>
    </div>
  );
}

function AdvancedBlock({ config, set }: { config: ProjectConfig; set: SetConfig }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const layout = config.render_layout;
  const gameplay = usesGameplayPosition(layout);
  const side = usesCameraSide(layout);
  const pip = layout === 'pip';
  const manual = config.manual_camera;
  const atDefaults = manual && !manualCameraActive(config);
  // Versus and Podcast frame the two halves the same way; the rest have one camera.
  const twoPeople = layout === 'versus' || layout === 'podcast';
  return (
    <div className={styles.adv}>
      <button type="button" className={styles.advHead} aria-expanded={open} aria-controls={`${id}-body`} onClick={() => setOpen((value) => !value)}>
        <span className={cx(styles.chev, open && styles.chevOpen)}>
          <Icon name="chevron-right" size={14} />
        </span>
        <span className={styles.swText}>
          <span className={styles.rcTitle}>Avanzado</span>
          <span className={styles.rcDesc}>
            {ADVANCED_SUMMARY[layout] ?? 'Cámara manual.'}
          </span>
        </span>
      </button>
      {open ? (
        <div className={styles.advBody} id={`${id}-body`}>
          {pip ? (
            <ControlRow label="Esquina de la cámara">
              <Select
                aria-label="Esquina de la cámara"
                value={config.pip_corner}
                onChange={(event) => set({ pip_corner: event.target.value as PipCorner })}
                options={PIP_CORNERS.map((value) => ({ value, label: LABELS.pip_corner[value] }))}
              />
            </ControlRow>
          ) : null}
          {gameplay ? (
            <ControlRow label="Posición del gameplay">
              <SegmentedControl ariaLabel="Posición del gameplay" value={config.gameplay_position} onChange={(value) => set({ gameplay_position: value })} options={POSITIONS} />
            </ControlRow>
          ) : null}
          {side ? (
            <ControlRow label="Lado de la cámara" description={manual ? 'La cámara manual reemplaza al lado elegido.' : 'Centro usa todo el cuadro.'}>
              <SegmentedControl
                ariaLabel="Lado de la cámara"
                value={config.camera_selection_position}
                onChange={(value) => set({ camera_selection_position: value })}
                options={POSITIONS.map((option) => ({ ...option, disabled: manual }))}
              />
            </ControlRow>
          ) : null}
          <ControlRow
            label="Cámara manual"
            description={
              twoPeople
                ? 'Encuadra igual a las dos personas, una por mitad. Reemplaza a Seguir caras.'
                : 'Elegís vos dónde está la cámara. Reemplaza al lado elegido y a Seguir caras.'
            }
          >
            <Switch ariaLabel="Cámara manual" checked={manual} onChange={(value) => set({ manual_camera: value })} />
          </ControlRow>
          {manual ? (
            <div className={styles.camera}>
              <ControlRow label="Centro horizontal">
                <Slider
                  aria-label="Centro horizontal"
                  className={styles.slider}
                  min={0}
                  max={1}
                  step={0.05}
                  value={config.manual_camera_x}
                  onChange={(value) => set({ manual_camera_x: value })}
                  formatValue={(value) => formatNumber(value)}
                />
              </ControlRow>
              <ControlRow label="Centro vertical">
                <Slider
                  aria-label="Centro vertical"
                  className={styles.slider}
                  min={0}
                  max={1}
                  step={0.05}
                  value={config.manual_camera_y}
                  onChange={(value) => set({ manual_camera_y: value })}
                  formatValue={(value) => formatNumber(value)}
                />
              </ControlRow>
              <ControlRow label="Zoom">
                <Slider
                  aria-label="Zoom"
                  className={styles.slider}
                  min={1}
                  max={4}
                  step={0.1}
                  value={config.manual_camera_zoom}
                  onChange={(value) => set({ manual_camera_zoom: Math.round(value * 10) / 10 })}
                  formatValue={(value) => `${formatNumber(value, 1)}×`}
                />
              </ControlRow>
              <p className={styles.hint}>
                Zoom <span className="num">1</span> es el encuadre automático: sin Seguir caras, la cámara manual mueve el encuadre sin alejarlo; con Seguir caras puede
                verse más abierto o más cerrado.
              </p>
              {atDefaults ? (
                <p className={styles.hint} role="status">
                  Con los valores por defecto (<span className="num">{formatNumber(MANUAL_CAMERA_DEFAULT.x)}</span> ·{' '}
                  <span className="num">{formatNumber(MANUAL_CAMERA_DEFAULT.y)}</span> · zoom <span className="num">{MANUAL_CAMERA_DEFAULT.zoom}</span>) la cámara manual
                  sigue apagada. Mové alguno para usarla.
                </p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export interface CustomizeProps {
  config: ProjectConfig;
  set: SetConfig;
  /** Name of the starting point, for the section hint. */
  presetName: string;
  modified: boolean;
  open: CustomizePanel | null;
  onOpen: (panel: CustomizePanel | null) => void;
  brandKits: BrandKit[];
  kit: BrandKit | null;
  plan: Plan | null;
  plans: Plan[];
  seoCost: number;
  seoMarked: boolean;
}

export function intelligenceSummary(config: ProjectConfig, seoCost: number): string {
  const single = config.project_type === 'single_edit';
  const parts = [
    followsFaces(config) ? 'seguir caras' : null,
    config.remove_silences ? 'quitar silencios' : null,
    config.use_seo ? `SEO +${formatTokens(seoCost)}` : null,
    single || config.auto_render_bypass ? 'renderiza sin revisar' : 'se detiene en la revisión',
  ].filter((part): part is string => part !== null);
  const text = parts.join(' · ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function CustomizeSection({ config, set, presetName, modified, open, onOpen, brandKits, kit, plan, plans, seoCost, seoMarked }: CustomizeProps) {
  const toggle = (panel: CustomizePanel) => onOpen(open === panel ? null : panel);
  const kitId = useId();
  const single = config.project_type === 'single_edit';
  const facesForced = config.render_layout === 'active';
  const seoAllowed = plan ? plan.has_seo_optimization : true;
  const seoPlan = seoAllowed ? null : (firstPlanWith(plans, (item) => item.has_seo_optimization)?.name ?? null);
  const kitColors = kit ? [kit.primary_color, kit.secondary_color, kit.accent_color].map((color) => normalizeHex(color, '')).filter(Boolean) : [];
  // Brand kit colors arrive as free text: only a real hex can become a subtitle color.
  const kitAccent = kit ? normalizeHex(kit.accent_color, '') : '';
  const colors: Array<{ value: string; label: string }> = [
    ...SUBTITLE_COLORS,
    ...(kitAccent && !SUBTITLE_COLORS.some((item) => item.value.toUpperCase() === kitAccent) ? [{ value: kitAccent, label: 'Color del brand kit' }] : []),
  ];
  const subsOff = !config.add_subtitles;

  return (
    <Section id="np-pers" title="Personalizar" hint={`Valores de «${presetName}»${modified ? ', con cambios tuyos.' : '. Abrí lo que quieras cambiar.'}`}>
      <div className={styles.accList}>
        <Accordion name="Estilo" value={`${styleLine(config)} · ${kit ? `Brand kit ${kit.name}` : 'sin brand kit'}`} open={open === 'style'} onToggle={() => toggle('style')}>
          <Labeled label="Estilo de edición">
            {(
              <RadioGroup
                ariaLabel="Estilo de edición"
                value={config.editing_style}
                onChange={(value) => set({ editing_style: value as ProjectConfig['editing_style'] })}
                layout="cards"
                columns={4}
                className={styles.styleCards}
              >
                {STYLES.map((style) => (
                  <RadioCard key={style.value} value={style.value} title={LABELS.editing_style[style.value]} description={style.description} media={<StyleSample style={style.value} />} />
                ))}
              </RadioGroup>
            )}
          </Labeled>
          <Labeled
            label="Layout"
            hint={
              <>
                {LAYOUT_HINTS[config.render_layout]}
                {config.render_layout === 'versus' && config.aspect_ratio !== '9:16' ? (
                  <>
                    {' '}
                    En <span className="num">{config.aspect_ratio}</span> cada mitad conserva al menos el <span className="num">60 %</span> de su alto y queda negro a los
                    costados.
                  </>
                ) : null}
              </>
            }
          >
            {(
              <RadioGroup
                ariaLabel="Layout"
                value={config.render_layout}
                onChange={(value) => set({ render_layout: value as Layout })}
                layout="cards"
                columns={7}
                className={styles.layoutCards}
              >
                {LAYOUTS.map((layout) => (
                  <RadioCard key={layout} value={layout} title={LABELS.render_layout[layout]} media={<LayoutDiagram layout={layout} ratio={config.aspect_ratio} />} />
                ))}
              </RadioGroup>
            )}
          </Labeled>
          {config.render_layout === 'trending' ? <TrendingOptions config={config} set={set} /> : null}
          {hasAdvanced(config.render_layout) ? <AdvancedBlock key={config.render_layout} config={config} set={set} /> : null}
          <div className={styles.field}>
            <label className={styles.fieldLabel} htmlFor={kitId}>
              Brand kit
            </label>
            <div className={styles.kitRow}>
              {kit ? (
                <span className={styles.swatches} aria-hidden="true">
                  {kitColors.map((color, index) => (
                    <span key={index} style={{ background: color }} />
                  ))}
                </span>
              ) : null}
              <Select
                id={kitId}
                className={styles.kitSelect}
                value={kit?.id ?? ''}
                onChange={(event) => set({ brand_kit: event.target.value || null })}
                options={[...brandKits.map((item) => ({ value: item.id, label: item.is_default ? `${item.name} (por defecto)` : item.name })), { value: '', label: 'Sin brand kit' }]}
              />
            </div>
            <span className={styles.hint}>
              {brandKits.length === 0 ? (
                <>
                  Todavía no tenés un brand kit.{' '}
                  <Link className="lnk" href="/brand-kits">
                    Crear brand kit
                  </Link>
                </>
              ) : (
                <>
                  {kit ? 'Colores, tipografía y logo de tu marca. ' : 'Los clips salen sin colores ni logo de marca. '}
                  <Link className="lnk" href="/brand-kits">
                    Ver brand kits
                  </Link>
                </>
              )}
            </span>
          </div>
        </Accordion>

        <Accordion name="Subtítulos" value={subtitlesSummary(config, kit?.accent_color)} open={open === 'subtitles'} onToggle={() => toggle('subtitles')}>
          <div className={styles.subGrid}>
            <figure className={styles.subPrev} aria-label="Muestra en vivo de los subtítulos">
              <CaptionSample
                enabled={config.add_subtitles}
                size={config.subtitle_size}
                position={config.subtitle_position}
                color={config.subtitle_color}
                wordsPerLine={config.subtitle_words_per_segment}
                style={config.editing_style}
              />
            </figure>
            <div className={styles.subCtl}>
              <ControlRow label="Mostrar subtítulos" description="La muestra cambia en vivo sobre un fotograma de ejemplo.">
                <Switch ariaLabel="Mostrar subtítulos" checked={config.add_subtitles} onChange={(value) => set({ add_subtitles: value })} />
              </ControlRow>
              <hr className="divider" />
              <ControlRow label="Tamaño">
                <SegmentedControl<SubtitleSize>
                  ariaLabel="Tamaño de los subtítulos"
                  value={config.subtitle_size}
                  onChange={(value) => set({ subtitle_size: value })}
                  options={(['small', 'medium', 'large'] as const).map((value) => ({ value, label: LABELS.subtitle_size[value], disabled: subsOff }))}
                />
              </ControlRow>
              <ControlRow label="Posición">
                <SegmentedControl<SubtitlePosition>
                  ariaLabel="Posición de los subtítulos"
                  value={config.subtitle_position}
                  onChange={(value) => set({ subtitle_position: value })}
                  options={(['top', 'center', 'bottom'] as const).map((value) => ({ value, label: LABELS.subtitle_position[value], disabled: subsOff }))}
                />
              </ControlRow>
              <ControlRow label="Color">
                <div className={styles.colors} role="group" aria-label="Color de los subtítulos">
                  {colors.map((color) => (
                    <button
                      key={color.value}
                      type="button"
                      className={styles.colorBtn}
                      aria-pressed={config.subtitle_color.toLowerCase() === color.value.toLowerCase()}
                      aria-label={color.label}
                      title={color.label}
                      disabled={subsOff}
                      onClick={() => set({ subtitle_color: color.value })}
                    >
                      <span style={{ background: color.value }} />
                    </button>
                  ))}
                </div>
              </ControlRow>
              <ControlRow label="Palabras por línea">
                <Stepper
                  value={config.subtitle_words_per_segment}
                  min={WORDS_PER_LINE.min}
                  max={WORDS_PER_LINE.max}
                  onChange={(value) => set({ subtitle_words_per_segment: value })}
                  decrementLabel="Una palabra menos"
                  incrementLabel="Una palabra más"
                  disabled={subsOff}
                />
              </ControlRow>
            </div>
          </div>
        </Accordion>

        <Accordion name="Inteligencia" value={intelligenceSummary(config, seoCost)} open={open === 'intelligence'} onToggle={() => toggle('intelligence')}>
          <div className={styles.swRows}>
            <div className={styles.swRow}>
              <div className={styles.swText}>
                <span className={styles.rcTitle}>Seguir caras</span>
                <span className={styles.rcDesc}>
                  {facesForced
                    ? 'Prendido: el layout Quien habla siempre sigue caras para saber quién habla.'
                    : manualCameraActive(config)
                      ? 'La cámara manual lo reemplaza mientras esté prendida.'
                      : 'El encuadre sigue a quien aparece en cámara.'}
                </span>
              </div>
              <Switch ariaLabel="Seguir caras" checked={followsFaces(config)} disabled={facesForced} onChange={(value) => set({ use_facetracking: value })} />
            </div>
            <div className={styles.swRow}>
              <div className={styles.swText}>
                <span className={styles.rcTitle}>Quitar silencios</span>
                <span className={styles.rcDesc}>Recorta las pausas largas dentro de cada clip.</span>
              </div>
              <Switch ariaLabel="Quitar silencios" checked={config.remove_silences} onChange={(value) => set({ remove_silences: value })} />
            </div>
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
                  Títulos, descripciones y hashtags pensados para cada red.{seoAllowed && plan ? ` Incluido en tu plan ${plan.name}.` : ''}
                </span>
                {seoAllowed ? null : <PlanLock plan={seoPlan ?? 'Creador'} />}
                {seoMarked ? (
                  <span className={styles.errline} role="alert">
                    <Icon name="error" size={14} />
                    <span>Tu plan no incluye SEO. Apagalo o mirá los planes.</span>
                  </span>
                ) : null}
              </div>
              <Switch
                ariaLabel={seoAllowed ? `SEO, suma ${formatTokens(seoCost)}` : `SEO, disponible en el plan ${seoPlan ?? 'Creador'}`}
                checked={config.use_seo}
                disabled={!seoAllowed}
                onChange={(value) => set({ use_seo: value })}
              />
            </div>
            <div className={styles.swRow}>
              <div className={styles.swText}>
                <span className={styles.rcTitle}>Renderizar sin revisar</span>
                <span className={styles.rcDesc}>
                  {single
                    ? 'El video completo se renderiza siempre sin pasar por la revisión.'
                    : config.auto_render_bypass
                      ? 'El render de los clips se reserva apenas estén listos, al mismo precio que aprobarlos, y se cobra solo lo que se entrega. Si no alcanza el saldo, el proyecto espera tu revisión.'
                      : 'Apagado: el proyecto se detiene en la revisión para que elijas qué clips renderizar.'}
                </span>
              </div>
              <Switch
                ariaLabel="Renderizar sin revisar"
                checked={single || config.auto_render_bypass}
                disabled={single}
                onChange={(value) => set({ auto_render_bypass: value })}
              />
            </div>
          </div>
        </Accordion>
      </div>
    </Section>
  );
}
