/* eslint-disable @next/next/no-html-link-for-pages -- the guard watches plain anchors, which is what next/link renders */
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useActiveWorkspaceId } from '@/api/hooks';
import { UnsavedChangesDialog } from '@/dialogs';
import { getActiveWorkspaceId, getDeferredWorkspaceId, setActiveWorkspaceId, WORKSPACE_STORAGE_KEY } from '@/api/session';
import { LeaveGuardProvider, LEAVE_SAFETY_MS, POP_SAFETY_MS, useGuardedNavigation, useLeaveGuard, type LeaveGuardOptions, type ScreenLeaveGuard } from './leaveGuard';

/** One router for the whole app, as in the real one. Nobody may replace its methods. */
const router = vi.hoisted(() => {
  const object = { push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn(), forward: vi.fn() };
  return { object, push: object.push, replace: object.replace };
});
vi.mock('next/navigation', () => ({ useRouter: () => router.object, usePathname: () => window.location.pathname }));

let guard: ScreenLeaveGuard;
let shell: ReturnType<typeof useGuardedNavigation>;
const discarded = vi.fn();

/** What the link's own handler saw: a menu that closes on click must still close when the guard holds the navigation. */
const linkClicked = vi.fn();
/** Clicks that reached the link with nothing holding them: the browser (next/link) follows those. */
const followed = vi.fn();

function Editor({ dirty, options, what = 'los cambios de «Estudio Norte»' }: { dirty: boolean; options?: LeaveGuardOptions; what?: string }) {
  const own = useLeaveGuard(dirty, { onDiscard: () => discarded(what), ...options });
  if (what.includes('Estudio Norte')) guard = own;
  shell = useGuardedNavigation();
  return (
    <>
      <a
        href="/publicaciones"
        onClick={(event) => {
          linkClicked();
          if (!event.defaultPrevented) followed();
          // jsdom cannot follow links.
          event.preventDefault();
        }}
      >
        Publicaciones
      </a>
      <a href="/uso/comprar">Comprar tokens</a>
      <a href="/brand-kits/x#logo">Logo</a>
      <UnsavedChangesDialog {...own.dialog} what={what} />
    </>
  );
}

