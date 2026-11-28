import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http } from 'msw';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { API_URL } from '@/api/config';
import { server } from '@/api/mocks/server';
import { AUTH_STORAGE_KEY, clearSession, getSessionEmail, getTokens, isSessionExpired, noteRefreshRejected, notifySessionExpired, rememberSessionEmail, SESSION_EMAIL_STORAGE_KEY, setTokens, tokenUser, WORKSPACE_STORAGE_KEY } from '@/api/session';
import { mockTokens } from '@/api/mocks/state';
import { keys } from '@/api/query';
import { USER_IDS } from '@/api/mocks/seed';
import { LoginScreen, SESSION_ENDED } from '@/features/acceso/LoginScreen';
import { RequireSession } from '@/shell/RequireSession';
import { editDb, renderScenario, startMockApi } from '@/testing';
import { SessionExpiredDialog } from './SessionExpiredDialog';

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/proyectos',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

startMockApi();

// New tokens end the "expired" state the test left in the session module.
afterEach(() => {
  setTokens(mockTokens(USER_IDS.lucia));
  rememberSessionEmail(null);
});

/** D9 as the app has it: next to a screen that needs the session. */
const APP = (
  <>
    <RequireSession>
      <p>adentro</p>
    </RequireSession>
    <SessionExpiredDialog />
  </>
);

