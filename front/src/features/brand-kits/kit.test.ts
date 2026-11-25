import { describe, expect, it } from 'vitest';
import {
  EMPTY_KIT,
  buildInput,
  changedParts,
  currentFile,
  describeChanges,
  draftFrom,
  fileName,
  fontLabel,
  loadableUrl,
  normalizeHex,
  validateDraft,
  validateFile,
  type KitSource,
} from './kit';

const kit: KitSource = {
  ...EMPTY_KIT,
  name: 'Estudio Norte',
  description: 'Marca principal del estudio.',
  primary_color: '#F4F1EA',
  secondary_color: '#1D1D1F',
  accent_color: '#F2C94C',
  is_default: true,
  primary_font: '/media/assets/fonts/InterTight-Bold.ttf',
  watermark_logo: '/media/assets/logos/estudio-norte.png',
};

const file = (name: string, size = 1000) => ({ name, size });

describe('colores', () => {
  it('normaliza lo que se escribe a un hex de 6 dígitos en mayúsculas', () => {
    expect(normalizeHex('f2c94c')).toBe('#F2C94C');
    expect(normalizeHex(' #f2c ')).toBe('#FF22CC');
    expect(normalizeHex('#F2C94C')).toBe('#F2C94C');
  });

  it('un color que no es hex marca su campo', () => {
    const errors = validateDraft({ ...draftFrom(kit), accent_color: 'amarillo', name: '' });
    expect(errors.accent_color).toMatch(/hex/);
    expect(errors.name).toBe('Ponele un nombre al brand kit.');
    expect(errors.primary_color).toBeUndefined();
    expect(validateDraft(draftFrom(kit))).toEqual({});
  });
});

describe('archivos', () => {
  it('nombre del archivo y de la fuente', () => {
    expect(fileName('/media/assets/fonts/InterTight-Bold.ttf?X-Amz=1')).toBe('InterTight-Bold.ttf');
    expect(fontLabel('/media/assets/fonts/InterTight-Bold.ttf')).toBe('Inter Tight');
    expect(fontLabel('/media/assets/fonts/Inter-Bold.ttf')).toBe('Inter');
    expect(fontLabel(null)).toBe('');
  });

  it('solo carga direcciones reales: las rutas de los mocks no existen', () => {
    expect(loadableUrl('/media/assets/logos/estudio-norte.png')).toBeNull();
    expect(loadableUrl('https://cdn.example.com/logo.png')).toBe('https://cdn.example.com/logo.png');
    expect(loadableUrl('blob:http://localhost/abc')).toBe('blob:http://localhost/abc');
  });

  it('valida el tipo y el peso de cada archivo', () => {
    expect(validateFile('primary_font', file('marca.woff2'))).toBe('Subí una fuente .ttf u .otf.');
    expect(validateFile('primary_font', file('marca.otf'))).toBeNull();
    expect(validateFile('watermark_logo', file('logo.jpg'))).toBe('Subí una imagen PNG.');
    expect(validateFile('watermark_logo', file('logo.png', 3 * 1024 * 1024))).toBe('El logo pesa más de 2 MB.');
    expect(validateFile('intro_video', file('intro.mov'))).toBe('Subí un video MP4.');
  });
});

describe('cambios sin guardar', () => {
  it('sin tocar nada no hay cambios', () => {
    expect(changedParts(kit, draftFrom(kit))).toEqual([]);
  });

  it('describe qué cambió, como en la barra de guardar', () => {
    const draft = { ...draftFrom(kit), accent_color: '#3DD6C4', watermark_position: 'top_left' as const };
    expect(describeChanges(changedParts(kit, draft))).toBe('Cambiaste el color de la palabra activa y la posición del logo.');
    expect(describeChanges(changedParts(kit, { ...draft, name: 'Otro' }))).toBe('Cambiaste el nombre, el color de la palabra activa y la posición del logo.');
  });

  it('quitar un archivo guardado es un cambio; quitar uno que no había, no', () => {
    const removedLogo = { ...draftFrom(kit), files: { watermark_logo: null } };
    expect(changedParts(kit, removedLogo)).toEqual(['el logo']);
    expect(currentFile(kit, removedLogo, 'watermark_logo')).toEqual({ file: null, url: null });
    expect(changedParts(kit, { ...draftFrom(kit), files: { intro_video: null } })).toEqual([]);
  });

  it('al editar solo manda lo que cambió; un archivo quitado va vacío', () => {
    const logo = new File(['x'], 'nuevo.png', { type: 'image/png' });
    const draft = { ...draftFrom(kit), accent_color: '#3dd6c4', is_default: false, files: { watermark_logo: logo, primary_font: null } };
    expect(buildInput(kit, draft, { create: false })).toEqual({ accent_color: '#3DD6C4', watermark_logo: logo, primary_font: '', is_default: false });
    expect(buildInput(kit, draftFrom(kit), { create: false })).toEqual({});
  });

  it('al crear manda todos los datos', () => {
    const draft = { ...draftFrom(EMPTY_KIT), name: '  Podcast Vender ' };
    expect(buildInput(EMPTY_KIT, draft, { create: true })).toEqual({
      name: 'Podcast Vender',
      description: '',
      primary_color: '#FFFFFF',
      secondary_color: '#000000',
      accent_color: '#FFFF00',
      watermark_position: 'bottom_right',
    });
  });
});

describe('colores guardados', () => {
  it('lo que guardó el backend llega al editor como #RRGGBB, o el color de fábrica si no es un hex', () => {
    const draft = draftFrom({ ...EMPTY_KIT, primary_color: '#abc', secondary_color: 'red', accent_color: '#f2c94c' });
    expect(draft.primary_color).toBe('#AABBCC');
    expect(draft.secondary_color).toBe(EMPTY_KIT.secondary_color);
    expect(draft.accent_color).toBe('#F2C94C');
  });
});
