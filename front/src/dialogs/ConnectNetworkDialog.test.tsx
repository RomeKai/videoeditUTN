import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { completeMockSocialConnect } from '@/api/mocks/control';
import { USER_IDS, WORKSPACE_IDS } from '@/api/mocks/seed';
import { keys } from '@/api/query';
import { server } from '@/api/mocks/server';
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

  it('sin `allow_social_accounts` en el plan (backend anterior) no hay candado: decide el backend', async () => {
    const { queryClient } = renderScenario(<ConnectNetworkDialog platform="YOUTUBE_SHORTS" onClose={vi.fn()} />, {
      scenario: 'plan-gratis',
      prepare: () =>
        editDb((db) => {
          for (const plan of db.plans) delete plan.allow_social_accounts;
        }),
    });
    // Once the plan (Gratis, without the flag) is read, the dialog still offers to continue.
    await waitFor(() => expect(queryClient.getQueriesData({ queryKey: keys.billing.subscription }).some(([, data]) => data !== undefined)).toBe(true));
    expect(await screen.findByRole('button', { name: 'Continuar en YouTube' })).toBeInTheDocument();
    expect(screen.queryByText(/Tu plan no incluye conectar redes/)).not.toBeInTheDocument();
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

  it('sin el email verificado ofrece reenviar la verificación, con la espera de 60 s, y después deja conectar', async () => {
    fakePopup();
    const user = userEvent.setup();
    const unverify = (unverified: boolean) =>
      editDb((db) => {
        const lucia = db.users.find((item) => item.id === USER_IDS.lucia);
        if (lucia) lucia.is_email_verified = !unverified;
      });
    renderScenario(<ConnectNetworkDialog platform="YOUTUBE_SHORTS" onClose={vi.fn()} />, { prepare: () => unverify(true) });
    const dialog = await screen.findByRole('dialog', { name: 'Conectar YouTube' });
    // Nothing to resend until the backend says the email is the problem.
    expect(within(dialog).queryByRole('button', { name: /Reenviar/ })).not.toBeInTheDocument();
    await user.click(await within(dialog).findByRole('button', { name: 'Continuar en YouTube' }));
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('Verificá tu email para conectar redes.');

    const asked: unknown[] = [];
    server.events.on('request:start', ({ request }) => {
      if (request.url.endsWith('/api/v1/auth/verify-email/resend/')) void request.clone().json().then((body) => asked.push(body));
    });
    await user.click(within(dialog).getByRole('button', { name: 'Reenviar el email de verificación' }));
    // The email goes to the address of the account, and the button waits like «Reenviar» in A3.
    expect(await within(dialog).findByText(/Te mandamos el link a/)).toHaveTextContent('lucia@estudionorte.com');
    expect(asked).toEqual([{ email: 'lucia@estudionorte.com' }]);
    expect(within(dialog).getByRole('button', { name: /Reenviar en 1:00|Reenviar en 0:59/ })).toBeDisabled();
    server.events.removeAllListeners();

    // The link was opened (in another tab): the same dialog connects.
    unverify(false);
    await user.click(within(dialog).getByRole('button', { name: 'Continuar en YouTube' }));
    expect(await within(dialog).findByRole('button', { name: 'Abrir YouTube de nuevo' })).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /Reenviar/ })).not.toBeInTheDocument();
  });

  it('si ya se pidió hace menos de 60 s (en A3), el botón muestra lo que falta', async () => {
    const user = userEvent.setup();
    renderScenario(<ConnectNetworkDialog platform="YOUTUBE_SHORTS" onClose={vi.fn()} />, {
      prepare: () =>
        editDb((db) => {
          const lucia = db.users.find((item) => item.id === USER_IDS.lucia);
          if (lucia) lucia.is_email_verified = false;
          // Asked 20 s ago from another screen.
          db.resendAt['lucia@estudionorte.com'] = Date.now() - 20_000;
        }),
    });
    await user.click(await screen.findByRole('button', { name: 'Continuar en YouTube' }));
    await user.click(await screen.findByRole('button', { name: 'Reenviar el email de verificación' }));
    expect(await screen.findByRole('button', { name: /Reenviar en 0:(40|39|38)/ })).toBeDisabled();
    // A wait is not a failure: only the refusal to connect is on screen as an error.
    expect(screen.getAllByRole('alert')).toHaveLength(1);
  });
});
