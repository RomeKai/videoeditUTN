import { describe, expect, it } from 'vitest';
import { checkFile, checkMedia, extensionsLabel, mockDuration, readMedia } from './media';

const pricing = { upload_extensions: ['.mp4', '.mov', '.avi'], upload_max_size_bytes: 500 * 1024 * 1024, upload_max_duration_seconds: 10800 };
const pro = { max_video_duration_seconds: 10800 };
const free = { max_video_duration_seconds: 600 };

describe('checkFile', () => {
  it('acepta los videos del contrato', () => {
    expect(checkFile({ name: 'clase.MP4', type: 'video/mp4', size: 10 }, pricing)).toBeNull();
    expect(checkFile({ name: 'clase.mov', type: '', size: 10 }, pricing)).toBeNull();
  });

  it('rechaza lo que no es un video antes de subir', () => {
    expect(checkFile({ name: 'guion.pdf', type: 'application/pdf', size: 10 }, pricing)).toEqual({ code: 'not_a_video' });
    expect(checkFile({ name: 'audio.mp4', type: 'audio/mp4', size: 10 }, pricing)).toEqual({ code: 'not_a_video' });
    expect(checkFile({ name: 'sin-extension', type: 'video/mp4', size: 10 }, pricing)).toEqual({ code: 'not_a_video' });
  });

  it('rechaza un archivo que pasa el tope de peso', () => {
    expect(checkFile({ name: 'largo.mp4', type: 'video/mp4', size: pricing.upload_max_size_bytes + 1 }, pricing)).toEqual({ code: 'too_heavy', limitBytes: pricing.upload_max_size_bytes });
  });
});

describe('checkMedia', () => {
  it('compara la duración con el plan', () => {
    expect(checkMedia({ duration: 601, width: 1920, height: 1080 }, pricing, free)).toEqual({ code: 'too_long', limitSeconds: 600, byPlan: true });
    expect(checkMedia({ duration: 600, width: 1920, height: 1080 }, pricing, free)).toBeNull();
  });

  it('aplica el tope del sistema cuando no hay plan', () => {
    expect(checkMedia({ duration: 12000, width: null, height: null }, pricing, null)).toEqual({ code: 'too_long', limitSeconds: 10800 });
  });

  it('revisa la resolución', () => {
    expect(checkMedia({ duration: 60, width: 7680, height: 4320 }, pricing, pro)).toEqual({ code: 'too_large' });
    expect(checkMedia({ duration: 60, width: 176, height: 144 }, pricing, pro)).toEqual({ code: 'too_small' });
  });

  it('sin metadata no bloquea: lo mide el backend', () => {
    expect(checkMedia({ duration: null, width: null, height: null }, pricing, free)).toBeNull();
  });
});

describe('mocks', () => {
  it('la duración sale del nombre o del peso, como en la API simulada', async () => {
    expect(mockDuration('clase-45min.mp4', 10)).toBe(2700);
    expect(mockDuration('clase.mp4', 10)).toBe(180);
    // jsdom cannot decode video: in mock mode the name decides.
    expect((await readMedia(new File(['x'], 'vlog-12min.mp4', { type: 'video/mp4' }))).duration).toBe(720);
  });
});

describe('extensionsLabel', () => {
  it('arma la lista en español', () => {
    expect(extensionsLabel(['.mp4', '.mov', '.avi'])).toBe('MP4, MOV o AVI');
    expect(extensionsLabel(['.mp4'])).toBe('MP4');
  });
});
