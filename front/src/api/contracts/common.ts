import { z } from 'zod';

/** UUID primary keys (every model uses UUIDField). */
export const Id = z.string().uuid();

/** ISO 8601 date-time as DRF writes it (UTC, with offset or Z). */
export const IsoDateTime = z.string().datetime({ offset: true });

/**
 * Decimal amount. A serializer DecimalField arrives as a string
 * ("4860.0000000000"); a Decimal a view puts straight in a Response
 * (`tokens_reserved`, `final_cost`, `required`) arrives as a JSON number,
 * because DRF's JSON encoder turns it into a float. Both are accepted and
 * parsed to a number.
 */
export const Decimal = z
  .union([z.number(), z.string().regex(/^-?\d+(\.\d+)?$/, 'Decimal inválido')])
  .transform((value) => Number(value));

/** For requests: what the frontend sends is always #RRGGBB. */
export const HexColor = z.string().regex(/^#[0-9a-fA-F]{6}$/, 'Color hexadecimal inválido');

/**
 * For responses: the backend stores colors as free text of up to 7 characters
 * (no validator), so another client may have saved "#fff". Use `normalizeHex()`
 * of `@/lib/format` before feeding one to a color input.
 */
export const ColorValue = z.string();

/** Page of results: DRF PageNumberPagination shape. Lists use 25 per page (map B2). */
export function paginated<T extends z.ZodTypeAny>(item: T) {
  return z.object({
    count: z.number().int().nonnegative(),
    next: z.string().nullable(),
    previous: z.string().nullable(),
    results: z.array(item),
  });
}

export const PageQuery = z.object({
  page: z.number().int().positive().optional(),
  page_size: z.number().int().positive().max(100).optional(),
});

export const DEFAULT_PAGE_SIZE = 25;

/** Small reference to a user, embedded where the UI shows "who" (proposed everywhere it appears). */
export const UserRef = z.object({
  id: Id,
  name: z.string(),
  email: z.string().email().optional(),
});
export type UserRef = z.infer<typeof UserRef>;

/** 204 No Content. */
export const NoContent = z.void();

/** Roles in the API: `owner` is derived from Workspace.owner; the rest are WorkspaceMember.Role values. The UI calls `viewer` "Lector". */
export const Role = z.enum(['owner', 'admin', 'editor', 'viewer']);
export type Role = z.infer<typeof Role>;

/** WorkspaceMember.Role (what can be assigned). */
export const MemberRole = z.enum(['admin', 'editor', 'viewer']);
export type MemberRole = z.infer<typeof MemberRole>;

/** ScheduledPost.Platform */
export const Platform = z.enum(['TIKTOK', 'INSTAGRAM_REELS', 'YOUTUBE_SHORTS']);
export type Platform = z.infer<typeof Platform>;

/** SubscriptionPlan.max_resolution / render_resolution */
export const RenderResolution = z.enum(['720p', '1080p', '4K']);
export type RenderResolution = z.infer<typeof RenderResolution>;

/** Error body of hand-written views. */
export const ErrorBody = z
  .object({
    error: z.string().optional(),
    detail: z.string().optional(),
    code: z.string().optional(),
    required: Decimal.optional(),
  })
  .passthrough();
