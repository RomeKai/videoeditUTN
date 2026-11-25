import type { UsageDay } from '@/api/contracts/billing';
import { cx } from '@/lib/cx';
import { formatNumber } from '@/lib/format';
import { monthLong, monthName } from './period';
import styles from './uso.module.css';

const PLOT_HEIGHT = 180;
const MONTH_SHORT = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'] as const;

export interface UsageTotals {
  projects: number;
  renders: number;
  seo: number;
  total: number;
}

export function usageTotals(days: ReadonlyArray<UsageDay>): UsageTotals {
  const totals = days.reduce(
    (sum, day) => ({ projects: sum.projects + day.projects, renders: sum.renders + day.renders, seo: sum.seo + day.seo }),
    { projects: 0, renders: 0, seo: 0 },
  );
  return { ...totals, total: totals.projects + totals.renders + totals.seo };
}

/** A round step so that three grid lines cover the highest day: 6.507 → 2.000. */
export function gridStep(max: number): number {
  if (max <= 0) return 1;
  const rough = max / 3;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const nice = normalized >= 5 ? 5 : normalized >= 2 ? 2 : 1;
  return nice * magnitude;
}

function segment(value: number, scale: number): number {
  // A day with a small charge still shows: 3 px at least.
  return value > 0 ? Math.max(3, Math.round(value * scale)) : 0;
}

export interface UsageChartProps {
  /** `YYYY-MM` */
  month: string;
  days: ReadonlyArray<UsageDay>;
  /** `YYYY-MM-DD` of today, to mark the day and mute what has not happened yet. */
  today: string;
}

/**
 * G1 · Consumo por día: one stacked bar per day (projects, renders, SEO).
 * The drawing is decorative for assistive tech; the same numbers are in the
 * legend and in the `aria-label`, which names the total and the busiest day.
 */
export function UsageChart({ month, days, today }: UsageChartProps) {
  const totals = usageTotals(days);
  const sums = days.map((day) => day.projects + day.renders + day.seo);
  const max = Math.max(0, ...sums);
  const scale = max > 0 ? PLOT_HEIGHT / max : 0;
  const step = gridStep(max);
  const lines = max > 0 ? [0, 1, 2, 3].map((index) => index * step).filter((value) => value <= max) : [0];
  const ranked = sums
    .map((value, index) => ({ value, index }))
    .filter((item) => item.value > 0)
    .sort((a, b) => b.value - a.value);
  const peaks = new Set(ranked.slice(0, 2).map((item) => item.index));
  const top = ranked[0];
  const todayNumber = today.startsWith(month) ? Number(today.slice(8)) : -10;
  const short = MONTH_SHORT[Number(month.split('-')[1] ?? 1) - 1] ?? '';
  const label =
    max > 0 && top
      ? `Consumo por día en ${monthLong(month)}. Total cobrado ${formatNumber(totals.total)} tokens: proyectos ${formatNumber(totals.projects)}, renders ${formatNumber(totals.renders)}, SEO ${formatNumber(totals.seo)}. El día de mayor consumo es el ${top.index + 1} de ${monthName(month)}, con ${formatNumber(top.value)} tokens.`
      : `Consumo por día en ${monthLong(month)}. Todavía no hubo consumo.`;

  return (
    <>
      <ul className={styles.legend} aria-label="Total del mes por tipo">
        <li>
          <span className={cx(styles.key, styles.s1)} aria-hidden="true" />
          Proyectos <span className={cx('num', styles.n)}>{formatNumber(totals.projects)}</span>
        </li>
        <li>
          <span className={cx(styles.key, styles.s2)} aria-hidden="true" />
          Renders <span className={cx('num', styles.n)}>{formatNumber(totals.renders)}</span>
        </li>
        <li>
          <span className={cx(styles.key, styles.s3)} aria-hidden="true" />
          SEO <span className={cx('num', styles.n)}>{formatNumber(totals.seo)}</span>
        </li>
      </ul>
      <div className={styles.chart} role="img" aria-label={label}>
        <div className={styles.plot}>
          {lines.map((value) => (
            <div key={value} className={styles.gline} style={{ bottom: Math.round(value * scale) }}>
              <span className="num">{formatNumber(value, 0)}</span>
            </div>
          ))}
          <div className={styles.cols}>
            {days.map((day, index) => {
              const number = index + 1;
              const sum = sums[index] ?? 0;
              const future = day.date > today;
              const isToday = day.date === today;
              // Day 1, every fifth day and today; a fixed label right next to today's would collide with it.
              const showLabel = isToday || ((number === 1 || number % 5 === 0) && Math.abs(number - todayNumber) > 1);
              return (
                <div
                  key={day.date}
                  className={cx(styles.day, isToday && styles.today)}
                  title={future ? `${number} ${short}` : `${number} ${short}: ${formatNumber(sum)} tokens cobrados`}
                >
                  {peaks.has(index) ? <span className={cx('num', styles.peak)}>{formatNumber(sum, 0)}</span> : null}
                  <div className={styles.bar}>
                    {day.seo > 0 ? <div className={styles.s3} style={{ height: segment(day.seo, scale) }} /> : null}
                    {day.renders > 0 ? <div className={styles.s2} style={{ height: segment(day.renders, scale) }} /> : null}
                    {day.projects > 0 ? <div className={styles.s1} style={{ height: segment(day.projects, scale) }} /> : null}
                  </div>
                  <span className={cx('num', styles.dlabel)}>{showLabel ? number : ''}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}
