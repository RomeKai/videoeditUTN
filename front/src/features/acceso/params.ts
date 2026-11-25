/** `searchParams` of a Next 15 page. */
export type SearchParams = Promise<Record<string, string | string[] | undefined>>;

/** First value of a query parameter, or null. */
export function one(value: string | string[] | undefined): string | null {
  const first = Array.isArray(value) ? value[0] : value;
  return first ? first : null;
}
