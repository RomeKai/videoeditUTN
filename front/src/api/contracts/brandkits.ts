import { z } from 'zod';
import { SLOW_REQUEST_TIMEOUT_MS } from '../config';
import { defineEndpoint } from '../endpoint';
import { ColorValue, HexColor, Id, IsoDateTime, NoContent } from './common';

export const WatermarkPosition = z.enum(['top_left', 'top_right', 'bottom_left', 'bottom_right']);
export type WatermarkPosition = z.infer<typeof WatermarkPosition>;

/** BrandKitSerializer (fields = '__all__'). File fields arrive as URLs. */
export const BrandKit = z.object({
  id: Id,
  workspace: Id,
  user: Id.nullable(),
  name: z.string(),
  description: z.string(),
  primary_font: z.string().nullable(),
  secondary_font: z.string().nullable(),
  primary_color: ColorValue,
  secondary_color: ColorValue,
  accent_color: ColorValue,
  watermark_logo: z.string().nullable(),
  intro_video: z.string().nullable(),
  outro_video: z.string().nullable(),
  is_default: z.boolean(),
  created_at: IsoDateTime,
  /** Proposed (map F2, "con posición de muestra"): corner of the frame where the watermark goes. Absent today: the UI assumes bottom right. */
  watermark_position: WatermarkPosition.optional(),
});
export type BrandKit = z.infer<typeof BrandKit>;

const Upload = z.custom<Blob>((value) => typeof Blob !== 'undefined' && value instanceof Blob, 'Archivo inválido');

/**
 * Multipart body. A file field takes a File to replace it or '' to clear it;
 * leaving it out keeps the current file.
 */
export const BrandKitInput = z.object({
  name: z.string().min(1).max(100),
  description: z.string().optional(),
  primary_color: HexColor.optional(),
  secondary_color: HexColor.optional(),
  accent_color: HexColor.optional(),
  /** .ttf / .otf */
  primary_font: z.union([Upload, z.literal('')]).optional(),
  secondary_font: z.union([Upload, z.literal('')]).optional(),
  /** PNG */
  watermark_logo: z.union([Upload, z.literal('')]).optional(),
  intro_video: z.union([Upload, z.literal('')]).optional(),
  outro_video: z.union([Upload, z.literal('')]).optional(),
  is_default: z.boolean().optional(),
  /** Proposed: see BrandKit.watermark_position. */
  watermark_position: WatermarkPosition.optional(),
});
export type BrandKitInput = z.infer<typeof BrandKitInput>;

const roleError = { status: 403, when: 'El rol no puede crear ni editar brand kits (Lector).' };
/**
 * Each file has its cap (back/apps/core/upload_limits.py · BRAND_KIT_FILE_MAX_BYTES, the same as features/brand-kits/kit.ts):
 * over it, a 400 on its field. The whole request may weigh their sum.
 */
const uploadTooLargeError = {
  status: 413,
  when: 'El pedido pesa más que los archivos del kit juntos (112 MB) más 1 MB: `{ error, code: "file_too_large", limit_bytes }`, sin leer el cuerpo. Cada archivo tiene su tope (fuentes 5 MB, logo 2 MB, intro y outro 50 MB): pasado de ese, es un 400 en su campo con el texto de la web. La web revisa los dos antes de subir.',
};

export const listBrandKits = defineEndpoint({
  id: 'brandKits.list',
  method: 'GET',
  path: '/api/v1/brand-kits/',
  summary: 'Brand kits del workspace.',
  response: z.array(BrandKit),
  errors: [],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · BrandKitViewSet.list + BrandKitSerializer',
    note: 'Desde el PR #125 la vista lee `X-Workspace-Id` y devuelve solo los kits del workspace activo (404 si el usuario no es miembro activo de ese workspace). La UI igual compara `workspace` antes de tocar el default de otro kit: es una defensa que no cuesta nada. Propuesto, todavía no existe: `watermark_position` (esquina de la marca de agua, F2). Mientras la respuesta no lo traiga, la UI no muestra el selector de esquina y el logo va abajo a la derecha.',
  },
  screens: ['F1', 'C1', 'B2', 'D5'],
});

