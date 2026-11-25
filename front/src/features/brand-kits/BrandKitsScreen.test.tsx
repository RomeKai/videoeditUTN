import { cleanup, configure, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { server } from '@/api/mocks/server';
import { db, idle, renderScreen } from '../publicar/testing';
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
  renderScreen(<BrandKitsScreen />, { scenario });
  await screen.findByRole('heading', { level: 1, name: 'Brand kits' });
};
const card = (name: string) => screen.findByRole('article', { name: `Brand kit ${name}` });
const menuItem = async (kit: string, item: string) => {
  fireEvent.click(within(await card(kit)).getByRole('button', { name: `Acciones de ${kit}` }));
  return within(await screen.findByRole('menu', { name: `Acciones de ${kit}` })).getByRole('menuitem', { name: item });
};
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
