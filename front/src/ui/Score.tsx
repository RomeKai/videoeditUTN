import { cx } from '@/lib/cx';
import styles from './Patterns.module.css';

export type ScoreBand = 'Excelente' | 'Bueno' | 'Flojo';

/** Band of the virality score (map 9.1.5). No invented sub-scores. */
export function scoreBand(score: number): ScoreBand {
  if (score >= 85) return 'Excelente';
  if (score >= 70) return 'Bueno';
  return 'Flojo';
}

export interface ScoreProps {
  /** 0–100 (VideoClip.virality_score). */
  value: number;
  showBand?: boolean;
  className?: string;
}

/** Score in a row: accent bar, number and band (kit: .score). The accent is reserved for scores and review. */
export function Score({ value, showBand = true, className }: ScoreProps) {
  const safe = Math.min(100, Math.max(0, Math.round(value)));
  const band = scoreBand(safe);
  return (
    <span className={cx(styles.score, className)} aria-label={`Puntaje ${safe} de 100: ${band.toLowerCase()}`}>
      <span className={styles.scoreBar} aria-hidden="true">
        <span style={{ width: `${safe}%` }} />
      </span>
      <span className="num">{safe}</span>
      {showBand ? <span className={styles.band}>{band}</span> : null}
    </span>
  );
}
