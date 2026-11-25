/**
 * Month and day helpers of Uso. The API counts months and days in the workspace's time zone
 * (`billing.transactions`, `billing.usage`), so these screens do too, whatever the browser's zone:
 * every month key (`YYYY-MM`), "today" and the default selected month come from here.
 */
export const WORKSPACE_TIME_ZONE = 'America/Argentina/Buenos_Aires';

const MONTH_NAMES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre'] as const;

const DAY_PARTS = new Intl.DateTimeFormat('en-CA', { timeZone: WORKSPACE_TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' });

/** `YYYY-MM-DD` of a moment in the workspace's time zone. */
export function workspaceDay(date: Date = new Date()): string {
  const parts = DAY_PARTS.formatToParts(date);
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? '';
  return `${part('year')}-${part('month')}-${part('day')}`;
}

/** `YYYY-MM` of a moment in the workspace's time zone. */
export function monthKey(date: Date = new Date()): string {
  return workspaceDay(date).slice(0, 7);
}

export function shiftMonth(key: string, delta: number): string {
  const [year = 0, month = 1] = key.split('-').map(Number);
  const index = year * 12 + (month - 1) + delta;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, '0')}`;
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
