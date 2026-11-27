import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearSession, getActiveWorkspaceId } from '@/api/session';
import { WORKSPACE_IDS } from '@/api/mocks/seed';
import { allowSlowScreens, db, editDb, renderScenario, startMockApi } from '@/testing';
import { RequireSession } from './RequireSession';

const navigation = vi.hoisted(() => ({ pathname: '/ajustes', replace: vi.fn(), push: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: navigation.push, replace: navigation.replace, back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

startMockApi();
allowSlowScreens();

describe('sin ningún workspace', () => {
  it('ofrece crear uno, y al crearlo entra a la app en él', async () => {
    const user = userEvent.setup();
    // Removed from everything: no workspace owned and no membership.
    const prepare = () =>
      editDb((data) => {
        data.workspaces = [];
        data.members = [];
      });
    renderScenario(
      <RequireSession>
        <p>adentro</p>
      </RequireSession>,
      { prepare },
    );

    expect(await screen.findByRole('heading', { name: 'No tenés ningún workspace' })).toBeInTheDocument();
    expect(screen.queryByText('adentro')).not.toBeInTheDocument();
    // The id of the workspace that is gone is not kept.
    await waitFor(() => expect(getActiveWorkspaceId()).toBeNull());

    await user.click(screen.getByRole('button', { name: 'Crear un espacio de trabajo' }));
    const dialog = await screen.findByRole('dialog', { name: 'Crear workspace' });
    await user.type(within(dialog).getByRole('textbox'), 'Mi canal');
    await user.click(within(dialog).getByRole('button', { name: 'Crear workspace' }));

    expect(await screen.findByText('adentro')).toBeInTheDocument();
    const created = db().workspaces.find((workspace) => workspace.name === 'Mi canal');
    expect(created).toBeTruthy();
    await waitFor(() => expect(getActiveWorkspaceId()).toBe(created?.id));
  });
});

describe('un link con su workspace (`?espacio=`): los emails de los avisos', () => {
  afterEach(() => {
    window.history.replaceState({}, '', '/');
    navigation.pathname = '/ajustes';
    navigation.replace.mockClear();
  });

  /** What the page of the link would ask the API with: the active workspace at each of its renders. */
  function page() {
    const seen: (string | null)[] = [];
    function Inside() {
      seen.push(getActiveWorkspaceId());
      return <p>adentro</p>;
    }
    return { seen, Inside };
  }

  function open(url: string) {
    window.history.replaceState({}, '', url);
    navigation.pathname = url.split('?')[0] ?? url;
  }

  it('cambia a ese workspace antes de mostrar la página y saca el parámetro de la URL', async () => {
    open(`/uso/comprar?espacio=${WORKSPACE_IDS.canal}&compra=ok`);
    const { seen, Inside } = page();
    renderScenario(
      <RequireSession>
        <Inside />
      </RequireSession>,
    );

    expect(await screen.findByText('adentro')).toBeInTheDocument();
    // Lucía was in Estudio Norte: the page never rendered there.
    expect(seen.length).toBeGreaterThan(0);
    expect(new Set(seen)).toEqual(new Set([WORKSPACE_IDS.canal]));
    expect(getActiveWorkspaceId()).toBe(WORKSPACE_IDS.canal);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/uso/comprar?compra=ok'));
  });

  it('en el workspace que ya está activo no cambia nada, solo limpia la URL', async () => {
    open(`/proyectos/abc?espacio=${WORKSPACE_IDS.estudio}`);
    const { seen, Inside } = page();
    renderScenario(
      <RequireSession>
        <Inside />
      </RequireSession>,
    );

    expect(await screen.findByText('adentro')).toBeInTheDocument();
    expect(new Set(seen)).toEqual(new Set([WORKSPACE_IDS.estudio]));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/proyectos/abc'));
  });

  it('con un workspace que no es de la persona, se queda donde estaba', async () => {
    open('/proyectos/abc?espacio=0c8f6b1e-4d3a-4b52-9a7e-2f1d5e6c7a80');
    const { seen, Inside } = page();
    renderScenario(
      <RequireSession>
        <Inside />
      </RequireSession>,
    );

    expect(await screen.findByText('adentro')).toBeInTheDocument();
    expect(new Set(seen)).toEqual(new Set([WORKSPACE_IDS.estudio]));
    expect(getActiveWorkspaceId()).toBe(WORKSPACE_IDS.estudio);
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it('sin sesión, el login vuelve al link entero, con su workspace', async () => {
    open(`/proyectos/abc/revision?espacio=${WORKSPACE_IDS.canal}`);
    renderScenario(
      <RequireSession>
        <p>adentro</p>
      </RequireSession>,
      { prepare: () => clearSession() },
    );

    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith(`/login?siguiente=${encodeURIComponent(`/proyectos/abc/revision?espacio=${WORKSPACE_IDS.canal}`)}`),
    );
    expect(screen.queryByText('adentro')).not.toBeInTheDocument();
  });
});
