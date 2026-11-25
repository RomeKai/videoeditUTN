'use client';

import Link from 'next/link';
import { useId, useState, type ReactNode } from 'react';
import { formatBytes, formatMinutes, formatNumber, formatTokens } from '@/lib/format';
import { Banner, Button, Card, CardHead, Icon, ProgressBar, cx } from '@/ui';
import styles from './NewProject.module.css';

export interface SummaryRow {
  key: string;
  label: ReactNode;
  value: ReactNode;
}

export type CostView =
  /** Nothing to estimate yet (no video, or a video the plan does not take). */
  | { kind: 'none'; reason: string }
  /** `note`: a line under the number ("Describir con IA" is always priced at 720p). */
  | { kind: 'exact'; total: number; note?: string };

export interface UploadProgress {
  loaded: number;
  total: number;
  /** Bytes per second, 0 until it can be measured. */
  speed: number;
}

export interface SummaryProps {
  rows: SummaryRow[];
  /** "Tu plan agrega marca de agua": name of the plan that removes it, or null when the plan has none. */
  watermarkPlan: string | null | undefined;
  cost: CostView;
  detail: SummaryRow[];
  available: number | null;
  after: { label: ReactNode; value: number } | null;
  /** Tokens missing: the button turns into "Comprar tokens". */
  missing: number | null;
  required: number | null;
  etaSeconds: number | null;
  /** "Renderizar sin revisar": the second reservation, for the render of the clips. */
  renderNote: ReactNode | null;
  /** Small print under the card. */
  note: ReactNode;
  cta: CtaProps;
}

export interface CtaProps {
  phase: 'idle' | 'uploading' | 'creating' | 'opening';
  upload: UploadProgress | null;
  onCreate: () => void;
  onCancel: () => void;
  /** Why "Crear proyecto" cannot be used yet. Shown under the button. */
  blocked: ReactNode | null;
  /** Role: disabled with the tooltip, never hidden. */
  roleReason?: string;
  canBuy: boolean;
  /** Line under the button when everything is ready. */
  hint: ReactNode;
  error: ReactNode | null;
}

function remainingText(upload: UploadProgress): string {
  if (upload.speed <= 0) return 'Calculando…';
  const seconds = Math.max(1, Math.round((upload.total - upload.loaded) / upload.speed));
  return `${formatBytes(Math.round(upload.speed))}/s · quedan ${seconds < 60 ? `${seconds} s` : formatMinutes(seconds)}`;
}

export function uploadPercent(upload: UploadProgress | null): number {
  if (!upload || upload.total <= 0) return 0;
  return Math.min(100, Math.floor((upload.loaded / upload.total) * 100));
}

function UploadStatus({ upload, onCancel, compact }: { upload: UploadProgress | null; onCancel: () => void; compact?: boolean }) {
  const percent = uploadPercent(upload);
  const sent = upload !== null && upload.loaded >= upload.total && upload.total > 0;
  return (
    <>
      <div className={styles.uploading} role="status" aria-label="Subiendo el video">
        <div className={cx(styles.row, styles.between)}>
          <span className={styles.strong}>{sent ? 'Creando el proyecto…' : 'Subiendo el video'}</span>
          <span className={cx('num', styles.strong)}>{percent} %</span>
        </div>
        <ProgressBar value={percent} label="Progreso de la subida" />
        {upload ? (
          <span className={cx('num t-label muted', styles.row, styles.between, styles.wrap)}>
            <span>
              {formatBytes(upload.loaded)} de {formatBytes(upload.total)}
            </span>
            <span>{sent ? 'Casi listo' : remainingText(upload)}</span>
          </span>
        ) : null}
      </div>
      {/* Once the whole video left the browser the server is already creating the project: cancelling would not undo it. */}
      {sent ? null : (
        <Button variant="secondary" onClick={onCancel} block={!compact}>
          Cancelar
        </Button>
      )}
      {compact ? null : (
        <span className={cx(styles.hint, styles.center)}>
          {sent ? 'El video ya está subido. En un momento se abre el proyecto.' : 'No cierres esta pestaña hasta que termine. Después se abre el proyecto.'}
        </span>
      )}
    </>
  );
}

