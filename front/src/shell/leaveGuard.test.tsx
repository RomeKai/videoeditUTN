/* eslint-disable @next/next/no-html-link-for-pages -- the guard watches plain anchors, which is what next/link renders */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { UnsavedChangesDialog } from '@/dialogs';
import { ToastProvider } from '@/ui/Toast';
import { LeaveGuardProvider, POP_SAFETY_MS, useGuardedNavigation, useLeaveGuard, type LeaveGuardOptions, type ScreenLeaveGuard } from './leaveGuard';
import { WorkspaceList } from './WorkspaceSwitcher';

/** One router for the whole app, as in the real one. Nobody may replace its methods. */
const router = vi.hoisted(() => {
  const object = { push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn(), forward: vi.fn() };
  return { object, push: object.push, replace: object.replace };
});
vi.mock('next/navigation', () => ({ useRouter: () => router.object, usePathname: () => window.location.pathname }));

const session = vi.hoisted(() => ({ switchWorkspace: vi.fn<(id: string) => Promise<boolean>>() }));
vi.mock('./session', () => ({
  useSession: () => ({
    me: {
      workspaces: [
        { id: 'ws-1', name: 'Estudio Norte', role: 'owner', plan: null, available_balance: '100.00' },
        { id: 'ws-2', name: 'Personal', role: 'owner', plan: null, available_balance: '5.00' },
      ],
    },
    workspace: { id: 'ws-1', name: 'Estudio Norte', role: 'owner', plan: null, available_balance: '100.00' },
    switchWorkspace: session.switchWorkspace,
  }),
}));

let guard: ScreenLeaveGuard;
let shell: ReturnType<typeof useGuardedNavigation>;
const discarded = vi.fn();

function Editor({ dirty, options }: { dirty: boolean; options?: LeaveGuardOptions }) {
  guard = useLeaveGuard(dirty, { onDiscard: discarded, ...options });
  shell = useGuardedNavigation();
  return (
    <>
      <a href="/publicaciones">Publicaciones</a>
      <a href="/uso/comprar">Comprar tokens</a>
      <a href="/brand-kits/x#logo">Logo</a>
      <UnsavedChangesDialog {...guard.dialog} what="los cambios de «Estudio Norte»" />
    </>
  );
}

const app = (dirty: boolean, options?: LeaveGuardOptions, extra?: React.ReactNode) => (
  <ToastProvider>
    <LeaveGuardProvider>
      <Editor dirty={dirty} options={options} />
      {extra}
    </LeaveGuardProvider>
  </ToastProvider>
);

/** Lets the browser finish a step in the history (`popstate` comes later, as in a real one). */
const historySettles = () => act(() => new Promise<void>((resolve) => setTimeout(resolve, 20)));
const dialog = () => screen.findByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
const noDialog = () => waitFor(() => expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument());

/** Clicks a link and says whether the guard stopped it. (jsdom cannot follow links: the click ends here either way.) */
const clickLink = (name: string): 'held' | 'followed' => {
  let held = false;
  const watch = (event: MouseEvent) => {
    held = event.defaultPrevented;
    event.preventDefault();
  };
  window.addEventListener('click', watch);
  // The guard stops the click on its way down, so it never gets back up to `window`.
  const reached = { window: false };
  const mark = () => (reached.window = true);
  window.addEventListener('click', mark);
  fireEvent.click(screen.getByRole('link', { name }));
  window.removeEventListener('click', watch);
  window.removeEventListener('click', mark);
  return held || !reached.window ? 'held' : 'followed';
};

/** Calls in the order they happened: the step back in the history must come before the navigation. */
let order: string[] = [];

beforeEach(() => {
  order = [];
  router.object.push.mockReset().mockImplementation((href: string) => void order.push(`push ${href}`));
  router.object.replace.mockReset().mockImplementation((href: string) => void order.push(`replace ${href}`));
  session.switchWorkspace.mockReset().mockResolvedValue(true);
  discarded.mockReset();
  // A history of its own for each test: the list, then the editor.
  window.history.pushState(null, '', '/brand-kits');
  window.history.pushState(null, '', '/brand-kits/x');
});
afterEach(async () => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  await new Promise((resolve) => setTimeout(resolve, 20));
});