export const createBrandKit = defineEndpoint({
  id: 'brandKits.create',
  method: 'POST',
  path: '/api/v1/brand-kits/',
  // Fonts, logo and videos are stored (or copied) while the request waits.
  timeoutMs: SLOW_REQUEST_TIMEOUT_MS,
  summary: 'Crea un brand kit (multipart por las fuentes, el logo y los videos).',
  request: BrandKitInput,
  bodyType: 'multipart',
  response: BrandKit,
  successStatus: 201,
  errors: [{ status: 400, when: 'Validación por campo (nombre vacío, color que no es hex, archivo inválido, archivo más pesado que su tope).' }, uploadTooLargeError, roleError],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · BrandKitViewSet.create/perform_create',
    note: 'Desde el PR #125 lo crea en el workspace de `X-Workspace-Id`, y aplica el rol: el Lector recibe 403 `{ detail }` en español. Sin ningún workspace responde 400.',
  },
  screens: ['F1', 'F2'],
});

export const getBrandKit = defineEndpoint({
  id: 'brandKits.detail',
  method: 'GET',
  path: '/api/v1/brand-kits/:id/',
  summary: 'Un brand kit.',
  response: BrandKit,
  errors: [{ status: 404, when: 'No existe o es de otro workspace.' }],
  backend: { status: 'exists', source: 'back/apps/videos/views.py · BrandKitViewSet.retrieve' },
  screens: ['F2'],
});

export const updateBrandKit = defineEndpoint({
  id: 'brandKits.update',
  method: 'PATCH',
  path: '/api/v1/brand-kits/:id/',
  // Fonts, logo and videos are stored (or copied) while the request waits.
  timeoutMs: SLOW_REQUEST_TIMEOUT_MS,
  summary: 'Edita un brand kit. Con `is_default: true` pasa a ser el default del workspace.',
  request: BrandKitInput.partial(),
  bodyType: 'multipart',
  response: BrandKit,
  errors: [{ status: 400, when: 'Validación por campo, también un archivo más pesado que su tope.' }, uploadTooLargeError, roleError, { status: 404, when: 'No existe o es de otro workspace.' }],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · BrandKitViewSet.perform_update/_save_one_default + brand_kit_service',
    note: 'Un solo default por workspace, garantizado en el backend. `is_default: true` desmarca el default anterior en la misma transacción, con la fila del workspace bloqueada: dos pedidos a la vez terminan con uno solo (gana el último). Crear un kit con `is_default: true` hace lo mismo. Además hay una restricción única parcial en la base (`one_default_brand_kit_per_workspace`). `is_default: false` sobre el kit default se acepta y deja al workspace sin default, igual que borrarlo: ningún otro kit se promueve solo y los proyectos nuevos salen sin kit preseleccionado.',
  },
  screens: ['F1', 'F2'],
});

export const deleteBrandKit = defineEndpoint({
  id: 'brandKits.delete',
  method: 'DELETE',
  path: '/api/v1/brand-kits/:id/',
  summary: 'Borra un brand kit. Los proyectos que lo usaban quedan sin kit (SET_NULL).',
  response: NoContent,
  successStatus: 204,
  errors: [roleError],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · BrandKitViewSet.destroy',
    note: 'Borrar el kit default deja al workspace sin default: ningún otro kit se promueve solo.',
  },
  screens: ['F1', 'F2'],
});

export const duplicateBrandKit = defineEndpoint({
  id: 'brandKits.duplicate',
  method: 'POST',
  path: '/api/v1/brand-kits/:id/duplicate/',
  // Fonts, logo and videos are stored (or copied) while the request waits.
  timeoutMs: SLOW_REQUEST_TIMEOUT_MS,
  summary: 'Copia un brand kit con sus archivos ("Copia de …", "Copia 2 de …"). La copia nunca es default.',
  response: BrandKit,
  successStatus: 201,
  errors: [
    roleError,
    { status: 404, when: 'No existe o es de otro workspace.' },
    { status: 502, when: 'No se pudo copiar un archivo del kit (`code: "brand_kit_copy_failed"`). No se crea nada: ni el kit ni archivos sueltos. No es 503: la app lee cualquier 503 como mantenimiento.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · BrandKitViewSet.duplicate + back/apps/videos/services/brand_kit_service.py · duplicate_brand_kit',
    note: 'Sin cuerpo. La copia queda en el workspace activo, a nombre de quien la pide, como «Copia de {nombre}»; si el workspace ya tiene ese nombre, «Copia 2 de {nombre}», «Copia 3 de {nombre}»… (cortado a 100 caracteres). Copia todos los campos y cada archivo (fuentes, logo, intro, outro) a la carpeta del kit nuevo con el mismo nombre de archivo (`assets/logos/{id del kit}/logo.png`): los dos kits no comparten archivos. Nunca es default.',
  },
  screens: ['F1'],
});

export const brandKitsEndpoints = [listBrandKits, createBrandKit, getBrandKit, updateBrandKit, deleteBrandKit, duplicateBrandKit] as const;
