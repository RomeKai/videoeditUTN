import { describe, expect, it } from 'vitest';
import { DEFAULT_LIST_STATE, linkProvider, listQuery, readListParams } from './project-view';

describe('linkProvider', () => {
  it('nombra solo los hosts exactos que el backend baja', () => {
    expect(linkProvider('https://www.youtube.com/watch?v=abc')).toBe('YouTube');
    expect(linkProvider('https://youtube.com/watch?v=abc')).toBe('YouTube');
    expect(linkProvider('https://youtu.be/abc')).toBe('YouTube');
    expect(linkProvider('https://player.vimeo.com/video/1')).toBe('Vimeo');
    expect(linkProvider('https://clips.twitch.tv/Slug')).toBe('Twitch');
    expect(linkProvider('https://drive.google.com/file/d/x/view')).toBe('Google Drive');
  });

  it('otro subdominio o un host que solo termina igual no es del sitio', () => {
    for (const url of [
      'https://youtube.com.evil.com/watch?v=abc',
      'https://evilyoutube.com/watch?v=abc',
      'https://consent.youtube.com/m?continue=http://10.0.0.1/',
      'https://x.youtu.be/abc',
      'https://evil.drive.google.com/file/d/x',
      'https://sites.google.com/x',
      'https://example.com/youtube.com',
      'no es un link',
    ]) {
      expect(linkProvider(url), url).toBeNull();
    }
  });
});

describe('la lista de Proyectos en la URL', () => {
  const read = (query: string) => readListParams(new URLSearchParams(query));

  it('lee el filtro, el orden, la página y la búsqueda', () => {
    expect(read('estado=to_review&orden=title&pagina=3&buscar=podcast%20ep')).toEqual({ group: 'to_review', ordering: 'title', page: 3, search: 'podcast ep' });
    expect(read('')).toEqual(DEFAULT_LIST_STATE);
  });

  it('lo que falta o no conoce es el valor por defecto', () => {
    for (const query of ['estado=borrados', 'orden=-title', 'pagina=0', 'pagina=-2', 'pagina=2.5', 'pagina=dos', 'pagina=1e3', 'pagina=9999999', 'buscar=%20%20']) {
      expect(read(query), query).toEqual(DEFAULT_LIST_STATE);
    }
    expect(read(`buscar=${'a'.repeat(400)}`).search).toHaveLength(255);
  });

  it('escribe solo lo que no es el valor por defecto', () => {
    expect(listQuery(DEFAULT_LIST_STATE)).toBe('');
    expect(listQuery({ ...DEFAULT_LIST_STATE, page: 2 })).toBe('pagina=2');
    expect(listQuery({ group: 'failed', ordering: 'created_at', page: 4, search: ' clase 3 ' })).toBe('estado=failed&orden=created_at&pagina=4&buscar=clase+3');
    // What it writes, it reads back.
    const state = { group: 'ready', ordering: 'title', page: 7, search: 'ñandú & co' } as const;
    expect(read(listQuery(state))).toEqual(state);
  });

  it('conserva los parámetros que no son de la lista y saca los suyos cuando vuelven al valor por defecto', () => {
    const current = new URLSearchParams('espacio=ws-1&estado=failed&pagina=3');
    expect(listQuery({ ...DEFAULT_LIST_STATE, ordering: 'title' }, current)).toBe('espacio=ws-1&orden=title');
    expect(listQuery(DEFAULT_LIST_STATE, current)).toBe('espacio=ws-1');
  });
});