const app = (dirty: boolean, options?: LeaveGuardOptions, extra?: React.ReactNode) => (
  <LeaveGuardProvider>
    <Editor dirty={dirty} options={options} />
    {extra}
  </LeaveGuardProvider>
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
  discarded.mockReset();
  linkClicked.mockReset();
  followed.mockReset();
  // A history of its own for each test: the list, then the editor.
  window.history.pushState(null, '', '/brand-kits');
  window.history.pushState(null, '', '/brand-kits/x');
});
afterEach(async () => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  setActiveWorkspaceId(null);
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
    const screenNo = (key: number) => (
      <LeaveGuardProvider>
        <Editor key={key} dirty />
      </LeaveGuardProvider>
    );
    const view = render(screenNo(1));
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
  it('un link pregunta; «Seguir editando» se queda y «Descartar» quita la entrada y recién después lo sigue', async () => {
    render(app(true));
    fireEvent.click(screen.getByRole('link', { name: 'Publicaciones' }));
    expect(await dialog()).toHaveTextContent('los cambios de «Estudio Norte»');
    // Only the navigation is held: the link's own handler ran (the menu it lives in closes).
    expect(linkClicked).toHaveBeenCalledTimes(1);
    expect(followed).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Seguir editando' }));
    await noDialog();
    expect(followed).not.toHaveBeenCalled();
    expect(discarded).not.toHaveBeenCalled();

    followed.mockImplementation(() => void order.push('link'));
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => {
      order.push('back');
      setTimeout(() => window.dispatchEvent(new PopStateEvent('popstate')), 5);
    });
    fireEvent.click(screen.getByRole('link', { name: 'Publicaciones' }));
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    expect(discarded).toHaveBeenCalledTimes(1);
    expect(back).toHaveBeenCalledTimes(1);
    expect(followed).not.toHaveBeenCalled();
    // The link itself is clicked again, so it navigates as its component says (its `replace`, its `scroll`).
    await waitFor(() => expect(followed).toHaveBeenCalledTimes(1));
    expect(order).toEqual(['back', 'link']);
    expect(router.push).not.toHaveBeenCalled();
    // Let go: the tab closes without the browser's prompt.
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(true);
  });

  it('si el link ya no está en la página (su menú se cerró al tocarlo), «Descartar» navega igual', async () => {
    function Menu() {
      const [open, setOpen] = useState(true);
      return open ? (
        <a href="/perfil" onClick={() => setOpen(false)}>
          Perfil
        </a>
      ) : null;
    }
    render(app(true, undefined, <Menu />));
    fireEvent.click(screen.getByRole('link', { name: 'Perfil' }));
    await dialog();
    // The menu closed, as on any click.
    expect(screen.queryByRole('link', { name: 'Perfil' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/perfil'));
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

  it('`request()` dice si se puede salir: sí sin cambios o después de «Descartar», no con «Seguir editando»', async () => {
    const view = render(app(false));
    await expect(shell.request()).resolves.toBe(true);

    view.rerender(app(true));
    let answer: boolean | 'pendiente' = 'pendiente';
    act(() => void shell.request().then((value) => (answer = value)));
    await dialog();
    expect(answer).toBe('pendiente');
    fireEvent.click(screen.getByRole('button', { name: 'Seguir editando' }));
    await waitFor(() => expect(answer).toBe(false));
    expect(discarded).not.toHaveBeenCalled();

    answer = 'pendiente';
    act(() => void shell.request().then((value) => (answer = value)));
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await waitFor(() => expect(answer).toBe(true));
    expect(discarded).toHaveBeenCalledTimes(1);
  });

  it('si lo que venía después de «Descartar» falla, la pantalla que sigue ahí vuelve a estar protegida', async () => {
    render(app(true));
    act(() => shell.confirm(() => Promise.reject(new Error('sin red'))));
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await historySettles();
    act(() => shell.push('/proyectos'));
    await dialog();
    expect(router.push).not.toHaveBeenCalled();
  });

  it('pero si mientras tanto arrancó otra navegación, la falla vieja no vuelve a frenar la pantalla que se está yendo', async () => {
    render(app(true));
    let fail: (reason: Error) => void = () => undefined;
    act(() => shell.confirm(() => new Promise((_resolve, reject) => (fail = reject))));
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await historySettles();
    // The person goes somewhere else while the first action is still running…
    act(() => shell.push('/proyectos'));
    expect(router.push).toHaveBeenCalledWith('/proyectos');
    // …and then it fails.
    await act(async () => fail(new Error('sin red')));
    await historySettles();
    act(() => shell.push('/uso'));
    expect(router.push).toHaveBeenLastCalledWith('/uso');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
  });

  it('dos pantallas con cambios: si la que preguntaba se va con la pregunta abierta, la otra la toma', async () => {
    const two = (showTop: boolean) => (
      <LeaveGuardProvider>
        <Editor dirty />
        {showTop ? <Editor dirty what="el texto de la publicación" /> : null}
      </LeaveGuardProvider>
    );
    const view = render(two(true));
    act(() => shell.push('/proyectos'));
    // The one on top asks.
    expect(await dialog()).toHaveTextContent('el texto de la publicación');
    view.rerender(two(false));
    // The navigation is not thrown away: the screen that is left asks for it.
    await waitFor(() => expect(screen.getByRole('alertdialog')).toHaveTextContent('los cambios de «Estudio Norte»'));
    expect(router.push).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/proyectos'));
  });

  it('si la única pantalla con cambios se va con la pregunta abierta, no queda nada colgado', async () => {
    const screenNo = (key: number) => (
      <LeaveGuardProvider>
        <Editor key={key} dirty />
      </LeaveGuardProvider>
    );
    const view = render(screenNo(1));
    let answer: boolean | 'pendiente' = 'pendiente';
    act(() => void shell.request().then((value) => (answer = value)));
    await dialog();
    view.rerender(app(false));
    await noDialog();
    await waitFor(() => expect(answer).toBe(false));
    act(() => shell.push('/uso'));
    await waitFor(() => expect(router.push).toHaveBeenCalledWith('/uso'));
  });
});

describe('guarda de salida · otra pestaña cambia de workspace', () => {
  const otherTabSwitches = (from: string, to: string) => {
    window.localStorage.setItem(WORKSPACE_STORAGE_KEY, to);
    act(() => void window.dispatchEvent(new StorageEvent('storage', { key: WORKSPACE_STORAGE_KEY, oldValue: from, newValue: to })));
  };
  function Workspace() {
    return <p>workspace: {useActiveWorkspaceId()}</p>;
  }

  it('con cambios sin guardar esta pestaña se queda en su workspace; sin cambios, sigue a la otra', async () => {
    setActiveWorkspaceId('ws-1');
    const view = render(app(true, undefined, <Workspace />));
    otherTabSwitches('ws-1', 'ws-2');
    // Following would reload the screen in the other workspace and lose what is being edited.
    expect(screen.getByText('workspace: ws-1')).toBeInTheDocument();
    expect(getActiveWorkspaceId()).toBe('ws-1');
    // The shell knows where the other tab went, to offer it.
    expect(getDeferredWorkspaceId()).toBe('ws-2');

    // Saved (or discarded): it still does not move on its own; the person decides.
    view.rerender(app(false, undefined, <Workspace />));
    await historySettles();
    expect(getActiveWorkspaceId()).toBe('ws-1');
    act(() => setActiveWorkspaceId('ws-2'));
    expect(screen.getByText('workspace: ws-2')).toBeInTheDocument();
    expect(getDeferredWorkspaceId()).toBeNull();

    // Without changes on screen the tab follows, as always.
    otherTabSwitches('ws-2', 'ws-1');
    expect(screen.getByText('workspace: ws-1')).toBeInTheDocument();
    expect(getDeferredWorkspaceId()).toBeNull();
  });

  it('si la otra pestaña vuelve al workspace de esta, no queda nada pendiente', () => {
    setActiveWorkspaceId('ws-1');
    render(app(true, undefined, <Workspace />));
    otherTabSwitches('ws-1', 'ws-2');
    expect(getDeferredWorkspaceId()).toBe('ws-2');
    otherTabSwitches('ws-2', 'ws-1');
    expect(getDeferredWorkspaceId()).toBeNull();
    expect(getActiveWorkspaceId()).toBe('ws-1');
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

describe('guarda de salida · Atrás con entradas de antes', () => {
  const TOKEN = '__ocLeaveGuard';

  it('una entrada de más que ya estaba (recarga, ida y vuelta por un link permitido) se retoma: «Descartar» sale de la pantalla', async () => {
    // The page was reloaded on the extra entry: the browser kept it, with its mark.
    window.history.pushState({ [TOKEN]: 'de-antes' }, '', '/brand-kits/x');
    const pushState = vi.spyOn(window.history, 'pushState');
    render(app(true));
    // Not another one on top: two entries of this screen would need two "Descartar" to get out.
    expect(pushState).not.toHaveBeenCalled();

    window.history.back();
    await dialog();
    expect(window.location.pathname).toBe('/brand-kits/x');
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await waitFor(() => expect(window.location.pathname).toBe('/brand-kits'));
    await historySettles();
    expect(window.location.pathname).toBe('/brand-kits');
  });

  it('si al salir queda debajo otra entrada de más de la misma pantalla, da un paso más', async () => {
    // list · extra entry from before · the screen again (a full load on top of it)
    window.history.replaceState({ [TOKEN]: 'de-antes' }, '', '/brand-kits/x');
    window.history.pushState(null, '', '/brand-kits/x');
    render(app(true));
    window.history.back();
    await dialog();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await waitFor(() => expect(window.location.pathname).toBe('/brand-kits'));
  });

  it('una pestaña que arranca en la pantalla no suma la entrada (Atrás no tiene a dónde ir), pero sigue protegida', async () => {
    vi.stubGlobal('navigation', { currentEntry: { index: 0 } });
    const pushState = vi.spyOn(window.history, 'pushState');
    render(app(true));
    expect(pushState).not.toHaveBeenCalled();
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(false);
    fireEvent.click(screen.getByRole('link', { name: 'Publicaciones' }));
    await dialog();
    expect(followed).not.toHaveBeenCalled();
  });

  it('si «Descartar» no tiene a dónde volver, la pantalla sigue ahí y vuelve a estar protegida', async () => {
    render(app(true));
    window.history.back();
    await dialog();
    // The browser refuses to go back (nothing before the screen after all).
    vi.spyOn(window.history, 'go').mockImplementation(() => undefined);
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    await act(() => vi.advanceTimersByTimeAsync(LEAVE_SAFETY_MS + 10));
    expect(fireEvent(window, new Event('beforeunload', { cancelable: true }))).toBe(false);
    act(() => shell.push('/uso'));
    expect(router.push).not.toHaveBeenCalled();
    expect(screen.getByRole('alertdialog')).toBeInTheDocument();
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
    const screenNo = (key: number) => (
      <LeaveGuardProvider>
        <Editor key={key} dirty />
      </LeaveGuardProvider>
    );
    const view = render(screenNo(1));
    vi.spyOn(window.history, 'back').mockImplementation(() => {
      order.push('back');
      setTimeout(() => window.dispatchEvent(new PopStateEvent('popstate')), 800);
    });
    view.rerender(app(false));
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(order).toEqual(['back']);
    // Stopped here and followed by the guard afterwards: a navigation before the step back ends would be undone by it.
    followed.mockImplementation(() => void order.push('link'));
    fireEvent.click(screen.getByRole('link', { name: 'Publicaciones' }));
    expect(followed).not.toHaveBeenCalled();
    await act(() => vi.advanceTimersByTimeAsync(800));
    expect(order).toEqual(['back', 'link']);
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

  it('el paso atrás tardío solo se espera un rato: después, un Atrás vuelve a ser de la persona', async () => {
    vi.useFakeTimers();
    const screenNo = (key: number) => (
      <LeaveGuardProvider>
        <Editor key={key} dirty />
      </LeaveGuardProvider>
    );
    const view = render(screenNo(1));
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    const forward = vi.spyOn(window.history, 'forward').mockImplementation(() => undefined);
    act(() => shell.push('/publicaciones'));
    fireEvent.click(screen.getByRole('button', { name: 'Descartar' }));
    // It never came, and the wait for it is over too.
    await act(() => vi.advanceTimersByTimeAsync(POP_SAFETY_MS * 2 + 20));
    back.mockRestore();
    vi.useRealTimers();
    // A screen with changes again, and a real Back: it is held and asked, not bounced forward.
    view.rerender(screenNo(2));
    await historySettles();
    window.history.back();
    await dialog();
    expect(forward).not.toHaveBeenCalled();
  });
});
