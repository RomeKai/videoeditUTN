import { describe, expect, it } from 'vitest';
import type { Clip } from '@/api/contracts/projects';
import { POST_GOING_OUT_WAIT_MS, anyPostGoingOut, clipsChanging } from './project-context';

const NOW = Date.parse('2026-11-28T18:00:00Z');
const MIN = 60_000;

const project = (...posts: Array<{ status: NonNullable<Clip['posts']>[number]['status']; at: number }>) => ({
  clips: [
    {
      status: 'completed',
      posts: posts.map((post, index) => ({ id: `p${index}`, platform: 'TIKTOK' as const, status: post.status, publish_at: new Date(post.at).toISOString() })),
    },
  ] as Clip[],
});

describe('Clips vuelve a pedir el proyecto solo mientras una publicación sale ahora', () => {
  it('lo que sale ahora: publicando, o en cola o programada para una hora que ya llegó', () => {
    expect(anyPostGoingOut(project({ status: 'PROCESSING', at: NOW - MIN }), NOW)).toBe(true);
    expect(anyPostGoingOut(project({ status: 'QUEUED', at: NOW - MIN }), NOW)).toBe(true);
    expect(anyPostGoingOut(project({ status: 'SCHEDULED', at: NOW }), NOW)).toBe(true);
  });

  it('una en cola para dentro de 50 minutos no hace consultar cada 5 s todo ese rato (revisión de #192)', () => {
    // The dispatcher queues posts up to an hour before their time.
    expect(anyPostGoingOut(project({ status: 'QUEUED', at: NOW + 50 * MIN }), NOW)).toBe(false);
    expect(anyPostGoingOut(project({ status: 'SCHEDULED', at: NOW + 50 * MIN }), NOW)).toBe(false);
  });

  it('deja de consultar pasado el tope: una trabada o una que el beat nunca mandó', () => {
    const late = NOW - POST_GOING_OUT_WAIT_MS - MIN;
    expect(anyPostGoingOut(project({ status: 'PROCESSING', at: late }), NOW)).toBe(false);
    expect(anyPostGoingOut(project({ status: 'SCHEDULED', at: late }), NOW)).toBe(false);
    expect(anyPostGoingOut(project({ status: 'QUEUED', at: late }), NOW)).toBe(false);
  });

  it('lo que ya salió, falló o es un borrador no la hace consultar', () => {
    for (const status of ['PUBLISHED', 'FAILED', 'DRAFT'] as const) expect(anyPostGoingOut(project({ status, at: NOW - MIN }), NOW)).toBe(false);
    expect(clipsChanging(project({ status: 'PUBLISHED', at: NOW - MIN }))).toBe(false);
  });
});
