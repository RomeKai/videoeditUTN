import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { completeMockSocialConnect } from '@/api/mocks/control';
import { USER_IDS, WORKSPACE_IDS } from '@/api/mocks/seed';
import { keys } from '@/api/query';
import { allowSlowScreens, editDb, renderScenario, startMockApi } from '@/testing';
import { ConnectNetworkDialog } from './ConnectNetworkDialog';

startMockApi();
allowSlowScreens();

/** The network's window, as `window.open` gives it back. */
function fakePopup() {
  const popup = { closed: false, close: vi.fn(() => (popup.closed = true)) };
  vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  return popup;
}

afterEach(() => vi.restoreAllMocks());

describe('D6 · Conectar red social', () => {
  it('«Cancelar» también cierra la ventana de la red', async () => {
    const popup = fakePopup();
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderScenario(<ConnectNetworkDialog platform="YOUTUBE_SHORTS" onClose={onClose} />);
    await user.click(await screen.findByRole('button', { name: 'Continuar en YouTube' }));
    expect(await screen.findByRole('button', { name: 'Abrir YouTube de nuevo' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cancelar' }));
    expect(popup.close).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalled();
  });

  it('una vez conectada deja de preguntar por las cuentas', async () => {
    fakePopup();
    const user = userEvent.setup();
    const { queryClient } = renderScenario(<ConnectNetworkDialog platform="YOUTUBE_SHORTS" onClose={vi.fn()} />);
    await user.click(await screen.findByRole('button', { name: 'Continuar en YouTube' }));
    await screen.findByRole('button', { name: 'Abrir YouTube de nuevo' });
    completeMockSocialConnect('YOUTUBE_SHORTS', WORKSPACE_IDS.estudio);
    // Back on the tab: the accounts are read again and the account shows up.
    window.dispatchEvent(new Event('focus'));
    expect(await screen.findByRole('dialog', { name: 'YouTube quedó conectada' })).toBeInTheDocument();
    const asked = vi.spyOn(queryClient, 'invalidateQueries');
    window.dispatchEvent(new Event('focus'));
    await new Promise((resolve) => setTimeout(resolve, 1200));
    expect(asked.mock.calls.filter(([filters]) => JSON.stringify(filters?.queryKey) === JSON.stringify(keys.social.accounts))).toHaveLength(0);
  });

  it('en el plan Gratis muestra el candado con el plan que lo incluye, sin abrir la red', async () => {
    const open = vi.spyOn(window, 'open');
    renderScenario(<ConnectNetworkDialog platform="YOUTUBE_SHORTS" onClose={vi.fn()} />, { scenario: 'plan-gratis' });
    const dialog = await screen.findByRole('dialog', { name: 'Conectar YouTube' });
    expect(await within(dialog).findByText(/Disponible en el plan Creador/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Tu plan no incluye conectar redes/)).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /Continuar en/ })).not.toBeInTheDocument();
    expect(open).not.toHaveBeenCalled();
  });

  it('sin el email verificado muestra el mensaje del backend', async () => {
    const open = vi.spyOn(window, 'open');
    const user = userEvent.setup();
    renderScenario(<ConnectNetworkDialog platform="YOUTUBE_SHORTS" onClose={vi.fn()} />, {
      prepare: () =>
        editDb((db) => {
          const lucia = db.users.find((item) => item.id === USER_IDS.lucia);
          if (lucia) lucia.is_email_verified = false;
        }),
    });
    await user.click(await screen.findByRole('button', { name: 'Continuar en YouTube' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Verificá tu email para conectar redes.');
    await waitFor(() => expect(open).not.toHaveBeenCalled());
  });
});
