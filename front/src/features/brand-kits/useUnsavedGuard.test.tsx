import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { interceptPush, useUnsavedGuard } from './useUnsavedGuard';

/** One router for the whole app, as in the real one. */
const router = vi.hoisted(() => {
  const push = vi.fn();
  return { original: push, object: { push, replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn(), forward: vi.fn() } };
});
vi.mock('next/navigation', () => ({ useRouter: () => router.object, usePathname: () => '/brand-kits/x' }));

afterEach(() => {
  cleanup();
  router.original.mockReset();
  router.object.push = router.original;
  vi.restoreAllMocks();
});

/** `history.back()` as the browser does it: the entry changes and `popstate` comes later. */
const fakeHistory = () => {
  const pushState = vi.spyOn(window.history, 'pushState').mockImplementation(() => undefined);
  const back = vi.spyOn(window.history, 'back').mockImplementation(() => void setTimeout(() => window.dispatchEvent(new PopStateEvent('popstate')), 5));
  return { pushState, back };
};

describe('D8 · navegaciones desde código', () => {
  it('dos guardas a la vez: salgan en el orden que salgan, el router queda como estaba', () => {
    const held: string[] = [];
    const first = interceptPush(router.object, (href) => held.push(`a:${href}`) > 0);
    const second = interceptPush(router.object, () => false);
    expect(router.object.push).not.toBe(router.original);
    // Not a stack: the first one in is the first one out.
    first();
    router.object.push('/uso');
    expect(held).toEqual([]);
    expect(router.original).toHaveBeenCalledWith('/uso', undefined);
    expect(router.object.push).not.toBe(router.original);
    second();
    expect(router.object.push).toBe(router.original);
    // And nothing of a guard that is gone is left to swallow a navigation.
    router.object.push('/proyectos');
    expect(router.original).toHaveBeenLastCalledWith('/proyectos');
  });

  it('dos pantallas con cambios: al limpiarse una, la otra sigue preguntando y después el router queda libre', () => {
    fakeHistory();
    const a = renderHook(({ dirty }) => useUnsavedGuard(dirty), { initialProps: { dirty: true } });
    const b = renderHook(({ dirty }) => useUnsavedGuard(dirty), { initialProps: { dirty: true } });
    a.rerender({ dirty: false });
    act(() => router.object.push('/proyectos'));
    expect(router.original).not.toHaveBeenCalled();
    expect(b.result.current.asking).toBe(true);
    expect(a.result.current.asking).toBe(false);
    act(() => b.result.current.stay());
    b.rerender({ dirty: false });
    expect(router.object.push).toBe(router.original);
  });
});

describe('D8 · la entrada de más del historial', () => {
  it('después de guardar se quita antes de navegar: `release()` espera a que el navegador vuelva', async () => {
    const { pushState, back } = fakeHistory();
    const { result } = renderHook(() => useUnsavedGuard(true));
    expect(pushState).toHaveBeenCalledTimes(1);
    let released = false;
    await act(async () => {
      await result.current.release().then(() => (released = true));
    });
    expect(back).toHaveBeenCalledTimes(1);
    expect(released).toBe(true);
    // Released: nothing asks, and no new entry is added while the screen is still the same.
    act(() => router.object.push('/brand-kits/nuevo-id'));
    expect(result.current.asking).toBe(false);
    expect(pushState).toHaveBeenCalledTimes(1);
  });

  it('«Descartar» por un link quita la entrada y recién después navega', async () => {
    const { back } = fakeHistory();
    const { result } = renderHook(() => useUnsavedGuard(true));
    act(() => result.current.leave('/publicaciones'));
    expect(result.current.asking).toBe(true);
    act(() => result.current.discard());
    expect(back).toHaveBeenCalledTimes(1);
    expect(router.original).not.toHaveBeenCalled();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 30));
    });
    expect(router.original).toHaveBeenCalledWith('/publicaciones', undefined);
  });
});
