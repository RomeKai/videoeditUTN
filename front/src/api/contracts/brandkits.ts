import { z } from 'zod';
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
    note: 'Hueco: hoy la vista ignora `X-Workspace-Id` y devuelve los kits de todos los workspaces del usuario; debería filtrar por el workspace activo (como projects.list). Mientras tanto la UI filtra por `workspace` antes de tocar el default de otro kit. Propuesto, todavía no existe: `watermark_position` (esquina de la marca de agua, F2). Mientras la respuesta no lo traiga, la UI no muestra el selector de esquina y el logo va abajo a la derecha.',
  },
  screens: ['F1', 'C1', 'B2', 'D5'],
});

export const createBrandKit = defineEndpoint({
  id: 'brandKits.create',
  method: 'POST',
  path: '/api/v1/brand-kits/',
  summary: 'Crea un brand kit (multipart por las fuentes, el logo y los videos).',
  request: BrandKitInput,
  bodyType: 'multipart',
  response: BrandKit,
  successStatus: 201,
  errors: [{ status: 400, when: 'Validación por campo (nombre vacío, color que no es hex, archivo inválido).' }, roleError],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · BrandKitViewSet.create/perform_create',
    note: 'Usa `user.workspaces.first()` en lugar del workspace activo (J3).',
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
  summary: 'Edita un brand kit. Con `is_default: true` pasa a ser el default del workspace.',
  request: BrandKitInput.partial(),
  bodyType: 'multipart',
  response: BrandKit,
  errors: [{ status: 400, when: 'Validación por campo.' }, roleError],
  backend: {
    status: 'partial',
    source: 'back/apps/videos/views.py · BrandKitViewSet.partial_update',
    note: 'El backend no garantiza un solo default por workspace: al marcar uno debería desmarcar el resto (mapa F2).',
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
  backend: { status: 'exists', source: 'back/apps/videos/views.py · BrandKitViewSet.destroy' },
  screens: ['F1', 'F2'],
});

export const duplicateBrandKit = defineEndpoint({
  id: 'brandKits.duplicate',
  method: 'POST',
  path: '/api/v1/brand-kits/:id/duplicate/',
  summary: 'Copia un brand kit con sus archivos ("Copia de …"). La copia nunca es default.',
  response: BrandKit,
  successStatus: 201,
  errors: [roleError],
  backend: { status: 'proposed', note: 'Sin número de hueco: el mapa (F1) pide duplicar y los archivos no se pueden copiar desde el navegador.' },
  screens: ['F1'],
});

export const brandKitsEndpoints = [listBrandKits, createBrandKit, getBrandKit, updateBrandKit, deleteBrandKit, duplicateBrandKit] as const;