describe('D9 · Sesión vencida', () => {
  it('cuando el token no se pudo renovar dice que la sesión venció', async () => {
    renderScenario(APP);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    act(() => notifySessionExpired());
    const dialog = await screen.findByRole('dialog', { name: 'Tu sesión venció' });
    expect(within(dialog).getByText('Ingresá tu contraseña para seguir. Esta pantalla queda como la dejaste.')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('Contraseña')).toBeInTheDocument();
  });

  it('después de «cerrar sesión en todos los dispositivos» dice eso, no que venció', async () => {
    renderScenario(APP);
    act(() => notifySessionExpired('revoked'));
    const dialog = await screen.findByRole('dialog', { name: 'Cerraste la sesión en todos tus dispositivos' });
    expect(within(dialog).getByText('Iniciá sesión de nuevo con tu contraseña para seguir. Esta pantalla queda como la dejaste.')).toBeInTheDocument();
    expect(screen.queryByText('Tu sesión venció')).not.toBeInTheDocument();
    // The way back in is the same: the password, on the same screen.
    expect(within(dialog).getByLabelText('Contraseña')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Continuar' })).toBeInTheDocument();
  });

  it('con el perfil cargado muestra de quién es la sesión, sin pedir el email', async () => {
    renderScenario(APP);
    expect(await screen.findByText('adentro')).toBeInTheDocument();
    // The profile says whose session this browser holds.
    await waitFor(() => expect(getSessionEmail()).toBe('lucia@estudionorte.com'));
    act(() => notifySessionExpired());
    const dialog = await screen.findByRole('dialog', { name: 'Tu sesión venció' });
    expect(within(dialog).getByText('lucia@estudionorte.com')).toBeInTheDocument();
    expect(within(dialog).queryByLabelText('Email')).not.toBeInTheDocument();
  });
});

describe('D9 y las otras pestañas', () => {
  it('si la sesión desaparece del navegador (otra pestaña la cerró), el diálogo se cierra: no queda arriba del login', async () => {
    renderScenario(APP);
    expect(await screen.findByText('adentro')).toBeInTheDocument();
    act(() => notifySessionExpired());
    expect(await screen.findByRole('dialog', { name: 'Tu sesión venció' })).toBeInTheDocument();
    act(() => clearSession());
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('si otra pestaña inicia sesión (guarda un par nuevo), el diálogo se cierra y la pantalla sigue, con lo que había fallado leído de nuevo', async () => {
    let profile = 0;
    server.use(
      http.get(`${API_URL}/api/v1/me/`, () => {
        profile += 1;
        return undefined;
      }),
    );
    renderScenario(APP);
    expect(await screen.findByText('adentro')).toBeInTheDocument();
    act(() => notifySessionExpired());
    const dialog = await screen.findByRole('dialog', { name: 'Tu sesión venció' });
    await userEvent.setup().type(within(dialog).getByLabelText('Contraseña'), 'a medio escribir');
    const asked = profile;

    // The other tab signs in from the login: it writes the storage, without going through this tab's `setTokens`.
    const pair = mockTokens(USER_IDS.lucia);
    act(() => {
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ access: `${pair.access}-otra`, refresh: `${pair.refresh}-otra` }));
      window.dispatchEvent(new StorageEvent('storage', { key: AUTH_STORAGE_KEY }));
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    // Same screen, and the session was read again with the new pair.
    expect(screen.getByText('adentro')).toBeInTheDocument();
    await waitFor(() => expect(profile).toBeGreaterThan(asked));
    expect(isSessionExpired()).toBe(false);
  });

  it('no se adelanta al aviso de la otra pestaña: con el par nuevo ya guardado pero la sesión todavía marcada vencida, escribir no lo cierra', async () => {
    const user = userEvent.setup();
    renderScenario(APP);
    expect(await screen.findByText('adentro')).toBeInTheDocument();
    act(() => notifySessionExpired());
    const dialog = await screen.findByRole('dialog', { name: 'Tu sesión venció' });
    // The other tab already wrote its pair; this tab has not been told yet (the `storage` event is on its way).
    const pair = mockTokens(USER_IDS.lucia);
    window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ access: `${pair.access}-otra`, refresh: `${pair.refresh}-otra` }));
    // A keystroke renders the dialog again. Closing now would read everything again with the session still
    // counted as expired: every request fails at once and stays failed.
    await user.type(within(dialog).getByLabelText('Contraseña'), 'abc');
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(isSessionExpired()).toBe(true);
    expect(screen.getByRole('dialog', { name: 'Tu sesión venció' })).toBeInTheDocument();
    // The event arrives: now it closes.
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: AUTH_STORAGE_KEY }));
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(isSessionExpired()).toBe(false);
  });

  it('si en la otra pestaña entra OTRA cuenta, el diálogo se queda: la pantalla de abajo es de esta', async () => {
    let profile = 0;
    server.use(
      http.get(`${API_URL}/api/v1/me/`, () => {
        profile += 1;
        return undefined;
      }),
    );
    renderScenario(APP);
    expect(await screen.findByText('adentro')).toBeInTheDocument();
    act(() => notifySessionExpired());
    const dialog = await screen.findByRole('dialog', { name: 'Tu sesión venció' });
    const asked = profile;
    act(() => {
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(mockTokens(USER_IDS.martin)));
      window.dispatchEvent(new StorageEvent('storage', { key: AUTH_STORAGE_KEY }));
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(screen.getByRole('dialog', { name: 'Tu sesión venció' })).toBeInTheDocument();
    // Still asking for this account's password, and nothing was read again as the other one.
    expect(within(dialog).getByText('lucia@estudionorte.com')).toBeInTheDocument();
    expect(profile).toBe(asked);
  });

  it('con otra cuenta en la otra pestaña sigue abierto aunque el perfil de abajo se lea de nuevo como esa cuenta y llegue otro aviso', async () => {
    const view = renderScenario(APP);
    expect(await screen.findByText('adentro')).toBeInTheDocument();
    act(() => notifySessionExpired());
    expect(await screen.findByRole('dialog', { name: 'Tu sesión venció' })).toBeInTheDocument();
    // The other tab signs in as someone else…
    act(() => {
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(mockTokens(USER_IDS.martin)));
      window.dispatchEvent(new StorageEvent('storage', { key: AUTH_STORAGE_KEY }));
    });
    // …the profile underneath is read again with that token (the window got the focus, it was old)…
    await act(() => view.queryClient.refetchQueries({ queryKey: keys.me }));
    await waitFor(() => expect(view.queryClient.getQueryData<{ id: string }>(keys.me)?.id).toBe(USER_IDS.martin));
    // …and the session module speaks again (the other tab changed workspace, or renewed its pair).
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: WORKSPACE_STORAGE_KEY }));
    });
    await new Promise((resolve) => setTimeout(resolve, 100));
    // The dialog opened over Lucía's screen: Martín's session does not close it.
    expect(screen.getByRole('dialog', { name: 'Tu sesión venció' })).toBeInTheDocument();
  });

  it('de quién es un token: el `user_id` de un JWT, o su parte del medio cuando no es un JWT', () => {
    const jwt = (claims: object) => `x.${btoa(JSON.stringify(claims)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')}.y`;
    expect(tokenUser(jwt({ user_id: 'u-1', exp: 1 }))).toBe('u-1');
    expect(tokenUser(jwt({ user_id: 7 }))).toBe('7');
    expect(tokenUser(jwt({ user_id: 'u-1', jti: 'otro' }))).toBe(tokenUser(jwt({ user_id: 'u-1' })));
    expect(tokenUser(mockTokens(USER_IDS.lucia).access)).toBe(USER_IDS.lucia);
    expect(tokenUser(mockTokens(USER_IDS.lucia).refresh)).not.toBe(tokenUser(mockTokens(USER_IDS.martin).refresh));
    for (const nobody of [null, undefined, '', 'a', 'a.b']) expect(tokenUser(nobody)).toBeNull();
  });

  it('un evento de otra pestaña que deja el mismo par muerto no lo cierra', async () => {
    renderScenario(APP);
    expect(await screen.findByText('adentro')).toBeInTheDocument();
    act(() => notifySessionExpired());
    expect(await screen.findByRole('dialog', { name: 'Tu sesión venció' })).toBeInTheDocument();
    act(() => {
      window.dispatchEvent(new StorageEvent('storage', { key: AUTH_STORAGE_KEY }));
    });
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.getByRole('dialog', { name: 'Tu sesión venció' })).toBeInTheDocument();
  });
});

/** A session stored long ago: the tokens are still in the browser, and the server no longer takes them. */
const deadSession = () => editDb((db) => (db.flags.sessionExpired = true));

