import type { z, ZodTypeAny } from 'zod';

export type HttpMethod = 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';

/** Whether the endpoint is in the Django backend today. */
export type BackendStatus =
  /** Exists as described: field names copied from the DRF view/serializer cited in `source`. */
  | 'exists'
  /** Exists, but the frontend needs more than it gives today (see `note` and `gap`). */
  | 'partial'
  /** Does not exist: proposed shape for the gap of `documents/producto/mapa-de-pantallas.md` §8. */
  | 'proposed';

export interface EndpointError {
  status: number;
  /** When it happens and what the UI does with it (Spanish, goes to CONTRACT.md). */
  when: string;
}

export interface EndpointBackend {
  status: BackendStatus;
  /** DRF view / serializer / model that backs it (for `exists` and `partial`). */
  source?: string;
  /** Gap number(s) in section 8 of the screen map (for `partial` and `proposed`). */
  gap?: number | number[];
  note?: string;
}

type ExtractParams<Path extends string> = Path extends `${string}:${infer Param}/${infer Rest}`
  ? Param | ExtractParams<`/${Rest}`>
  : Path extends `${string}:${infer Param}`
    ? Param
    : never;

export type PathParams<Path extends string> = [ExtractParams<Path>] extends [never]
  ? undefined
  : { [Key in ExtractParams<Path>]: string };

export interface EndpointDef<
  Path extends string = string,
  Req extends ZodTypeAny | undefined = ZodTypeAny | undefined,
  Query extends ZodTypeAny | undefined = ZodTypeAny | undefined,
  Res extends ZodTypeAny = ZodTypeAny,
> {
  /** Stable id: `<domain>.<name>`. */
  id: string;
  method: HttpMethod;
  /** Path from the API origin, with `:param` placeholders and DRF's trailing slash. */
  path: Path;
  /** One line, Spanish: what it does. */
  summary: string;
  /** Body schema. */
  request?: Req;
  /** How the body travels. Default json. */
  bodyType?: 'json' | 'multipart';
  /** Query string schema. */
  query?: Query;
  /** Success body schema (`z.void()` for 204). */
  response: Res;
  /** What the body is when it is not JSON. */
  responseType?: 'json' | 'text' | 'blob';
  /** Success status code, for the documentation. Default 200. */
  successStatus?: number;
  /**
   * true (default): needs a session; 401 triggers one token refresh.
   * false: public, no Authorization header.
   * 'optional': public, but sends the token when there is a session.
   */
  auth?: boolean | 'optional';
  /** False for endpoints that are not scoped to a workspace (no X-Workspace-Id). Default true. */
  workspaceScoped?: boolean;
  /**
   * How long to wait for the answer, in ms, when the default (`REQUEST_TIMEOUT_MS`) does not fit:
   * more for an endpoint that answers slowly on purpose, `false` for none. The time a multipart
   * body takes to upload never counts.
   */
  timeoutMs?: number | false;
  errors: EndpointError[];
  backend: EndpointBackend;
  /** Screen codes of the map that use it (A1, D2, J3…). */
  screens: string[];
}

export type AnyEndpoint = EndpointDef<string, ZodTypeAny | undefined, ZodTypeAny | undefined, ZodTypeAny>;

type Defined<T> = Exclude<T, undefined>;
export type RequestBody<E extends AnyEndpoint> = [Defined<E['request']>] extends [never]
  ? undefined
  : Defined<E['request']> extends ZodTypeAny
    ? z.input<Defined<E['request']>>
    : undefined;
export type QueryOf<E extends AnyEndpoint> = [Defined<E['query']>] extends [never]
  ? undefined
  : Defined<E['query']> extends ZodTypeAny
    ? z.input<Defined<E['query']>>
    : undefined;
export type ResponseOf<E extends AnyEndpoint> = z.output<E['response']>;
export type ParamsOf<E extends AnyEndpoint> = E extends EndpointDef<infer Path, ZodTypeAny | undefined, ZodTypeAny | undefined, ZodTypeAny>
  ? PathParams<Path>
  : undefined;

/** Describes one endpoint, once. Hooks, mocks and CONTRACT.md are all derived from it. */
export function defineEndpoint<
  const Path extends string,
  Req extends ZodTypeAny | undefined = undefined,
  Query extends ZodTypeAny | undefined = undefined,
  Res extends ZodTypeAny = ZodTypeAny,
>(def: EndpointDef<Path, Req, Query, Res>): EndpointDef<Path, Req, Query, Res> {
  return def;
}

/** Replaces `:param` placeholders. Values are URL-encoded. */
export function buildPath(path: string, params?: Record<string, string>): string {
  return path.replace(/:([A-Za-z_]+)/g, (_match, name: string) => {
    const value = params?.[name];
    if (value === undefined) throw new Error(`Falta el parámetro de ruta "${name}" en ${path}`);
    return encodeURIComponent(value);
  });
}

/** Query string from a plain object: skips undefined, null and '', repeats arrays. */
export function buildQuery(query?: Record<string, unknown>): string {
  if (!query) return '';
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue;
    if (Array.isArray(value)) for (const item of value) search.append(key, String(item));
    else search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : '';
}
