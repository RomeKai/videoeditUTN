import { describe, expect, it } from 'vitest';
import {
  EMPTY_KIT,
  buildInput,
  changedParts,
  currentFile,
  describeChanges,
  draftFrom,
  fileName,
  storedFileName,
  storedFontLabel,
  fontLabel,
  loadableUrl,
  typedHex,
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
    expect(typedHex('f2c94c')).toBe('#F2C94C');
    expect(typedHex(' #f2c ')).toBe('#FF22CC');
    expect(typedHex('#F2C94C')).toBe('#F2C94C');
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

  it('el sufijo que agrega el almacenamiento no se muestra', () => {
    expect(storedFileName('/media/assets/logos/logo_vCHttht.png')).toBe('logo.png');
    expect(storedFileName('http://localhost:8000/media/assets/videos/intro_a1B2c3D.mp4?v=1')).toBe('intro.mp4');
    expect(storedFontLabel('/media/assets/fonts/fuente_ZxKsE0r.ttf')).toBe('fuente');
    expect(storedFontLabel('/media/assets/fonts/InterTight-Bold_a1B2c3D.ttf')).toBe('Inter Tight');
    // Seven letters that are a word are part of the name.
    expect(storedFileName('/media/assets/fonts/Inter_Regular.ttf')).toBe('Inter_Regular.ttf');
    expect(storedFileName('/media/assets/fonts/open_display.otf')).toBe('open_display.otf');
    expect(storedFileName('/media/assets/logos/LOGO_PRIMARY.png')).toBe('LOGO_PRIMARY.png');
    expect(storedFileName('/media/assets/logos/logo_2024111.png')).toBe('logo_2024111.png');
    expect(storedFontLabel('/media/assets/fonts/Inter_Regular.ttf')).toBe('Inter');
    // The folder of the kit is not the name.
    expect(storedFileName('/media/assets/logos/c3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a77/logo.png')).toBe('logo.png');
    expect(storedFileName(null)).toBe('');
  });

  it('en la carpeta del kit el nombre es el que se subió: no se le quita nada', () => {
    const folder = '/media/assets/logos/c3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a77';
    for (const name of ['logo_256x256.png', 'clip_1080p60.mp4', 'logo_darkRed.png', 'logo_v2final.png', 'foto_IMG1234.jpg', 'Roboto_Mono500.ttf', '_abcDEF1.png']) {
      expect(storedFileName(`${folder}/${name}`)).toBe(name);
      expect(storedFileName(`http://localhost:8000${folder}/${name}?v=1`)).toBe(name);
    }
    expect(storedFontLabel('/media/assets/fonts/c3c1e2f4-5b6d-4e7f-8a9b-0c1d2e3f4a77/Roboto_Mono500.ttf')).toBe('Roboto Mono500');
    // A name that is all suffix keeps its stem, also in an old path.
    expect(storedFileName('/media/assets/logos/_abcDEF1.png')).toBe('_abcDEF1.png');
  });

  it('una fuente recién elegida se muestra con su nombre, sin tocar', () => {
    expect(fontLabel('Roboto_Mono500.ttf')).toBe('Roboto Mono500');
    expect(fontLabel('fuente_ZxKsE0r.ttf')).toBe('fuente Zx Ks E0r');
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
