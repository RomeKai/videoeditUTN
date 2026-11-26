import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { Id, IsoDateTime, NoContent } from './common';
import { Clip, PaidActionResponse } from './projects';

/** ClipPreview.Status */
export const PreviewStatus = z.enum(['pending', 'rendering', 'ready', 'failed']);

/** Payload of ClipPreviewView (PTE-03): a free 360p preview that expires. */
export const ClipPreview = z.object({
  preview_id: Id,
  clip_id: Id,
  status: PreviewStatus,
  /** Temporary URL once ready, null before. */
  url: z.string().nullable(),
  expires_at: IsoDateTime,
  error_code: z.string().nullable(),
  cost_tokens: z.literal(0),
  /** What the preview leaves out to stay fast. */
  simplified: z.object({ height: z.number().int(), face_tracking: z.boolean(), silence_removal: z.boolean() }),
});
export type ClipPreview = z.infer<typeof ClipPreview>;

export const UpdateClipRequest = z
  .object({
    title: z.string().min(1).max(255),
    /** Seconds in the source video; `0 <= start_time < end_time`. */
    start_time: z.number().min(0),
    end_time: z.number().positive(),
  })
  .partial();
export type UpdateClipRequest = z.infer<typeof UpdateClipRequest>;

export const getClip = defineEndpoint({
  id: 'clips.detail',
  method: 'GET',
  path: '/api/v1/clips/:id/',
  summary: 'Un clip con sus datos, sus capas y dónde está publicado.',
  response: Clip,
  errors: [{ status: 404, when: 'No existe o es de otro workspace.' }],
  backend: { status: 'proposed', gap: 11, note: 'Hoy los clips solo llegan dentro del proyecto (VideoClipSerializer).' },
  screens: ['D4', 'E1'],
});

export const updateClip = defineEndpoint({
  id: 'clips.update',
  method: 'PATCH',
  path: '/api/v1/clips/:id/',
  summary: 'Cambia el título o el recorte. No renderiza: para eso está `render`.',
  request: UpdateClipRequest,
  response: Clip,
  errors: [
    { status: 400, when: 'Recorte inválido (fin menor o igual al inicio, o fuera del video).' },
    { status: 403, when: 'El rol no puede editar (Lector).' },
    {
      status: 409,
      when: 'El proyecto se está renderizando y el pedido cambia el recorte (`code: "project_rendering"`). Solo `title` se puede cambiar mientras tanto. La UI avisa y deja el borrador como está.',
    },
  ],
  backend: {
    status: 'proposed',
    gap: 11,
    note: 'El 409 sigue la regla que ya aplica `VideoProjectSerializer.update` al PATCH del proyecto (ProjectRenderingError): lo que el render lee no cambia debajo de él.',
  },
  screens: ['D4', 'D3', 'D5'],
});

export const deleteClip = defineEndpoint({
  id: 'clips.delete',
  method: 'DELETE',
  path: '/api/v1/clips/:id/',
  summary: 'Borra el clip y cancela sus publicaciones programadas.',
  response: NoContent,
  successStatus: 204,
  errors: [{ status: 403, when: 'El rol no puede borrar.' }],
  backend: { status: 'proposed', gap: 11 },
  screens: ['D3'],
});

export const renderClip = defineEndpoint({
  id: 'clips.render',
  method: 'POST',
  path: '/api/v1/clips/:id/render/',
  summary: 'Vuelve a renderizar el clip con su recorte actual y la configuración del proyecto. Cobra por la duración del clip.',
  response: PaidActionResponse,
  successStatus: 202,
  errors: [
    { status: 402, when: 'Saldo insuficiente: `{ error, required }`. La UI abre D2.' },
    { status: 403, when: 'El plan actual no permite la resolución del proyecto (`code: "resolution"`). La UI abre D3.' },
    { status: 409, when: 'El clip ya se está renderizando, o su proyecto (`code: "project_rendering"`).' },
  ],
  backend: { status: 'proposed', gap: 11 },
  screens: ['D4', 'D5'],
});

export const retryClip = defineEndpoint({
  id: 'clips.retry',
  method: 'POST',
  path: '/api/v1/clips/:id/retry/',
  summary: 'Reintenta el render de un clip que falló. No cobra si el error fue del sistema.',
  response: Clip,
  successStatus: 202,
  errors: [{ status: 409, when: 'El clip no está en error.' }],
  backend: { status: 'proposed', gap: 11 },
  screens: ['D3'],
});

export const getClipDownload = defineEndpoint({
  id: 'clips.download',
  method: 'GET',
  path: '/api/v1/clips/:id/download/',
  summary: 'Link de descarga firmado y temporal del clip renderizado.',
  response: z.object({ url: z.string().url(), filename: z.string(), expires_at: IsoDateTime }),
  errors: [
    { status: 404, when: 'No existe o es de otro workspace.' },
    { status: 409, when: 'El clip todavía no está renderizado.' },
    { status: 503, when: 'No se pudo firmar el link o el archivo no está en el almacenamiento (`code: "storage_unavailable"`).' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views_project_data.py · ClipDownloadView + services/clip_download.py · clip_download',
    note: 'En R2 la URL firmada (1 h) pide `Content-Disposition: attachment` con `filename`, así el navegador guarda el archivo aunque sea de otro origen. En local (USE_R2=False) es la URL absoluta de /media/. `filename` es el título en minúsculas, con guiones en lugar de lo que no es letra o número, y `.mp4` («clip.mp4» si queda vacío). Lo puede pedir cualquier rol (`clip.download`).',
  },
  screens: ['D3', 'D4', 'D5'],
});

export const requestClipPreview = defineEndpoint({
  id: 'clips.requestPreview',
  method: 'POST',
  path: '/api/v1/clips/:clipId/preview/',
  summary: 'Pide una vista previa gratis en 360p, o devuelve la que ya está en curso. Nunca reserva tokens.',
  response: ClipPreview,
  successStatus: 202,
  errors: [
    { status: 409, when: 'El video fuente todavía no está, o el proyecto es de video completo (no tiene vista previa): `{ error }`.' },
    { status: 429, when: 'El workspace ya tiene el máximo de vistas previas en curso. La UI pide esperar unos segundos.' },
  ],
  backend: {
    status: 'exists',
    source: 'back/apps/videos/views.py · ClipPreviewView.post',
    note: 'Responde 202 si la encola y 200 si ya había una en curso. El mapa (D4) todavía la da por faltante.',
  },
  screens: ['D2', 'D4'],
});

export const getClipPreview = defineEndpoint({
  id: 'clips.preview',
  method: 'GET',
  path: '/api/v1/clips/:clipId/preview/',
  summary: 'La vista previa vigente más nueva del clip. Se consulta cada 5 s hasta `ready` o `failed`.',
  response: ClipPreview,
  errors: [{ status: 404, when: 'No hay vista previa vigente: `{ error }`. No es un error para la UI: ofrece pedir una.' }],
  backend: { status: 'exists', source: 'back/apps/videos/views.py · ClipPreviewView.get' },
  screens: ['D2', 'D4'],
});

export const clipsEndpoints = [
  getClip,
  updateClip,
  deleteClip,
  renderClip,
  retryClip,
  getClipDownload,
  requestClipPreview,
  getClipPreview,
] as const;