describe('guarda de salida · registro', () => {
  it('sin cambios no hay entrada de más, ni aviso al cerrar, ni pregunta', () => {
    const pushState = vi.spyOn(window.history, 'pushState');
    render(app(false));
    expect(pushState).not.toHaveBeenCalled();
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(true);
    act(() => shell.push('/uso'));
    expect(router.push).toHaveBeenCalledWith('/uso');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('con cambios: una entrada de más para la misma página y aviso del navegador al cerrar o recargar', async () => {
    const pushState = vi.spyOn(window.history, 'pushState');
    const view = render(app(true));
    expect(pushState).toHaveBeenCalledTimes(1);
    expect(window.location.pathname).toBe('/brand-kits/x');
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(false);

    // Clean again (saved, or the changes were undone): the entry goes away and the tab closes without asking.
    const back = vi.spyOn(window.history, 'back');
    view.rerender(app(false));
    await historySettles();
    expect(back).toHaveBeenCalledTimes(1);
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(true);
    // Nobody touched the router: the guard is asked, it does not wrap `push`.
    expect(router.object.push).toBe(router.push);
  });

  it('nadie reemplaza los métodos del router', () => {
    render(app(true));
    expect(router.object.push).toBe(router.push);
    expect(router.object.replace).toBe(router.replace);
  });
});

describe('guarda de salida · links de la app', () => {
  it('un link pregunta; «Seguir editando» se queda y «Descartar» quita la entrada y recién después navega', async () => {
    render(app(true));
    fireEvent.click(screen.getByRole('link', { name: 'Publicaciones' }));
    expect(await dialog()).toHaveTextContent('los cambios de «Estudio Norte»');
    fireEvent.click(screen.getByRole('button', { name: 'Seguir editando' }));
    await noDialog();
    expect(router.push).not.toHaveBeenCalled();
    expect(discarded).not.toHaveBeenCalled();

    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {
      order.push('back');
      setTimeout(() => window.dispatchEvent(new PopStateEvent('popstate')), 5);
    });
    fireEvent.click(screen.getByRole('link', { name: 'Publicaciones' }));
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    expect(discarded).toHaveBeenCalledTimes(1);
    expect(back).toHaveBeenCalledTimes(1);
    expect(router.push).not.toHaveBeenCalled();
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/publicaciones'));
    expect(order).toEqual(['back', 'push /publicaciones']);
    // Let go: the tab closes without the browser's prompt.
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(true);
  });

  it('un link a la misma página, o uno que la pantalla permite, no pregunta', async () => {
    const back = vi.spyOn(window.history, 'back');
    const view = render(app(true, { allow: (href) => href.startsWith('/uso/comprar') }));
    expect(clickLink('Logo')).toBe('followed');
    expect(clickLink('Comprar tokens')).toBe('followed');
    expect(clickLink('Publicaciones')).toBe('held');
    fireEvent.click(await screen.findByRole('button', { name: 'Seguir editando' }));

    // The link that never asks went through and left the extra entry buried under the new page:
    // when the screen goes away, the guard must not step back from the page the person is on.
    window.history.pushState(null, '', '/uso/comprar');
    view.unmount();
    await historySettles();
    expect(back).not.toHaveBeenCalled();
    expect(window.location.pathname).toBe('/uso/comprar');
  });
});

describe('guarda de salida · navegaciones del shell', () => {
  it('el menú de comandos y las notificaciones (push desde código) preguntan antes de navegar', async () => {
    render(app(true));
    act(() => shell.push('/proyectos'));
    await dialog();
    expect(router.push).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/proyectos'));
    expect(discarded).toHaveBeenCalledTimes(1);
  });

  it('el selector de workspace pregunta antes de cambiar: con «Seguir editando» no cambia nada', async () => {
    render(app(true, undefined, <div role="menu"><WorkspaceList onDone={() => undefined} onCreate={() => undefined} /></div>));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Personal/ }));
    await dialog();
    expect(session.switchWorkspace).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Seguir editando' }));
    await noDialog();
    expect(session.switchWorkspace).not.toHaveBeenCalled();
    expect(discarded).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('menuitemradio', { name: /Personal/ }));
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await waitFor(() => expect(session.switchWorkspace).toHaveBeenCalledWith('ws-2'));
    expect(discarded).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/proyectos'));
    expect(await screen.findByText('Ahora estás en Personal')).toBeInTheDocument();
  });

  it('sin cambios, el selector cambia de workspace sin preguntar', async () => {
    render(app(false, undefined, <div role="menu"><WorkspaceList onDone={() => undefined} onCreate={() => undefined} /></div>));
    fireEvent.click(screen.getByRole('menuitemradio', { name: /Personal/ }));
    expect(session.switchWorkspace).toHaveBeenCalledWith('ws-2');
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/proyectos'));
  });

  it('si el cambio de workspace falla después de «Descartar», la pantalla que sigue ahí vuelve a estar protegida', async () => {
    session.switchWorkspace.mockRejectedValue(new Error('sin red'));
    render(app(true));
    act(() => shell.confirm(() => session.switchWorkspace('ws-2')));
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await waitFor(() => expect(session.switchWorkspace).toHaveBeenCalled());
    await historySettles();
    act(() => shell.push('/proyectos'));
    await dialog();
    expect(router.push).not.toHaveBeenCalled();
  });
});

