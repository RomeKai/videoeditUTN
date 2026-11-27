import { act, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { notifySessionExpired, setTokens } from '@/api/session';
import { mockTokens } from '@/api/mocks/state';
import { USER_IDS } from '@/api/mocks/seed';
import { renderScenario, startMockApi } from '@/testing';
import { SessionExpiredDialog } from './SessionExpiredDialog';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/proyectos',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

startMockApi();

// New tokens end the "expired" state the test left in the session module.
afterEach(() => setTokens(mockTokens(USER_IDS.lucia)));

describe('D9 · Sesión vencida', () => {
  it('cuando el token no se pudo renovar dice que la sesión venció', async () => {
    renderScenario(<SessionExpiredDialog />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    act(() => notifySessionExpired());
    const dialog = await screen.findByRole('dialog', { name: 'Tu sesión venció' });
    expect(within(dialog).getByText('Ingresá tu contraseña para seguir. Esta pantalla queda como la dejaste.')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Contraseña')).toBeInTheDocument();
  });

  it('después de «cerrar sesión en todos los dispositivos» dice eso, no que venció', async () => {
    renderScenario(<SessionExpiredDialog />);
    act(() => notifySessionExpired('revoked'));
    const dialog = await screen.findByRole('dialog', { name: 'Cerraste la sesión en todos tus dispositivos' });
    expect(within(dialog).getByText('Iniciá sesión de nuevo con tu contraseña para seguir. Esta pantalla queda como la dejaste.')).toBeInTheDocument();
    expect(screen.queryByText('Tu sesión venció')).not.toBeInTheDocument();
    // The way back in is the same: the password, on the same screen.
    expect(within(dialog).getByLabelText('Contraseña')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Continuar' })).toBeInTheDocument();
  });
});