function CreateButton({ cta, missing, block }: { cta: CtaProps; missing: number | null; block?: boolean }) {
  if (missing !== null && cta.phase === 'idle') {
    return (
      <Button variant="primary" size="lg" block={block} href="/uso/comprar" disabledReason={cta.canBuy ? undefined : 'Pedile a un admin que compre tokens'}>
        Comprar tokens
      </Button>
    );
  }
  return (
    <Button
      variant="primary"
      size="lg"
      block={block}
      onClick={cta.onCreate}
      loading={cta.phase === 'creating' || cta.phase === 'opening'}
      disabled={cta.blocked !== null && !cta.roleReason}
      disabledReason={cta.roleReason}
    >
      {cta.phase === 'opening' ? 'Abriendo el proyecto…' : cta.phase === 'creating' ? 'Creando el proyecto…' : 'Crear proyecto'}
    </Button>
  );
}

function CostNumber({ cost }: { cost: CostView }) {
  if (cost.kind === 'none') {
    return (
      <span className={cx('num subtle', styles.totalNum)} aria-label="Sin estimación">
        —
      </span>
    );
  }
  return (
    <span className={cx(styles.row, styles.baseline)}>
      <span className={cx('num', styles.totalNum)} data-testid="cost-total">
        {formatNumber(cost.total)}
      </span>
      <span className="muted">tokens</span>
    </span>
  );
}

