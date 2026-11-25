import { cleanup, configure, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse } from 'msw';
import { server } from '@/api/mocks/server';
import { loadDb } from '@/api/mocks/state';
import { EXAMPLE_IDS } from '@/dev/screens';
import { idle, renderScreen } from '../publicar/testing';
import { BrandKitEditor } from './BrandKitEditor';

// These flows wait on the mocked API several times: on a busy CI runner the default 1 s / 5 s are not enough.
configure({ asyncUtilTimeout: 5000 });
vi.setConfig({ testTimeout: 30_000 });

/**
 * Saving a kit is multipart, and jsdom's FormData does not travel through
 * Node's fetch: the two writes are observed here instead of sent. What the mock
 * does with them is covered by src/api/mocks/handlers.test.ts.
 */
const writes = vi.hoisted(() => ({ update: vi.fn(), create: vi.fn() }));
vi.mock('@/api/hooks', async (original) => ({
  ...(await original<typeof import('@/api/hooks')>()),
  useUpdateBrandKit: () => ({ mutate: writes.update, isPending: false }),
  useCreateBrandKit: () => ({ mutate: writes.create, isPending: false }),
}));

/** One router for the whole app, as in the real one: the unsaved-changes guard watches its `push`. */
const router = vi.hoisted(() => {
  const push = vi.fn();
  return { original: push, object: { push, replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() } };
});
vi.mock('next/navigation', () => ({
  useRouter: () => router.object,
  usePathname: () => '/brand-kits/x',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
  redirect: vi.fn(),
  notFound: vi.fn(),
}));

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  router.original.mockReset();
  router.object.push = router.original;
  writes.update.mockReset();
  writes.create.mockReset();
});
afterEach(async () => {
  cleanup();
  await idle();
  server.resetHandlers();
});
afterAll(() => server.close());

const open = async (id: string = EXAMPLE_IDS.brandKit, scenario?: string) => {
  renderScreen(<BrandKitEditor id={id} />, { scenario });
  return screen.findByRole('form', { name: id === 'nuevo' ? 'Nuevo brand kit' : 'Editar brand kit' });
};
const saveBar = () => screen.queryByRole('region', { name: 'Acciones' });

