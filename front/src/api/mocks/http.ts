import { delay, http, HttpResponse, type HttpHandler } from 'msw';
import type { ZodTypeAny } from 'zod';
import { can, type Action, type PermissionContext } from '@/lib/permissions';
import { API_URL, WORKSPACE_HEADER } from '../config';
import type { Role } from '../contracts/common';
import type { AnyEndpoint, RequestBody, ResponseOf } from '../endpoint';
import type { MockDb, MockUser, MockWorkspace } from './db';
import { roleIn, tick } from './sim';
import { loadDb, saveDb, userIdFromToken } from './state';

/** Thrown by a resolver to answer with an error status and body. */
export class MockHttpError extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown = {},
  ) {
    super(`Mock ${status}`);
  }
}

export const notFound = (detail = 'No encontrado.') => new MockHttpError(404, { detail });
export const badRequest = (body: Record<string, unknown>) => new MockHttpError(400, body);
export const conflict = (error: string, code?: string) => new MockHttpError(409, code ? { error, code } : { error });
const ROLE_DENIED = {
  viewer: 'Con el rol Lector podés ver, pero no crear ni cambiar nada. Pedile acceso a un admin.',
  admins: 'Solo el dueño o un admin del espacio pueden hacer esto. Pedile acceso a un admin.',
  editorDelete: 'Como Editor solo podés borrar los proyectos que subiste vos.',
};
export const forbidden = (detail = 'No tenés permiso para hacer esto.') => new MockHttpError(403, { detail, code: 'permission_denied' });

export interface MockContext<E extends AnyEndpoint> {
  db: MockDb;
  now: number;
  request: Request;
  params: Record<string, string>;
  query: URLSearchParams;
  /** JSON body, already validated against the endpoint's request schema. */
  body: RequestBody<E>;
  /** Raw multipart form (creation of projects and brand kits). */
  form: FormData | null;
  /** Signed-in user. Throws 401 for a public endpoint called without a session. */
  user: MockUser;
  /** Null on public endpoints called without a session. */
  maybeUser: MockUser | null;
  /** Active workspace (X-Workspace-Id, else the user's active one). */
  workspace: MockWorkspace;
  role: Role;
  /** 403 unless the role may do the action (matrix of map 1.7). */
  require: (action: Action, context?: PermissionContext) => void;
}

export interface MockResult<E extends AnyEndpoint> {
  status?: number;
  data: ResponseOf<E> extends void ? undefined : unknown;
  /** Skip the schema check (non-JSON bodies). */
  raw?: boolean;
}

type Resolver<E extends AnyEndpoint> = (context: MockContext<E>) => MockResult<E> | Promise<MockResult<E>>;

/** Ids of the endpoints that have a handler: the coverage test compares it with the contract. */
export const mockedEndpointIds = new Set<string>();

const METHODS = { GET: http.get, POST: http.post, PATCH: http.patch, PUT: http.put, DELETE: http.delete } as const;

/** Latency of the mocked API in the browser, so loading states are visible. Zero under test. */
function latency(): number {
  if (process.env.NODE_ENV === 'test') return 0;
  if (typeof window !== 'undefined' && (window as unknown as { __OC_MOCK_DELAY__?: number }).__OC_MOCK_DELAY__ !== undefined) {
    return (window as unknown as { __OC_MOCK_DELAY__: number }).__OC_MOCK_DELAY__;
  }
  return 180;
}

function resolveWorkspace(db: MockDb, user: MockUser, request: Request): { workspace: MockWorkspace; role: Role } | null {
  const candidates = [request.headers.get(WORKSPACE_HEADER), user.active_workspace_id];
  for (const id of candidates) {
    if (!id) continue;
    const workspace = db.workspaces.find((item) => item.id === id);
    const role = workspace ? roleIn(db, workspace.id, user.id) : null;
    if (workspace && role) return { workspace, role };
  }
  for (const workspace of db.workspaces) {
    const role = roleIn(db, workspace.id, user.id);
    if (role) return { workspace, role };
  }
  return null;
}

/**
 * Registers the mock of one endpoint of the contract. The wrapper loads the
 * database, moves the simulations forward, authenticates, runs the resolver,
 * checks the answer against the endpoint's schema (a mock that drifts from the
 * contract fails loudly) and saves the database.
 */