function Rows({ rows, className, id }: { rows: SummaryRow[]; className?: string; id?: string }) {
  return (
    <dl className={className} id={id}>
      {rows.map((row) => (
        <div className={styles.sumRow} key={row.key}>
          <dt>{row.label}</dt>
          <dd>{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function Eta({ seconds }: { seconds: number | null }) {
  if (seconds === null) return null;
  return (
    <p className={styles.eta}>
      <span className={cx('muted', styles.etaIco)}>
        <Icon name="clock" size={14} />
      </span>
      <span>
        Listo para revisar en <span className={cx('num', styles.strong)}>≈ {formatMinutes(seconds)}</span>
      </span>
    </p>
  );
}

function balanceRows({ available, after, missing }: Pick<SummaryProps, 'available' | 'after' | 'missing'>): SummaryRow[] {
  return [
    ...(available !== null ? [{ key: 'available', label: 'Saldo disponible', value: <span className="num">{formatNumber(available)}</span> }] : []),
    ...(missing !== null
      ? [{ key: 'missing', label: <span className={styles.errText}>Te faltan</span>, value: <span className={cx('num', styles.strong, styles.errText)}>{formatNumber(missing)}</span> }]
      : after
        ? [{ key: 'after', label: after.label, value: <span className={cx('num', styles.strong)}>{formatNumber(after.value)}</span> }]
        : []),
  ];
}

function CtaNotes({ cta, missing, required, available }: { cta: CtaProps; missing: number | null; required: number | null; available: number | null }) {
  if (missing !== null && required !== null) {
    return (
      <span className={cx(styles.hint, styles.center)}>
        Necesitás <span className="num">{formatTokens(required)}</span> y tenés <span className="num">{formatNumber(available ?? 0)}</span>.
      </span>
    );
  }
  return <span className={cx(styles.hint, styles.center)}>{cta.blocked ?? cta.hint}</span>;
}

/** Fixed summary at the right of C1: what is being created, what it costs and the button. */
export function SummaryCard(props: SummaryProps) {
  const { rows, watermarkPlan, cost, detail, missing, required, available, etaSeconds, renderNote, note, cta } = props;
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const busy = cta.phase === 'uploading';
  return (
    <aside className={styles.side} aria-label="Resumen del proyecto">
      <Card>
        <CardHead title={<span className="t-h3">Resumen</span>} />
        <Rows rows={rows} className={styles.sumRows} />
        {watermarkPlan !== undefined ? (
          <div className={styles.sumNote}>
            <Banner tone="neutral" live="off" title="Tu plan agrega marca de agua">
              {watermarkPlan ? `Pasate al plan ${watermarkPlan} para sacarla. ` : null}
              <Link className="lnk" href="/uso/planes">
                Ver planes
              </Link>
            </Banner>
          </div>
        ) : null}
        <div className={styles.sumTotal}>
          <div className={styles.stack2}>
            <span className="t-small muted">Costo estimado</span>
            <CostNumber cost={cost} />
            {cost.kind === 'exact' && cost.note ? <span className="t-small muted">{cost.note}</span> : null}
          </div>
          {cost.kind === 'none' ? <p className="t-small muted">{cost.reason}</p> : null}
          {cost.kind !== 'none' && detail.length > 0 ? (
            <div className={styles.detail}>
              <button type="button" className={styles.detailBtn} aria-expanded={open} aria-controls={open ? detailId : undefined} onClick={() => setOpen((value) => !value)}>
                <span className={cx(styles.chev, open && styles.chevOpen)}>
                  <Icon name="chevron-right" size={14} />
                </span>
                {open ? 'Ocultar detalle' : 'Ver detalle'}
              </button>
              {open ? <Rows rows={detail} className={styles.detailRows} id={detailId} /> : null}
            </div>
          ) : null}
          <Rows rows={balanceRows(props)} className={styles.balance} />
          {renderNote ? (
            <p className={cx('t-small muted', styles.renderNote)} data-testid="render-note">
              {renderNote}
            </p>
          ) : null}
          <Eta seconds={cost.kind === 'none' ? null : etaSeconds} />
        </div>
        <div className={styles.sumCta}>
          {cta.error ? <Banner tone="err" title={cta.error} /> : null}
          {busy ? (
            <UploadStatus upload={cta.upload} onCancel={cta.onCancel} />
          ) : (
            <>
              <CreateButton cta={cta} missing={missing} block />
              <CtaNotes cta={cta} missing={missing} required={required} available={available} />
            </>
          )}
        </div>
      </Card>
      <p className={cx(styles.hint, styles.sideNote)}>{note}</p>
    </aside>
  );
}

/** Phone (M4): the cost and the button stay fixed at the bottom; the detail opens above them. */
export function SummaryBar(props: SummaryProps) {
  const { rows, watermarkPlan, cost, detail, missing, required, available, after, etaSeconds, renderNote, cta } = props;
  const [open, setOpen] = useState(false);
  const id = useId();
  const busy = cta.phase === 'uploading';
  const headline =
    cost.kind === 'exact' ? formatTokens(cost.total) : 'Sin estimación';
  return (
    <div className={styles.foot} role="region" aria-label="Costo y creación del proyecto">
      {open && !busy ? (
        <div className={styles.footSum} id={id}>
          <Rows rows={rows} className={styles.footRows} />
          {cost.kind !== 'none' ? <Rows rows={detail} className={styles.footRows} /> : <p className="t-small muted">{cost.reason}</p>}
          {cost.kind === 'exact' && cost.note ? <p className="t-small muted">{cost.note}</p> : null}
          {renderNote ? <p className="t-small muted">{renderNote}</p> : null}
          <Rows rows={balanceRows(props)} className={styles.footRows} />
          {watermarkPlan !== undefined ? (
            <p className="t-small muted">
              Tu plan agrega marca de agua.{' '}
              <Link className="lnk" href="/uso/planes">
                Ver planes
              </Link>
            </p>
          ) : null}
          <Eta seconds={cost.kind === 'none' ? null : etaSeconds} />
        </div>
      ) : null}
      {cta.error ? <Banner className={styles.footError} tone="err" title={cta.error} /> : null}
      {busy ? (
        <div className={styles.footUpload}>
          <UploadStatus upload={cta.upload} onCancel={cta.onCancel} compact />
        </div>
      ) : (
        <>
          <div className={styles.footBar}>
            <button
              type="button"
              className={styles.footCost}
              aria-expanded={open}
              aria-controls={open ? id : undefined}
              aria-label={`Costo estimado: ${headline}. ${open ? 'Ocultar' : 'Ver'} detalle`}
              onClick={() => setOpen((value) => !value)}
            >
              <span className={cx(styles.row, styles.strong)}>
                <span className="num">{headline}</span>
                <span className={cx(styles.chev, open && styles.chevUp)}>
                  <Icon name="chevron-up" size={14} />
                </span>
              </span>
              <span className={cx('num', styles.footSub, missing !== null && styles.errText)}>
                {missing !== null ? `Te faltan ${formatNumber(missing)}` : after ? `Saldo después ${formatNumber(after.value)}` : cost.kind === 'none' ? cost.reason : 'Ver detalle'}
              </span>
            </button>
            <CreateButton cta={cta} missing={missing} />
          </div>
          {cta.blocked || missing !== null ? (
            <div className={styles.footNote}>
              <CtaNotes cta={cta} missing={missing} required={required} available={available} />
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