describe('F2 · Editar brand kit', () => {
  it('carga el kit con sus datos, sus archivos y la vista previa', async () => {
    const form = await open();
    expect(screen.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeInTheDocument();
    expect(screen.getByText('Por defecto')).toBeInTheDocument();
    expect(within(form).getByLabelText('Nombre')).toHaveValue('Estudio Norte');
    expect(within(form).getByLabelText('Palabra activa')).toHaveValue('#F2C94C');
    expect(within(form).getByText('Inter-Bold.ttf')).toBeInTheDocument();
    expect(within(form).getByText('estudio-norte.png')).toBeInTheDocument();
    expect(within(form).getByRole('radio', { name: 'Abajo a la derecha' })).toBeChecked();
    expect(screen.getByRole('switch', { name: 'Usar por defecto' })).toBeChecked();
    expect(screen.getByText(/Fotograma de muestra con Inter y el logo abajo a la derecha/)).toBeInTheDocument();
    // Nothing changed yet: no save bar.
    expect(saveBar()).not.toBeInTheDocument();
  });

  it('al cambiar algo aparece la barra con lo que cambió, y Descartar vuelve atrás', async () => {
    const form = await open();
    fireEvent.change(within(form).getByLabelText('Palabra activa'), { target: { value: '#3DD6C4' } });
    fireEvent.click(within(form).getByRole('radio', { name: 'Arriba a la izquierda' }));
    const bar = saveBar() as HTMLElement;
    expect(bar).toHaveTextContent('Cambios sin guardar');
    expect(bar).toHaveTextContent('Cambiaste el color de la palabra activa y la posición del logo.');
    expect(screen.getByText(/el logo arriba a la izquierda/)).toBeInTheDocument();

    fireEvent.click(within(bar).getByRole('button', { name: 'Descartar' }));
    expect(within(form).getByLabelText('Palabra activa')).toHaveValue('#F2C94C');
    expect(within(form).getByRole('radio', { name: 'Abajo a la derecha' })).toBeChecked();
    expect(saveBar()).not.toBeInTheDocument();
  });

  it('guardar manda solo lo que cambió', async () => {
    const form = await open();
    fireEvent.change(within(form).getByLabelText('Nombre'), { target: { value: 'Estudio Norte 2027' } });
    fireEvent.change(within(form).getByLabelText('Fondo'), { target: { value: '#000000' } });
    fireEvent.click(within(form).getAllByRole('button', { name: 'Quitar' })[0] as HTMLElement);
    fireEvent.click(within(saveBar() as HTMLElement).getByRole('button', { name: 'Guardar' }));
    expect(writes.update).toHaveBeenCalledTimes(1);
    expect(writes.update.mock.calls[0]?.[0]).toEqual({ name: 'Estudio Norte 2027', secondary_color: '#000000', primary_font: '' });
  });

  it('valida el nombre y los colores antes de guardar', async () => {
    const form = await open();
    fireEvent.change(within(form).getByLabelText('Nombre'), { target: { value: '   ' } });
    fireEvent.change(within(form).getByLabelText('Texto'), { target: { value: 'blanco' } });
    fireEvent.click(within(saveBar() as HTMLElement).getByRole('button', { name: 'Guardar' }));
    expect(await within(form).findByText('Ponele un nombre al brand kit.')).toBeInTheDocument();
    expect(within(form).getByText('Usá un color hex de 6 dígitos, como #F2C94C.')).toBeInTheDocument();
    expect(writes.update).not.toHaveBeenCalled();
  });

  it('un hex corto se completa al salir del campo', async () => {
    const form = await open();
    const input = within(form).getByLabelText('Texto');
    fireEvent.change(input, { target: { value: 'f2c' } });
    fireEvent.blur(input);
    expect(input).toHaveValue('#FF22CC');
  });

  it('rechaza archivos que no son del tipo pedido y acepta los que sí', async () => {
    const form = await open();
    const font = within(form).getByLabelText('Fuente secundaria');
    fireEvent.change(font, { target: { files: [new File(['x'], 'marca.woff2')] } });
    expect(await within(form).findByText('Subí una fuente .ttf u .otf.')).toBeInTheDocument();
    expect(saveBar()).not.toBeInTheDocument();

    fireEvent.change(font, { target: { files: [new File(['x'], 'Marca-Regular.otf')] } });
    expect(await within(form).findByText('Marca')).toBeInTheDocument();
    expect(within(form).queryByText('Subí una fuente .ttf u .otf.')).not.toBeInTheDocument();
    expect(saveBar()).toHaveTextContent('Cambiaste la fuente secundaria.');

    fireEvent.change(within(form).getByLabelText('Logo de marca de agua'), { target: { files: [new File(['x'], 'logo.jpg')] } });
    expect(await within(form).findByText('Subí una imagen PNG.')).toBeInTheDocument();
  });

  it('salir con cambios pregunta antes: Seguir editando o Descartar', async () => {
    const form = await open();
    fireEvent.change(within(form).getByLabelText(/Descripción/), { target: { value: 'Otra descripción' } });
    fireEvent.click(screen.getByRole('link', { name: 'Brand kits' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    expect(dialog).toHaveTextContent('los cambios de «Estudio Norte»');
    fireEvent.click(within(dialog).getByRole('button', { name: 'Seguir editando' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    expect(within(form).getByLabelText(/Descripción/)).toHaveValue('Otra descripción');
  });

  it('Enter en un campo de color guarda el hex ya normalizado', async () => {
    const form = await open();
    const input = within(form).getByLabelText('Palabra activa');
    // No blur: the form is submitted from inside the field.
    fireEvent.change(input, { target: { value: '3dd' } });
    fireEvent.submit(form);
    expect(writes.update).toHaveBeenCalledTimes(1);
    expect(writes.update.mock.calls[0]?.[0]).toEqual({ accent_color: '#33DDDD' });
    expect(input).toHaveValue('#33DDDD');
  });

  it('el botón Atrás del navegador también pregunta', async () => {
    const pushState = vi.spyOn(window.history, 'pushState');
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    const form = await open();
    expect(pushState).not.toHaveBeenCalled();
    fireEvent.change(within(form).getByLabelText(/Descripción/), { target: { value: 'Otra descripción' } });
    // With changes there is one more history entry for this same page: Back lands on it instead of leaving.
    await waitFor(() => expect(pushState).toHaveBeenCalledTimes(1));
    fireEvent.popState(window);
    const dialog = await screen.findByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Seguir editando' }));
    await waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());
    // Staying puts the entry back, so the next Back asks again.
    expect(pushState).toHaveBeenCalledTimes(2);
    expect(within(form).getByLabelText(/Descripción/)).toHaveValue('Otra descripción');

    fireEvent.popState(window);
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: /Descartar/ }));
    expect(back).toHaveBeenCalledTimes(1);
    pushState.mockRestore();
    back.mockRestore();
  });

  it('una navegación desde el menú de comandos, las notificaciones o el selector de workspace también pregunta', async () => {
    const form = await open();
    // Without changes the router is untouched.
    router.object.push('/uso');
    expect(router.original).toHaveBeenLastCalledWith('/uso');
    router.original.mockClear();

    fireEvent.change(within(form).getByLabelText(/Descripción/), { target: { value: 'Otra descripción' } });
    await waitFor(() => expect(router.object.push).not.toBe(router.original));
    // What the shell does: `router.push()` from code, with no link to intercept.
    router.object.push('/proyectos');
    const dialog = await screen.findByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    expect(router.original).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole('button', { name: /Descartar/ }));
    // The extra history entry goes first, then the navigation.
    await waitFor(() => expect(router.original).toHaveBeenCalledWith('/proyectos', undefined));
  });

  it('sin `watermark_position` en la respuesta (el backend de hoy) no hay selector de esquina ni se manda el campo', async () => {
    server.use(
      http.get('http://localhost:8000/api/v1/brand-kits/:id/', ({ params }) => {
        const { watermark_position: _absent, ...today } = loadDb().brandKits.find((kit) => kit.id === params.id) ?? {};
        void _absent;
        return HttpResponse.json(today);
      }),
    );
    const form = await open();
    expect(within(form).getByText('estudio-norte.png')).toBeInTheDocument();
    expect(within(form).queryByRole('radiogroup')).not.toBeInTheDocument();
    expect(screen.getByText(/el logo abajo a la derecha/)).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText('Nombre'), { target: { value: 'Otro nombre' } });
    fireEvent.click(within(saveBar() as HTMLElement).getByRole('button', { name: 'Guardar' }));
    expect(writes.update.mock.calls[0]?.[0]).toEqual({ name: 'Otro nombre' });
  });

  it('`nuevo` crea un kit con todos sus datos', async () => {
    const form = await open('nuevo');
    expect(screen.getByRole('heading', { level: 1, name: 'Nuevo brand kit' })).toBeInTheDocument();
    const bar = saveBar() as HTMLElement;
    fireEvent.click(within(bar).getByRole('button', { name: 'Crear brand kit' }));
    expect(await within(form).findByText('Ponele un nombre al brand kit.')).toBeInTheDocument();
    expect(writes.create).not.toHaveBeenCalled();

    fireEvent.change(within(form).getByLabelText('Nombre'), { target: { value: 'Podcast Vender' } });
    fireEvent.change(within(form).getByLabelText('Palabra activa'), { target: { value: '#3DD6C4' } });
    fireEvent.click(screen.getByRole('switch', { name: 'Usar por defecto' }));
    fireEvent.click(within(bar).getByRole('button', { name: 'Crear brand kit' }));
    expect(writes.create.mock.calls[0]?.[0]).toEqual({
      name: 'Podcast Vender',
      description: '',
      primary_color: '#FFFFFF',
      secondary_color: '#000000',
      accent_color: '#3DD6C4',
      watermark_position: 'bottom_right',
      is_default: true,
    });
  });

  it('plan Gratis: avisa que el plan agrega su marca de agua', async () => {
    await open(EXAMPLE_IDS.brandKit, 'plan-gratis');
    expect(await screen.findByText(/Tu plan agrega la marca de agua de OneCreator/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Ver planes' })).toHaveAttribute('href', '/uso/planes');
  });

  it('rol Lector: solo lectura', async () => {
    const form = await open(EXAMPLE_IDS.brandKit, 'rol-lector');
    expect(screen.getByText('Solo lectura')).toBeInTheDocument();
    expect(within(form).getByLabelText('Nombre')).toBeDisabled();
    expect(within(form).getByLabelText('Palabra activa')).toBeDisabled();
    expect(within(form).getByRole('button', { name: 'Borrar' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('switch', { name: 'Usar por defecto' })).toBeDisabled();
  });

  it('un kit que no existe muestra el 404', async () => {
    renderScreen(<BrandKitEditor id="00000009-0000-4000-8000-00000000ffff" />);
    expect(await screen.findByText(/no existe|No encontramos/i)).toBeInTheDocument();
  });
});
