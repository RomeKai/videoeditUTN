/** Month helpers of Uso: keys are `YYYY-MM` in the browser's time zone, as the API filter expects. */
const MONTH_NAMES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'] as const;

export function monthKey(date: Date = new Date()): string {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
}

export function shiftMonth(key: string, delta: number): string {
  const [year = 0, month = 1] = key.split('-').map(Number);
  return monthKey(new Date(year, month - 1 + delta, 1));
}

/** "noviembre" */
export function monthName(key: string): string {
  const month = Number(key.split('-')[1] ?? 1);
  return MONTH_NAMES[month - 1] ?? '';
}

/** "noviembre de 2026" */
export function monthLong(key: string): string {
  return `${monthName(key)} de ${key.split('-')[0]}`;
}

/** "Noviembre 2026" */
export function monthTitle(key: string): string {
  const name = monthName(key);
  return `${name.charAt(0).toUpperCase()}${name.slice(1)} ${key.split('-')[0]}`;
}

/** The current month and the `count - 1` before it, newest first. */
export function recentMonths(count: number, from: Date = new Date()): string[] {
  const current = monthKey(from);
  return Array.from({ length: count }, (_, index) => shiftMonth(current, -index));
}
