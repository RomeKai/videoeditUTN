import type { CSSProperties } from 'react';
import type { EditingStyle, SubtitlePosition, SubtitleSize } from '@/api/contracts/projects';
import { VideoThumb, cx } from '@/ui';
import styles from './NewProject.module.css';

const SAMPLE_WORDS = ['la', 'regla', 'de', 'los', 'tres', 'sí', 'siempre', 'funciona'];
const FONT_PX: Record<SubtitleSize, number> = { small: 9, medium: 11, large: 13 };
const PLACE: Record<SubtitlePosition, CSSProperties> = { top: { top: '12%' }, center: { top: '44%' }, bottom: { bottom: '14%' } };

export interface CaptionSampleProps {
  enabled: boolean;
  size: SubtitleSize;
  position: SubtitlePosition;
  color: string;
  wordsPerLine: number;
  style: EditingStyle;
  hue?: string;
  className?: string;
}

/** Live sample of the subtitles over a placeholder frame (map C1 · Subtítulos). Two segments: the spoken one takes the color. */
export function CaptionSample({ enabled, size, position, color, wordsPerLine, style, hue = '#4a4038', className }: CaptionSampleProps) {
  const count = Math.max(1, wordsPerLine);
  const first = SAMPLE_WORDS.slice(0, count).join(' ');
  const second = SAMPLE_WORDS.slice(count, count * 2).join(' ');
  const loud = style === 'dynamic' || style === 'hormozi';
  return (
    <span className={cx(styles.sample, className)}>
      <VideoThumb ratio="9:16" hue={hue} captions={false} width="100%" />
      {enabled ? (
        <span
          className={cx(styles.cap, style === 'vlog' && styles.capBox)}
          style={{ ...PLACE[position], fontSize: FONT_PX[size], fontWeight: loud ? 700 : 600, textTransform: loud ? 'uppercase' : undefined }}
          data-testid="caption-sample"
        >
          {second ? (
            <>
              <span>{first}</span>
              <br />
              <span style={{ color }}>{second}</span>
            </>
          ) : (
            <span style={{ color }}>{first}</span>
          )}
        </span>
      ) : null}
    </span>
  );
}

const STYLE_SAMPLE: Record<Exclude<EditingStyle, 'custom'>, { hue: string; cap: CSSProperties; text: [string, string]; box?: boolean }> = {
  dynamic: { hue: '#4b3a52', cap: { bottom: '20%', fontSize: 8, fontWeight: 700, textTransform: 'uppercase' }, text: ['no es el ', 'precio'] },
  minimalist: { hue: '#39424a', cap: { bottom: '12%', fontSize: 6.5, fontWeight: 500 }, text: ['no es el precio', ''] },
  vlog: { hue: '#4a4636', cap: { bottom: '16%', fontSize: 6.5, fontWeight: 600 }, text: ['no es el precio', ''], box: true },
  hormozi: { hue: '#503c38', cap: { top: '44%', fontSize: 12, fontWeight: 800, textTransform: 'uppercase', letterSpacing: '-.02em' }, text: ['', 'precio'] },
};

/** Tiny example of each editing style, drawn (no real media). */
export function StyleSample({ style }: { style: Exclude<EditingStyle, 'custom'> }) {
  const sample = STYLE_SAMPLE[style];
  return (
    <span className={cx(styles.sample, styles.styleThumb)} aria-hidden="true">
      <VideoThumb ratio="9:16" hue={sample.hue} captions={false} width="100%" />
      <span className={cx(styles.cap, sample.box && styles.capBox)} style={sample.cap}>
        <span>{sample.text[0]}</span>
        {sample.text[1] ? <span style={{ color: '#F2C94C' }}>{sample.text[1]}</span> : null}
      </span>
    </span>
  );
}