describe('guarda de salida · Atrás', () => {
  it('Atrás cae en la misma pantalla y pregunta; quedarse la deja protegida para el próximo Atrás', async () => {
    render(app(true));
    window.history.back();
    await dialog();
    expect(window.location.pathname).toBe('/brand-kits/x');
    fireEvent.click(screen.getByRole('button', { name: 'Seguir editando' }));
    await noDialog();

    window.history.back();
    await dialog();
    expect(window.location.pathname).toBe('/brand-kits/x');
    // Back once more with the question still open: it is held too.
    window.history.back();
    await historySettles();
    expect(window.location.pathname).toBe('/brand-kits/x');
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    expect(discarded).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(window.location.pathname).toBe('/brand-kits'));
    // The router did not navigate: the browser did.
    expect(router.push).not.toHaveBeenCalled();
  });
});

describe('guarda de salida · la entrada de más del historial', () => {
  it('después de guardar se quita antes de navegar, y el reemplazo cae en la entrada real', async () => {
    const length = window.history.length;
    render(app(true));
    expect(window.history.length).toBe(length + 1);
    router.object.replace.mockImplementation((href: string) => window.history.replaceState(null, '', href));
    act(() => guard.leaveNow('/brand-kits/nuevo-id', { replace: true }));
    expect(router.replace).not.toHaveBeenCalled();
    await waitFor(() => expect(router.replace).toHaveBeenCalledWith('/brand-kits/nuevo-id'));
    expect(window.location.pathname).toBe('/brand-kits/nuevo-id');
    // One step back is the list: neither the old screen nor a copy of it is left in between.
    window.history.back();
    await waitFor(() => expect(window.location.pathname).toBe('/brand-kits'));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('si el navegador tarda en volver (más de medio segundo), la navegación espera a que termine', async () => {
    vi.useFakeTimers();
    render(app(true));
    vi.spyOn(window.history, 'back').mockImplementation(() => {
      order.push('back');
      setTimeout(() => {
        order.push('popstate');
        window.dispatchEvent(new PopStateEvent('popstate'));
      }, 1200);
    });
    act(() => shell.push('/publicaciones'));
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await act(() => vi.advanceTimersByTimeAsync(1100));
    expect(router.push).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(200));
    expect(order).toEqual(['back', 'popstate', 'push /publicaciones']);
  });

  it('un link que se toca mientras la entrada se está yendo espera a que el historial quede quieto', async () => {
    vi.useFakeTimers();
    const view = render(app(true));
    vi.spyOn(window.history, 'back').mockImplementation(() => {
      order.push('back');
      setTimeout(() => window.dispatchEvent(new PopStateEvent('popstate')), 800);
    });
    view.rerender(app(false));
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(order).toEqual(['back']);
    // Stopped here and navigated by the guard afterwards: a push before the step back ends would be undone by it.
    expect(clickLink('Publicaciones')).toBe('held');
    expect(router.push).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(800));
    expect(order).toEqual(['back', 'push /publicaciones']);
  });

  it('si el aviso del navegador no llega nunca, la app no queda trabada; y si llega tarde, deshace el paso atrás de más', async () => {
    vi.useFakeTimers();
    render(app(true));
    vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    const forward = vi.spyOn(window.history, 'forward').mockImplementation(() => undefined);
    act(() => shell.push('/publicaciones'));
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await act(() => vi.advanceTimersByTimeAsync(POP_SAFETY_MS + 10));
    expect(router.push).toHaveBeenCalledWith('/publicaciones');
    // The late step back took the person off the page they navigated to: forward puts them on it again.
    act(() => void window.dispatchEvent(new PopStateEvent('popstate')));
    expect(forward).toHaveBeenCalledTimes(1);
  });
});
