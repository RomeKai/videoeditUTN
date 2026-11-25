'use client';

import { useEffect, useState, type CSSProperties } from 'react';
import type { WatermarkPosition } from '@/api/contracts/brandkits';
import { initials } from '@/lib/format';
import { cx } from '@/ui';
import styles from './BrandKits.module.css';
import { loadableUrl } from './kit';

export type PreviewRatio = '9:16' | '1:1' | '16:9';
const RATIO_CLASS: Record<PreviewRatio, string | undefined> = { '9:16': styles.r9x16, '1:1': styles.r1x1, '16:9': styles.r16x9 };

export interface KitColors {
  text: string;
  background: string;
  accent: string;
}

let fontCounter = 0;

/**
 * Loads a font file (a File the user just picked, or a real URL) so the preview
 * can use it. Returns the family to put in `font-family`, or undefined while it
 * loads, when there is no font, or when the browser cannot read the file.
 */
export function useFontFace(source: File | string | null): string | undefined {
  const [loaded, setLoaded] = useState<{ source: File | string; family: string } | null>(null);
  useEffect(() => {
    if (!source || typeof FontFace === 'undefined' || !document.fonts) return;
    let cancelled = false;
    let face: FontFace | null = null;
    fontCounter += 1;
    const family = `oc-kit-font-${fontCounter}`;
    void (async () => {
      try {
        const data = typeof source === 'string' ? `url(${JSON.stringify(source)})` : await source.arrayBuffer();
        face = new FontFace(family, data);
        await face.load();
        if (cancelled) return;
        document.fonts.add(face);
        setLoaded({ source, family });
      } catch {
        // Not a font the browser can read: the preview keeps the system font.
      }
    })();
    return () => {
      cancelled = true;
      if (face) document.fonts.delete(face);
    };
  }, [source]);
  return loaded && loaded.source === source ? loaded.family : undefined;
}

/** Temporary address of a file the user picked, to show it before saving. */
export function useObjectUrl(file: File | null): string | null {
  const [entry, setEntry] = useState<{ file: File; url: string } | null>(null);
  useEffect(() => {
    if (!file || typeof URL.createObjectURL !== 'function') return;
    const url = URL.createObjectURL(file);
    setEntry({ file, url });
    return () => URL.revokeObjectURL(url);
  }, [file]);
  return entry && entry.file === file ? entry.url : null;
}

export interface LogoMarkProps {
  /** Kit name: its initials stand in for the image when there is none to load. */
  name: string;
  src?: string | null;
  colors: KitColors;
  className?: string;
  style?: CSSProperties;
}

/** The kit's watermark: the image when the browser can load it, its initials on the kit's colors otherwise. */
export function LogoMark({ name, src, colors, className, style }: LogoMarkProps) {
  const url = loadableUrl(src);
  return (
    <span
      className={cx(styles.logo, className)}
      style={{ background: url ? 'transparent' : colors.background, color: colors.text, ...style }}
      aria-hidden="true"
    >
      {url ? (
        // A file the user just picked (blob:) or a signed storage URL: next/image cannot optimize either.
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt="" />
      ) : (
        <span style={{ borderBottomColor: colors.accent }}>{initials(name || 'Logo')}</span>
      )}
    </span>
  );
}

export interface KitPreviewProps {
  colors: KitColors;
  name: string;
  /** Loaded font family (see useFontFace). */
  fontFamily?: string;
  /** Null: the kit has no watermark. */
  logo: { src?: string | null } | null;
  position?: WatermarkPosition;
  ratio?: PreviewRatio;
  /** Hatches what the networks cover with their buttons and text (9:16 only). */
  safeZones?: boolean;
  /** Short sample for the small card of the list. */
  compact?: boolean;
  /** The plan adds OneCreator's watermark to every clip. */
  planWatermark?: boolean;
  /** Hue of the placeholder frame. */
  hue?: string;
  className?: string;
}

/** A sample frame of a clip with the kit applied: subtitle colors, font and logo. Purely illustrative. */
export function KitPreview({
  colors,
  name,
  fontFamily,
  logo,
  position = 'bottom_right',
  ratio = '9:16',
  safeZones = false,
  compact = false,
  planWatermark = false,
  hue,
  className,
}: KitPreviewProps) {
  const showSafe = safeZones && ratio === '9:16';
  const line = { background: colors.background, color: colors.text };
  return (
    <span className={cx(styles.pv, RATIO_CLASS[ratio], showSafe && styles.safeOn, className)} aria-hidden="true">
      <span className={styles.pvFrame} style={{ '--f1': hue } as CSSProperties} />
      {showSafe ? (
        <>
          <span className={cx(styles.safe, styles.safeTop)} />
          <span className={cx(styles.safe, styles.safeRight)} />
          <span className={cx(styles.safe, styles.safeBottom)} />
        </>
      ) : null}
      <span className={styles.pvSub} style={{ fontFamily: fontFamily ? `'${fontFamily}', var(--font)` : undefined }}>
        <span className={styles.pvLine} style={line}>
          {compact ? 'Vender es' : 'Vender no es hablar,'}
        </span>
        <span className={styles.pvLine} style={line}>
          {compact ? null : 'es '}
          <span style={{ color: colors.accent }}>escuchar</span>
        </span>
      </span>
      {planWatermark ? <span className={styles.planMark}>OneCreator</span> : null}
      {logo ? <LogoMark name={name} src={logo.src} colors={colors} className={cx(styles.pvLogo, styles[position])} /> : null}
    </span>
  );
}
