import { cleanup, configure, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/api/mocks/server';
import real from '@/api/contracts/drf/responses.json';
import { db, idle, renderScenario } from '@/testing';
import { BrandKitsScreen } from './BrandKitsScreen';

// These flows wait on the mocked API several times: on a busy CI runner the default 1 s / 5 s are not enough.
configure({ asyncUtilTimeout: 5000 });
vi.setConfig({ testTimeout: 30_000 });

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(async () => {
  cleanup();
  await idle();
  server.resetHandlers();
});
afterAll(() => server.close());

const open = async (scenario?: string) => {
  renderScenario(<BrandKitsScreen />, { scenario });
  await screen.findByRole('heading', { level: 1, name: 'Brand kits' });
};
const card = (name: string) => screen.findByRole('article', { name: `Brand kit ${name}` });
const menuItem = async (kit: string, item: string) => {
  fireEvent.click(within(await card(kit)).getByRole('button', { name: `Acciones de ${kit}` }));
  return within(await screen.findByRole('menu', { name: `Acciones de ${kit}` })).getByRole('menuitem', { name: item });
};
const FOREIGN_KIT = '00000009-0000-4000-8000-0000000000ff';
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const kits = () => db().brandKits.filter((kit) => kit.workspace === db().workspaces[0]?.id);

describe('F1 · Brand kits', () => {
  it('muestra cada kit con sus colores, su logo, su fuente y la marca «Por defecto»', async () => {
    await open();
    const main = await card('Estudio Norte');
    expect(within(main).getByText('Por defecto')).toBeInTheDocument();
    expect(within(main).getByRole('img', { name: 'Colores: Texto #FFFFFF, Fondo #111113, Palabra activa #F2C94C' })).toBeInTheDocument();
    expect(within(main).getByText('Inter')).toBeInTheDocument();
    expect(within(main).getByRole('link', { name: 'Estudio Norte' })).toHaveAttribute('href', expect.stringMatching(/^\/brand-kits\/[0-9a-f-]+$/));

    const gaming = await card('Gaming nocturno');
    expect(within(gaming).queryByText('Por defecto')).not.toBeInTheDocument();
    expect(within(gaming).getByText('Sin logo')).toBeInTheDocument();
    expect(within(gaming).getByText('Sin fuente propia')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Nuevo brand kit/ })).toBeInTheDocument();
  });

  it('duplicar crea una copia que no es default', async () => {
    await open();
    fireEvent.click(await menuItem('Gaming nocturno', 'Duplicar'));
    expect(await card('Copia de Gaming nocturno')).toBeInTheDocument();
    expect(await screen.findByText('Se creó «Copia de Gaming nocturno»')).toBeInTheDocument();
    expect(kits().find((kit) => kit.name === 'Copia de Gaming nocturno')?.is_default).toBe(false);
  });

  it('usar por defecto mueve la marca: solo puede haber uno', async () => {
    await open();
    fireEvent.click(await menuItem('Gaming nocturno', 'Usar por defecto'));
    await waitFor(async () => expect(within(await card('Gaming nocturno')).getByText('Por defecto')).toBeInTheDocument());
    expect(within(await card('Estudio Norte')).queryByText('Por defecto')).not.toBeInTheDocument();
    expect(kits().filter((kit) => kit.is_default).map((kit) => kit.name)).toEqual(['Gaming nocturno']);
    // The kit that already is the default cannot be chosen again.
    expect(await menuItem('Gaming nocturno', 'Usar por defecto')).toHaveAttribute('aria-disabled', 'true');
  });

  it('con un backend que no desmarca el default anterior, se desmarca desde acá', async () => {
    const patched: string[] = [];
    let stale = true;
    server.use(
      http.patch('http://localhost:8000/api/v1/brand-kits/:id/', ({ params }) => {
        patched.push(String(params.id));
        return undefined;
      }),
      // Today's backend: after marking another kit, the old default still says default.
      http.get('http://localhost:8000/api/v1/brand-kits/', () => {
        // It also ignores X-Workspace-Id: the default kit of another workspace of the user comes in the same list.
        const list = [...kits(), { ...(kits()[0] as object), id: FOREIGN_KIT, workspace: '00000002-0000-4000-8000-0000000000ff', name: 'De otro workspace', is_default: true }];
        if (stale && patched.length === 1) return HttpResponse.json(list.map((kit) => (kit.name === 'Estudio Norte' ? { ...kit, is_default: true } : kit)));
        return HttpResponse.json(list);
      }),
    );
    await open();
    const [first, second] = [kits().find((kit) => kit.name === 'Estudio Norte'), kits().find((kit) => kit.name === 'Gaming nocturno')];
    fireEvent.click(await menuItem('Gaming nocturno', 'Usar por defecto'));
    // One PATCH marks the new default, the next one unmarks the old one. The other workspace's default is not touched.
    await waitFor(() => expect(patched).toEqual([second?.id, first?.id]));
    await pause(300);
    expect(patched).not.toContain(FOREIGN_KIT);
    stale = false;
    await waitFor(async () => expect(within(await card('Gaming nocturno')).getByText('Por defecto')).toBeInTheDocument());
    expect(within(await card('Estudio Norte')).queryByText('Por defecto')).not.toBeInTheDocument();
  });

  it('backend real: duplicar muestra la copia que responde, con sus archivos propios y sin ser default', async () => {
    const posted: string[] = [];
    // A copy made before each kit had its folder: the storage added a suffix to its files.
    const copy = {
      ...real.brand_kit_duplicate,
      workspace: String(db().workspaces[0]?.id),
      name: 'Copia de Gaming nocturno',
      primary_font: 'http://localhost:8000/media/assets/fonts/fuente_ZxKsE0r.ttf',
      watermark_logo: 'http://localhost:8000/media/assets/logos/logo_vCHttht.png',
    };
    server.use(
      http.post('http://localhost:8000/api/v1/brand-kits/:id/duplicate/', ({ params }) => {
        posted.push(String(params.id));
        return HttpResponse.json(copy, { status: 201 });
      }),
      http.get('http://localhost:8000/api/v1/brand-kits/', () => HttpResponse.json(posted.length ? [...kits(), copy] : kits())),
    );
    await open();
    fireEvent.click(await menuItem('Gaming nocturno', 'Duplicar'));
    expect(await screen.findByText('Se creó «Copia de Gaming nocturno»')).toBeInTheDocument();
    expect(posted).toEqual([kits().find((kit) => kit.name === 'Gaming nocturno')?.id]);
    const added = await card('Copia de Gaming nocturno');
    expect(within(added).queryByText('Por defecto')).not.toBeInTheDocument();
    expect(within(added).getByRole('link', { name: 'Copia de Gaming nocturno' })).toHaveAttribute('href', `/brand-kits/${copy.id}`);
    // The names it shows are the ones the files were uploaded with, without the storage suffix.
    expect(within(added).getByText('fuente')).toBeInTheDocument();
    expect(within(added).getByText('logo.png')).toBeInTheDocument();
  });

  it('backend real: si no se pudieron copiar los archivos (502), avisa y no agrega nada', async () => {
    const { status, body } = real.errors.brand_kit_copy_failed;
    server.use(http.post('http://localhost:8000/api/v1/brand-kits/:id/duplicate/', () => HttpResponse.json(body, { status })));
    await open();
    const before = kits().length;
    fireEvent.click(await menuItem('Gaming nocturno', 'Duplicar'));
    expect(await screen.findByText('No pudimos duplicar el brand kit.')).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Brand kit Copia de Gaming nocturno' })).not.toBeInTheDocument();
    expect(kits()).toHaveLength(before);
    // Not the maintenance page: the screen is still there.
    expect(screen.getByRole('heading', { level: 1, name: 'Brand kits' })).toBeInTheDocument();
  });

  it('backend real: «Usar por defecto» es un solo PATCH, porque el backend desmarca el anterior', async () => {
    const patched: Array<{ id: string; body: unknown }> = [];
    let marked: string | null = null;
    const list = () => kits().map((kit) => (marked ? { ...kit, is_default: kit.id === marked } : kit));
    server.use(
      http.patch('http://localhost:8000/api/v1/brand-kits/:id/', async ({ params, request }) => {
        patched.push({ id: String(params.id), body: Object.fromEntries(await request.formData()) });
        marked = String(params.id);
        return HttpResponse.json(list().find((kit) => kit.id === marked));
      }),
      http.get('http://localhost:8000/api/v1/brand-kits/', () => HttpResponse.json(list())),
    );
    await open();
    const second = kits().find((kit) => kit.name === 'Gaming nocturno');
    fireEvent.click(await menuItem('Gaming nocturno', 'Usar por defecto'));
    expect(await screen.findByText('«Gaming nocturno» es ahora el kit por defecto')).toBeInTheDocument();
    await waitFor(async () => expect(within(await card('Gaming nocturno')).getByText('Por defecto')).toBeInTheDocument());
    expect(within(await card('Estudio Norte')).queryByText('Por defecto')).not.toBeInTheDocument();
    await pause(300);
    expect(patched).toEqual([{ id: second?.id, body: { is_default: 'true' } }]);
  });

  it('un color guardado como texto libre no rompe la tarjeta: se normaliza antes de pintarlo', async () => {
    server.use(
      http.get('http://localhost:8000/api/v1/brand-kits/', () =>
        HttpResponse.json(kits().map((kit, index) => (index === 0 ? { ...kit, primary_color: '#abc', accent_color: 'javascript:alert(1)' } : kit))),
      ),
    );
    await open();
    const swatches = within(await card('Estudio Norte')).getByRole('img', { name: /Colores:/ });
    expect(swatches).toHaveAccessibleName('Colores: Texto #AABBCC, Fondo #111113, Palabra activa #FFFF00');
  });

  it('borrar pide confirmación y avisa qué pasa con los proyectos', async () => {
    await open();
    fireEvent.click(await menuItem('Cliente: Bodega Andes', 'Borrar'));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Borrar «Cliente: Bodega Andes»?' });
    expect(dialog).toHaveTextContent('Los proyectos que lo usaban quedan sin brand kit.');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Borrar brand kit' }));
    await waitFor(() => expect(screen.queryByRole('article', { name: 'Brand kit Cliente: Bodega Andes' })).not.toBeInTheDocument());
    expect(kits().map((kit) => kit.name)).toEqual(['Estudio Norte', 'Gaming nocturno']);
  });

  it('sin kits: una frase, una acción y qué incluye un kit', async () => {
    await open('workspace-vacio');
    expect(await screen.findByRole('heading', { name: 'Creá tu brand kit para que todos tus clips salgan con tu marca' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Nuevo brand kit' })).toHaveAttribute('href', '/brand-kits/nuevo');
    expect(within(screen.getByRole('list', { name: 'Qué incluye un brand kit' })).getAllByRole('listitem')).toHaveLength(4);
  });

  it('rol Lector: las acciones se ven, deshabilitadas', async () => {
    await open('rol-lector');
    for (const item of ['Editar', 'Duplicar', 'Usar por defecto', 'Borrar']) {
      expect(await menuItem('Gaming nocturno', item)).toHaveAttribute('aria-disabled', 'true');
      fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' });
    }
    expect(screen.getByRole('button', { name: /Nuevo brand kit/ })).toHaveAttribute('aria-disabled', 'true');
  });

  it('si la API no responde, avisa y deja reintentar', async () => {
    await open('servidor-caido');
    expect(await screen.findByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
  });
});
