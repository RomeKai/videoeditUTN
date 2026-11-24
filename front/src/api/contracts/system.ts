import { z } from 'zod';
import { defineEndpoint } from '../endpoint';
import { IsoDateTime } from './common';

export const SystemStatus = z.object({
  /** True: the app shows the maintenance page (K5) instead of the screens. */
  maintenance: z.boolean(),
  /** Estimated window, to show "24 nov 2026, 02:00 – 03:30". */
  maintenance_window: z.object({ starts_at: IsoDateTime, ends_at: IsoDateTime }).nullable(),
});
export type SystemStatus = z.infer<typeof SystemStatus>;

export const getSystemStatus = defineEndpoint({
  id: 'system.status',
  method: 'GET',
  path: '/api/v1/status/',
  summary: 'Si la plataforma está en mantenimiento y hasta cuándo.',
  response: SystemStatus,
  auth: false,
  workspaceScoped: false,
  errors: [{ status: 503, when: 'En mantenimiento sin poder responder: la UI muestra K5 igual.' }],
  backend: { status: 'proposed', note: 'Sin número de hueco: lo pide la pantalla K5 (horario estimado).' },
  screens: ['K5'],
});

export const systemEndpoints = [getSystemStatus] as const;