describe('una sesión guardada que ya no sirve', () => {
  it('en una pantalla de la app pide la contraseña con el email de esa cuenta ya escrito', async () => {
    renderScenario(APP, {
        prepare: () => {
          deadSession();
          rememberSessionEmail('lucia@estudionorte.com');
        },
    });
    const dialog = await screen.findByRole('dialog', { name: 'Tu sesión venció' });
    expect(within(dialog).getByLabelText('Email')).toHaveValue('lucia@estudionorte.com');
    // The session is still there to be renewed with the password.
    expect(getTokens()).not.toBeNull();
    expect(screen.queryByText('adentro')).not.toBeInTheDocument();
  });

  it('en Iniciar sesión no abre el diálogo: avisa arriba del formulario, con el email ya escrito', async () => {
    renderScenario(
      <>
        <LoginScreen />
        <SessionExpiredDialog />
      </>,
      {
        prepare: () => {
          deadSession();
          rememberSessionEmail('lucia@estudionorte.com');
        },
      },
    );
    expect(await screen.findByText(SESSION_ENDED.expired)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toHaveValue('lucia@estudionorte.com');
    // This tab has no session any more…
    expect(getTokens()).toBeNull();
    // …but the browser's storage is left alone: another tab may be asking for the password over a screen it keeps.
    expect(window.localStorage.getItem(AUTH_STORAGE_KEY)).not.toBeNull();
    expect(window.localStorage.getItem(SESSION_EMAIL_STORAGE_KEY)).toBe('lucia@estudionorte.com');
  });

  it('si otra pestaña ya renovó el par, Iniciar sesión no lo da por muerto: lee de nuevo con ese', async () => {
    renderScenario(
      <>
        <LoginScreen />
        <SessionExpiredDialog />
      </>,
    );
    // The server refused the pair this tab had; meanwhile another tab stored a new one.
    noteRefreshRejected('el-refresh-que-fallo');
    act(() => notifySessionExpired());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(getTokens()).not.toBeNull();
    expect(isSessionExpired()).toBe(false);
    expect(screen.queryByText(SESSION_ENDED.expired)).not.toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('otra pestaña que guarda un par nuevo le devuelve la sesión a la que la había dado por muerta', async () => {
    renderScenario(
      <>
        <LoginScreen />
        <SessionExpiredDialog />
      </>,
      { prepare: deadSession },
    );
    expect(await screen.findByText(SESSION_ENDED.expired)).toBeInTheDocument();
    expect(getTokens()).toBeNull();
    // Another tab signs in: it writes the storage, without going through this tab's `setTokens`.
    editDb((db) => (db.flags.sessionExpired = false));
    const pair = mockTokens(USER_IDS.lucia);
    act(() => {
      window.localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify({ ...pair, refresh: `${pair.refresh}-otra` }));
      window.dispatchEvent(new StorageEvent('storage', { key: AUTH_STORAGE_KEY }));
    });
    expect(getTokens()).not.toBeNull();
    await waitFor(() => expect(screen.queryByText(SESSION_ENDED.expired)).not.toBeInTheDocument());
  });

  it('sin saber de quién era, avisa igual y deja el email para escribir', async () => {
    renderScenario(
      <>
        <LoginScreen />
        <SessionExpiredDialog />
      </>,
      { prepare: deadSession },
    );
    expect(await screen.findByText(SESSION_ENDED.expired)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toHaveValue('');
  });

  it('no pisa el email que la persona ya estaba escribiendo ni el que trae el link', async () => {
    renderScenario(
      <>
        <LoginScreen email="otra@ejemplo.com" />
        <SessionExpiredDialog />
      </>,
      {
        prepare: () => {
          deadSession();
          rememberSessionEmail('lucia@estudionorte.com');
        },
      },
    );
    expect(await screen.findByText(SESSION_ENDED.expired)).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toHaveValue('otra@ejemplo.com');
  });

  it('al entrar de nuevo el aviso se va', async () => {
    const user = userEvent.setup();
    renderScenario(
      <>
        <LoginScreen />
        <SessionExpiredDialog />
      </>,
      {
        prepare: () => {
          deadSession();
          rememberSessionEmail('lucia@estudionorte.com');
        },
      },
    );
    expect(await screen.findByText(SESSION_ENDED.expired)).toBeInTheDocument();
    editDb((db) => (db.flags.sessionExpired = false));
    await user.type(screen.getByLabelText('Contraseña'), 'contrasena');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(getTokens()).not.toBeNull());
    await waitFor(() => expect(screen.queryByText(SESSION_ENDED.expired)).not.toBeInTheDocument());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('una primera visita, sin sesión guardada', () => {
  it('Iniciar sesión no dice nada de sesiones vencidas y el email queda vacío', async () => {
    renderScenario(
      <>
        <LoginScreen />
        <SessionExpiredDialog />
      </>,
      { prepare: () => setTokens(null) },
    );
    expect(await screen.findByRole('button', { name: 'Iniciar sesión' })).toBeInTheDocument();
    // Long enough for a session check to have answered, if there were one.
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText(SESSION_ENDED.expired)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toHaveValue('');
  });
});
