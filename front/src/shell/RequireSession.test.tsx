import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { getActiveWorkspaceId } from '@/api/session';
import { allowSlowScreens, db, editDb, renderScenario, startMockApi } from '@/testing';
import { RequireSession } from './RequireSession';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/ajustes',
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