export function route<E extends AnyEndpoint>(endpoint: E, resolver: Resolver<E>): HttpHandler {
  mockedEndpointIds.add(endpoint.id);
  return METHODS[endpoint.method](`${API_URL}${endpoint.path}`, async ({ request, params }) => {
    await delay(latency());
    const db = loadDb();
    const now = Date.now();
    tick(db, now);
    try {
      if (db.flags.serverDown && endpoint.method === 'GET' && endpoint.id !== 'system.status') {
        throw new MockHttpError(500, { detail: 'Error interno del servidor.' });
      }

      if (db.flags.maintenance && endpoint.id !== 'system.status') {
        throw new MockHttpError(503, { detail: 'Service temporarily unavailable.' });
      }

      const header = request.headers.get('Authorization');
      const userId = header?.startsWith('Bearer ') ? userIdFromToken(header.slice(7), 'access') : null;
      const maybeUser = db.flags.sessionExpired ? null : (db.users.find((item) => item.id === userId) ?? null);
      const needsAuth = endpoint.auth === undefined || endpoint.auth === true;
      if (needsAuth && !maybeUser) {
        throw new MockHttpError(401, { detail: 'Given token not valid for any token type', code: 'token_not_valid' });
      }
      const scoped = maybeUser ? resolveWorkspace(db, maybeUser, request) : null;

      let body: unknown;
      let form: FormData | null = null;
      if (endpoint.request && endpoint.method !== 'GET') {
        if (endpoint.bodyType === 'multipart') {
          form = await request.formData();
        } else {
          const text = await request.text();
          let json: unknown = {};
          try {
            json = text ? JSON.parse(text) : {};
          } catch {
            throw badRequest({ detail: 'JSON parse error' });
          }
          const parsed = (endpoint.request as ZodTypeAny).safeParse(json);
          if (!parsed.success) {
            // Shape DRF gives to validation errors: { field: [messages] }.
            const errors: Record<string, string[]> = {};
            for (const issue of parsed.error.issues) {
              const key = String(issue.path[0] ?? 'non_field_errors');
              errors[key] = [...(errors[key] ?? []), issue.message === 'Required' ? 'Este campo es obligatorio.' : issue.message];
            }
            throw badRequest(errors);
          }
          body = parsed.data;
        }
      }

      const context: MockContext<E> = {
        db,
        now,
        request,
        params: params as Record<string, string>,
        query: new URL(request.url).searchParams,
        body: body as RequestBody<E>,
        form,
        maybeUser,
        get user(): MockUser {
          if (!maybeUser) throw new MockHttpError(401, { detail: 'Las credenciales de autenticación no se proveyeron.' });
          return maybeUser;
        },
        get workspace(): MockWorkspace {
          if (!scoped) throw new MockHttpError(400, { error: 'El usuario no tiene un Workspace asignado.' });
          return scoped.workspace;
        },
        get role(): Role {
          if (!scoped) throw forbidden();
          return scoped.role;
        },
        require(action, permissionContext = {}) {
          if (scoped && can(scoped.role, action, { userId: maybeUser?.id, ...permissionContext })) return;
          // The sentences of the backend (back/apps/core/permissions.py · DENIED).
          throw forbidden(scoped?.role === 'viewer' ? ROLE_DENIED.viewer : scoped?.role === 'editor' && action === 'project.delete' ? ROLE_DENIED.editorDelete : ROLE_DENIED.admins);
        },
      };

      const result = await resolver(context);
      saveDb(db);
      if (result.data === undefined) return new HttpResponse(null, { status: result.status ?? 204 });
      if (result.raw) {
        return new HttpResponse(String(result.data), { status: result.status ?? 200, headers: { 'Content-Type': 'text/csv; charset=utf-8' } });
      }
      const check = endpoint.response.safeParse(result.data);
      if (!check.success) {
        const issue = check.error.issues[0];
        console.error(`[mocks] ${endpoint.id}: la respuesta no cumple el contrato`, check.error.issues);
        return HttpResponse.json(
          { detail: `Mock fuera de contrato en ${endpoint.id}: ${issue?.path.join('.')} ${issue?.message}` },
          { status: 500 },
        );
      }
      return HttpResponse.json(result.data as Record<string, unknown>, { status: result.status ?? endpoint.successStatus ?? 200 });
    } catch (error) {
      // Side effects of the tick (finished renders, refunds) are kept even when the request fails.
      saveDb(db);
      if (error instanceof MockHttpError) return HttpResponse.json(error.body as Record<string, unknown>, { status: error.status });
      throw error;
    }
  });
}

/** Decimal as DRF writes it: a string with fixed places. */
export function dec(value: number, places = 2): string {
  return value.toFixed(places);
}

/** Page of a list, DRF style. `page_size` defaults to 25. */
export function paginate<T>(items: T[], query: URLSearchParams, path: string): { count: number; next: string | null; previous: string | null; results: T[] } {
  const page = Math.max(1, Number(query.get('page') ?? '1') || 1);
  const size = Math.min(100, Math.max(1, Number(query.get('page_size') ?? '25') || 25));
  const start = (page - 1) * size;
  if (start >= items.length && page > 1) throw notFound('Página inválida.');
  const link = (target: number) => {
    const params = new URLSearchParams(query);
    params.set('page', String(target));
    return `${API_URL}${path}?${params.toString()}`;
  };
  return {
    count: items.length,
    next: start + size < items.length ? link(page + 1) : null,
    previous: page > 1 ? link(page - 1) : null,
    results: items.slice(start, start + size),
  };
}
