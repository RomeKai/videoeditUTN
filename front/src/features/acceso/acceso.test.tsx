import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { delay, http, HttpResponse } from 'msw';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { createQueryClient } from '@/api/query';
import { getActiveWorkspaceId, getTokens, setTokens } from '@/api/session';
import { server } from '@/api/mocks/server';
import { applyScenario, loadDb } from '@/api/mocks/state';
import { needsWelcome, SessionProvider } from '@/shell/session';
import { ToastProvider } from '@/ui';
import { InvitationScreen, joinOptions } from './InvitationScreen';
import { formatCountdown, NEXT_MAX_AGE_MS, rememberNext, retryAfterSeconds, safeNext, takeNext } from './lib';
import { LoginScreen } from './LoginScreen';
import { RecoverScreen } from './RecoverScreen';
import { RegisterScreen } from './RegisterScreen';
import { VerifyEmailScreen } from './VerifyEmailScreen';
import { WelcomeScreen } from './WelcomeScreen';
import { ApiError } from '@/api/errors';
import real from '@/api/contracts/drf/responses.json';

const nav = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => nav,
  usePathname: () => '/login',
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
beforeEach(() => {
  nav.push.mockClear();
  nav.replace.mockClear();
});

/** Seeds a scenario. `signedIn: false` drops the session the scenario starts with. */
function scenario(id: string, { signedIn = true }: { signedIn?: boolean } = {}) {
  applyScenario(id);
  if (!signedIn) setTokens(null);
}

function show(ui: ReactElement) {
  const client = createQueryClient();
  client.setDefaultOptions({ queries: { retry: false }, mutations: { retry: false } });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <SessionProvider>{ui}</SessionProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

const password = () => screen.getByLabelText('Contraseña');

describe('lib', () => {
  it('safeNext solo acepta rutas de la app', () => {
    expect(safeNext('/uso/movimientos')).toBe('/uso/movimientos');
    expect(safeNext('/invitacion/valida')).toBe('/invitacion/valida');
    expect(safeNext('https://otro.sitio')).toBeNull();
    expect(safeNext('//otro.sitio')).toBeNull();
    expect(safeNext('/\\otro.sitio')).toBeNull();
    expect(safeNext('/login?siguiente=/login')).toBeNull();
    expect(safeNext(null)).toBeNull();
    expect(safeNext('/proyectos/abc?publicar=1#clips')).toBe('/proyectos/abc?publicar=1#clips');
    // Only the login goes back to a verification link, and only one that carries its token.
    expect(safeNext('/verificar?token=abc&siguiente=%2Fperfil')).toBeNull();
    expect(safeNext('/verificar?token=abc&siguiente=%2Fperfil', { verifyLink: true })).toBe('/verificar?token=abc&siguiente=%2Fperfil');
    expect(safeNext('/verificar?email=a%40b.com', { verifyLink: true })).toBeNull();
    expect(safeNext('/recuperar?token=abc', { verifyLink: true })).toBeNull();
  });

  it('safeNext rechaza lo que el navegador convertiría en otro sitio', () => {
    // The URL parser drops tabs and line breaks: "/<TAB>/otro.sitio" is "//otro.sitio" for a browser.
    for (const value of ['/\t/otro.sitio', '/\n/otro.sitio', '/\r/otro.sitio', '/\r\n/otro.sitio', '/ /otro.sitio', '/\u0000/otro.sitio', '/\u007f/otro.sitio', '/proyectos\t']) {
      expect(safeNext(value), JSON.stringify(value)).toBeNull();
    }
    // Backslashes count as slashes, and dot segments are removed.
    for (const value of ['/\\otro.sitio', '\\\\otro.sitio', '/.//otro.sitio', '/./\\otro.sitio', '/a/..//otro.sitio', '/%2e//otro.sitio']) {
      expect(safeNext(value), JSON.stringify(value)).toBeNull();
    }
    for (const value of ['javascript:alert(1)', 'http://otro.sitio', 'otro.sitio', '', '/login', '/registro?x=1', '/a/../login']) {
      expect(safeNext(value), JSON.stringify(value)).toBeNull();
    }
    // Whatever is accepted resolves inside the app.
    for (const value of ['/uso/movimientos', '/a/../proyectos', '/proyectos?x=//otro.sitio', '/invitacion/valida']) {
      const result = safeNext(value);
      expect(result, value).not.toBeNull();
      expect(new URL(result as string, 'https://app.onecreator.test').origin).toBe('https://app.onecreator.test');
    }
  });

  it('la página recordada es de una sola cuenta, vence y se olvida al leerla', () => {
    const KEY = 'oc.acceso.siguiente';
    rememberNext('/invitacion/con-cuenta', ' Sofia@Ejemplo.com ');
    expect(takeNext('sofia@ejemplo.com')).toBe('/invitacion/con-cuenta');
    // Read once.
    expect(window.localStorage.getItem(KEY)).toBeNull();
    expect(takeNext('sofia@ejemplo.com')).toBeNull();

    // Someone else verifying in the same browser does not inherit it, and it is dropped anyway.
    rememberNext('/invitacion/con-cuenta', 'sofia@ejemplo.com');
    expect(takeNext('ana@ejemplo.com')).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();

    // Without knowing who verified, it is not used either.
    rememberNext('/invitacion/con-cuenta', 'sofia@ejemplo.com');
    expect(takeNext(null)).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();

    // Older than a day.
    window.localStorage.setItem(KEY, JSON.stringify({ path: '/uso', email: 'sofia@ejemplo.com', at: Date.now() - NEXT_MAX_AGE_MS - 1000 }));
    expect(takeNext('sofia@ejemplo.com')).toBeNull();
    expect(window.localStorage.getItem(KEY)).toBeNull();

    // Opening the wait without a destination, or without an owner, clears what was there.
    rememberNext('/uso', 'sofia@ejemplo.com');
    rememberNext(null, 'ana@ejemplo.com');
    expect(window.localStorage.getItem(KEY)).toBeNull();
    rememberNext('/uso', 'sofia@ejemplo.com');
    rememberNext('/uso', null);
    expect(window.localStorage.getItem(KEY)).toBeNull();

    // The format of the first version (a bare path), a tampered path or garbage are dropped.
    for (const raw of ['/uso', JSON.stringify({ path: '//otro.sitio', email: 'sofia@ejemplo.com', at: Date.now() }), '{', 'null', JSON.stringify({ path: '/uso' })]) {
      window.localStorage.setItem(KEY, raw);
      expect(takeNext('sofia@ejemplo.com'), raw).toBeNull();
      expect(window.localStorage.getItem(KEY)).toBeNull();
    }
  });

  it('lee la espera de un 429 y arma la cuenta regresiva', () => {
    expect(retryAfterSeconds(new ApiError(429, 'x', { detail: 'Podés pedirlo de nuevo en 42 segundos.' }))).toBe(42);
    expect(retryAfterSeconds(new ApiError(429, 'x', { detail: 'Request was throttled. Expected available in 7 seconds.' }))).toBe(7);
    // The backend's own 429 (apps/users/throttles.py): seconds under two minutes, minutes above.
    expect(retryAfterSeconds(new ApiError(429, 'x', { detail: 'Demasiados intentos. Probá de nuevo en 42 segundos.' }))).toBe(42);
    expect(retryAfterSeconds(new ApiError(429, 'x', { detail: 'Demasiados intentos. Probá de nuevo en 12 minutos.' }))).toBe(720);
    expect(retryAfterSeconds(new ApiError(429, 'x', { detail: 'Throttled' }))).toBe(60);
    expect(formatCountdown(42)).toBe('0:42');
    expect(formatCountdown(60)).toBe('1:00');
  });
});

describe('A1 · Iniciar sesión', () => {
  it('valida los campos antes de pedir nada', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<LoginScreen />);
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('Escribí tu email.');
    expect(password()).toHaveAccessibleDescription('Escribí tu contraseña.');
    await user.type(screen.getByLabelText('Email'), 'lucia@');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('Revisá el email: no parece completo.');
    expect(getTokens()).toBeNull();
  });

  it('con credenciales incorrectas no dice cuál de los dos falló', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<LoginScreen />);
    await user.type(screen.getByLabelText('Email'), 'lucia@estudionorte.com');
    await user.type(password(), 'incorrecta');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('El email o la contraseña no coinciden.');
    // What was typed stays.
    expect(screen.getByLabelText('Email')).toHaveValue('lucia@estudionorte.com');
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('un email que no existe responde lo mismo que una contraseña incorrecta', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<LoginScreen />);
    await user.type(screen.getByLabelText('Email'), 'nadie@ejemplo.com');
    await user.type(password(), 'cualquiera');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('El email o la contraseña no coinciden.');
  });

  it('al entrar va a Proyectos', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<LoginScreen />);
    await user.type(screen.getByLabelText('Email'), 'lucia@estudionorte.com');
    await user.type(password(), 'contrasena');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
  });

  it('al entrar vuelve a la página que se había pedido, si es de la app', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<LoginScreen next="/uso/movimientos" email="lucia@estudionorte.com" />);
    expect(screen.getByLabelText('Email')).toHaveValue('lucia@estudionorte.com');
    await user.type(password(), 'contrasena');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/uso/movimientos'));
  });

  it('al entrar vuelve al link de verificación que pedía la sesión', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    const link = '/verificar?token=cambio-de-email&siguiente=%2Fperfil';
    show(<LoginScreen next={link} email="lucia@estudionorte.com" />);
    await user.type(password(), 'contrasena');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith(link));
  });

  it('ignora un «siguiente» que apunta fuera de la app', async () => {
    scenario('estudio-norte');
    show(<LoginScreen next="https://otro.sitio/robo" />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
  });

  it('con sesión, un «siguiente» con un tabulador no saca de la app', async () => {
    scenario('estudio-norte');
    show(<LoginScreen next={'/\t/otro.sitio'} />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
    expect(nav.replace).toHaveBeenCalledTimes(1);
  });

  it('si la contraseña está bien pero el perfil no carga, lo dice y deja reintentar', async () => {
    scenario('sin-sesion');
    // Today's backend has no /me yet: the token arrives and the profile fails.
    server.use(http.get('http://localhost:8000/api/v1/me/', () => HttpResponse.json({ detail: 'Not found.' }, { status: 404 })));
    const user = userEvent.setup();
    show(<LoginScreen />);
    await user.type(screen.getByLabelText('Email'), 'lucia@estudionorte.com');
    await user.type(password(), 'contrasena');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Entraste, pero no pudimos cargar tu cuenta');
    expect(alert).toHaveTextContent('Algo falló de nuestro lado. Probá de nuevo en un momento.');
    // The form is usable again: no endless spinner.
    expect(screen.getByRole('button', { name: 'Iniciar sesión' })).not.toHaveAttribute('aria-busy');
    expect(nav.replace).not.toHaveBeenCalled();

    server.resetHandlers();
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
  });

  it('llegar con una sesión cuyo perfil no carga también muestra el error', async () => {
    scenario('estudio-norte');
    server.use(http.get('http://localhost:8000/api/v1/me/', () => HttpResponse.json({ detail: 'boom' }, { status: 500 })));
    show(<LoginScreen />);
    expect(await screen.findByRole('alert', {}, { timeout: 5000 })).toHaveTextContent('Entraste, pero no pudimos cargar tu cuenta');
    expect(screen.getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
  });

  it('una cuenta nueva entra por la Bienvenida', async () => {
    scenario('usuario-nuevo', { signedIn: false });
    const user = userEvent.setup();
    show(<LoginScreen />);
    await user.type(screen.getByLabelText('Email'), 'sofia@ejemplo.com');
    await user.type(password(), 'contrasena');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/bienvenida'));
  });

  it('con el email sin verificar lleva a Verificar email', async () => {
    scenario('email-sin-verificar');
    const user = userEvent.setup();
    show(<LoginScreen />);
    await user.type(screen.getByLabelText('Email'), 'lucia@estudionorte.com');
    await user.type(password(), 'contrasena');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/verificar?email=lucia%40estudionorte.com&motivo=login'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('con el email sin verificar conserva la página que se había pedido', async () => {
    scenario('email-sin-verificar');
    const user = userEvent.setup();
    show(<LoginScreen next="/invitacion/con-cuenta" email="sofia@ejemplo.com" />);
    await user.type(password(), 'contrasena');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/verificar?email=sofia%40ejemplo.com&motivo=login&siguiente=%2Finvitacion%2Fcon-cuenta'));
  });

  it('explica el límite de intentos y el error del servidor', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<LoginScreen />);
    await user.type(screen.getByLabelText('Email'), 'lucia@estudionorte.com');
    await user.type(password(), 'limite');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Probaste demasiadas veces. Esperá 1 minuto y volvé a intentar.');
    await user.clear(password());
    await user.type(password(), 'caida');
    await user.click(screen.getByRole('button', { name: 'Iniciar sesión' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Algo falló de nuestro lado. Probá de nuevo en un momento.'));
  });
});

describe('A2 · Crear cuenta', () => {
  const fill = async (user: ReturnType<typeof userEvent.setup>, email: string, pass = 'contrasena-segura') => {
    await user.type(screen.getByLabelText('Nombre'), 'Ana Gómez');
    await user.type(screen.getByLabelText('Email'), email);
    await user.type(password(), pass);
  };

  it('pide aceptar los términos', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<RegisterScreen />);
    await fill(user, 'ana@ejemplo.com');
    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }));
    const terms = screen.getByRole('checkbox', { name: /Acepto los Términos/ });
    expect(terms).toBeInvalid();
    expect(terms).toHaveAccessibleDescription('Para crear la cuenta tenés que aceptar los términos.');
    await user.click(terms);
    expect(terms).not.toBeInvalid();
    expect(nav.push).not.toHaveBeenCalled();
  });

  it('valida nombre, email y el largo de la contraseña', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<RegisterScreen />);
    await user.type(password(), 'corta');
    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }));
    expect(screen.getByLabelText('Nombre')).toHaveAccessibleDescription('Escribí tu nombre.');
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('Escribí tu email.');
    expect(password()).toHaveAccessibleDescription('Usá 8 caracteres o más.');
  });

  it('muestra debajo de cada campo lo que rechaza el servidor', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<RegisterScreen />);
    await fill(user, 'lucia@estudionorte.com');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }));
    await waitFor(() => expect(screen.getByLabelText('Email')).toHaveAccessibleDescription(/Ya existe una cuenta con este email\./));
    expect(within(screen.getByRole('alert')).getByRole('link', { name: 'Iniciar sesión' })).toHaveAttribute('href', '/login?email=lucia%40estudionorte.com');
    await user.clear(screen.getByLabelText('Email'));
    await user.type(screen.getByLabelText('Email'), 'ana@ejemplo.com');
    await user.clear(password());
    await user.type(password(), '1234567890');
    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }));
    await waitFor(() => expect(password()).toHaveAccessibleDescription('La contraseña no puede ser solo números.'));
  });

  it('traduce los errores del backend y reconoce el email ya usado aunque llegue en inglés', async () => {
    scenario('sin-sesion');
    server.use(
      http.post('http://localhost:8000/api/v1/auth/register/', () =>
        HttpResponse.json({ email: ['user with this email already exists.'], name: ['This field may not be blank.'], password: ['Ensure this field has at least 10 characters.'] }, { status: 400 }),
      ),
    );
    const user = userEvent.setup();
    show(<RegisterScreen />);
    await fill(user, 'lucia@estudionorte.com');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }));
    await waitFor(() => expect(screen.getByLabelText('Email')).toHaveAccessibleDescription(/Ya existe una cuenta con este email\./));
    expect(screen.getAllByRole('link', { name: 'Iniciar sesión' }).some((link) => link.getAttribute('href') === '/login?email=lucia%40estudionorte.com')).toBe(true);
    expect(screen.getByLabelText('Nombre')).toHaveAccessibleDescription('Este campo no puede quedar vacío.');
    expect(password()).toHaveAccessibleDescription('Mínimo 10 caracteres.');
    expect(screen.queryByText(/already exists|may not be blank|Ensure/)).not.toBeInTheDocument();
  });

  it('al crear la cuenta pasa a Verificar email', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<RegisterScreen />);
    await fill(user, 'ana@ejemplo.com');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/verificar?email=ana%40ejemplo.com'));
    expect(loadDb().users.find((item) => item.email === 'ana@ejemplo.com')).toMatchObject({ is_email_verified: false });
    // No session until the email is verified.
    expect(getTokens()).toBeNull();
  });

  it('con las respuestas reales del backend: email ya usado y contraseña débil a la vez, y después la cuenta', async () => {
    scenario('sin-sesion');
    let answer: { status: number; body: Record<string, unknown> } = real.errors.register_400_taken_and_weak;
    server.use(http.post('http://localhost:8000/api/v1/auth/register/', () => HttpResponse.json(answer.body, { status: answer.status })));
    const user = userEvent.setup();
    show(<RegisterScreen />);
    await fill(user, 'tester@test.com', '12345678');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }));
    await waitFor(() => expect(screen.getByLabelText('Email')).toHaveAccessibleDescription(/Ya existe una cuenta con este email\./));
    expect(password()).toHaveAccessibleDescription('Esa contraseña es muy común. Elegí otra.');
    expect(screen.getAllByRole('link', { name: 'Iniciar sesión' }).some((link) => link.getAttribute('href') === '/login?email=tester%40test.com')).toBe(true);

    answer = { status: 201, body: real.auth_register };
    await user.clear(screen.getByLabelText('Email'));
    await user.type(screen.getByLabelText('Email'), 'ana@example.com');
    await user.click(screen.getByRole('button', { name: 'Crear cuenta' }));
    await waitFor(() => expect(nav.push).toHaveBeenCalledWith('/verificar?email=ana%40example.com'));
  });
});

describe('A3 · Verificar email', () => {
  it('dice a qué email se mandó el link y deja reenviar una vez por minuto', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<VerifyEmailScreen email="lucia@estudionorte.com" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Te mandamos un link a tu email' })).toBeInTheDocument();
    expect(screen.getByText('lucia@estudionorte.com')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reenviar' }));
    const waiting = await screen.findByRole('button', { name: /Reenviar en (1:00|0:5\d)/ });
    expect(waiting).toBeDisabled();
    expect(await screen.findByText(/Te mandamos otro email/)).toBeInTheDocument();
  });

  it('si el servidor dice que es muy pronto, muestra la espera que falta', async () => {
    scenario('sin-sesion');
    const db = loadDb();
    db.resendAt['lucia@estudionorte.com'] = Date.now() - 18_000;
    window.localStorage.setItem('oc.mock.db', JSON.stringify(db));
    const user = userEvent.setup();
    show(<VerifyEmailScreen email="lucia@estudionorte.com" fromLogin />);
    expect(screen.getByText(/Tu cuenta todavía no está verificada/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Reenviar' }));
    expect(await screen.findByRole('button', { name: /Reenviar en 0:4\d/ })).toBeDisabled();
    expect(screen.queryByText(/Algo falló/)).not.toBeInTheDocument();
  });

  it('sin email en el link lo pide para reenviar', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<VerifyEmailScreen />);
    await user.click(screen.getByRole('button', { name: 'Reenviar' }));
    expect(screen.getByLabelText('Email de tu cuenta')).toHaveAccessibleDescription('Escribí tu email.');
    await user.type(screen.getByLabelText('Email de tu cuenta'), 'sofia@ejemplo.com');
    await user.click(screen.getByRole('button', { name: 'Reenviar' }));
    expect(await screen.findByRole('button', { name: /Reenviar en/ })).toBeDisabled();
  });

  it('el link del email verifica la cuenta, inicia la sesión y entra a la Bienvenida', async () => {
    scenario('email-sin-verificar');
    show(<VerifyEmailScreen token="abc123" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Verificando tu email' })).toBeInTheDocument();
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/bienvenida'));
    expect(getTokens()).not.toBeNull();
  });

  it('el link de otra cuenta no cambia la sesión de este navegador', async () => {
    scenario('estudio-norte');
    const before = getTokens();
    show(<VerifyEmailScreen token="abc123" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Este link es de otra cuenta' })).toBeInTheDocument();
    expect(screen.getByText('Este link es de otra cuenta. Cerrá la sesión y volvé a abrirlo.')).toBeInTheDocument();
    expect(getTokens()).toEqual(before);
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('el link de un cambio de email pide la sesión de esa cuenta', async () => {
    scenario('sin-sesion');
    show(<VerifyEmailScreen token="cambio-de-email" next="/perfil" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Iniciá sesión para confirmar el email' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Iniciar sesión' })).toHaveAttribute(
      'href',
      `/login?siguiente=${encodeURIComponent('/verificar?token=cambio-de-email&siguiente=%2Fperfil')}`,
    );
    expect(getTokens()).toBeNull();
  });

  it('con la sesión de su cuenta, el link de un cambio de email lo confirma y vuelve al perfil', async () => {
    scenario('estudio-norte');
    const db = loadDb();
    const lucia = db.users.find((item) => item.email === 'lucia@estudionorte.com');
    if (!lucia) throw new Error('falta Lucía');
    lucia.pending_email = 'lucia@nuevo.com';
    window.localStorage.setItem('oc.mock.db', JSON.stringify(db));
    show(<VerifyEmailScreen token="cambio-de-email" next="/perfil" />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/perfil'));
    expect(loadDb().users.find((item) => item.id === lucia.id)).toMatchObject({ email: 'lucia@nuevo.com', pending_email: null });
  });

  it('después de verificar vuelve a la página que se había pedido antes del login', async () => {
    scenario('email-sin-verificar');
    // Waiting screen, reached from a login that was going to an invitation.
    const waiting = show(<VerifyEmailScreen email="sofia@ejemplo.com" fromLogin next="/invitacion/con-cuenta" />);
    await screen.findByRole('heading', { level: 1, name: 'Te mandamos un link a tu email' });
    waiting.unmount();
    // The link of the email, opened later (it does not carry the destination).
    show(<VerifyEmailScreen token="abc123" />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/invitacion/con-cuenta'));
    expect(nav.replace).not.toHaveBeenCalledWith('/bienvenida');
  });

  it('otra persona que verifica después en el mismo navegador entra por la Bienvenida', async () => {
    scenario('email-sin-verificar');
    // Someone left a destination behind and never opened the email.
    const first = show(<VerifyEmailScreen email="carla@estudionorte.com" fromLogin next="/uso/movimientos" />);
    await screen.findByRole('heading', { level: 1, name: 'Te mandamos un link a tu email' });
    first.unmount();
    // Later, another account verifies with its link (the mock verifies Sofía).
    show(<VerifyEmailScreen token="abc123" />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/bienvenida'));
    expect(nav.replace).not.toHaveBeenCalledWith('/uso/movimientos');
    expect(window.localStorage.getItem('oc.acceso.siguiente')).toBeNull();
  });

  it('abrir la espera sin destino olvida el que había quedado', async () => {
    scenario('email-sin-verificar');
    const first = show(<VerifyEmailScreen email="sofia@ejemplo.com" fromLogin next="/uso/movimientos" />);
    await screen.findByRole('heading', { level: 1, name: 'Te mandamos un link a tu email' });
    expect(window.localStorage.getItem('oc.acceso.siguiente')).not.toBeNull();
    first.unmount();
    // The same person registers again, this time going nowhere in particular.
    const second = show(<VerifyEmailScreen email="sofia@ejemplo.com" />);
    await screen.findByRole('heading', { level: 1, name: 'Te mandamos un link a tu email' });
    expect(window.localStorage.getItem('oc.acceso.siguiente')).toBeNull();
    second.unmount();
    show(<VerifyEmailScreen token="abc123" />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/bienvenida'));
  });

  it('si el link trae su propio destino, lo sigue y no deja guardado el anterior', async () => {
    scenario('email-sin-verificar');
    const first = show(<VerifyEmailScreen email="sofia@ejemplo.com" fromLogin next="/uso/movimientos" />);
    await screen.findByRole('heading', { level: 1, name: 'Te mandamos un link a tu email' });
    first.unmount();
    show(<VerifyEmailScreen token="abc123" next="/invitacion/con-cuenta" />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/invitacion/con-cuenta'));
    expect(nav.replace).toHaveBeenCalledTimes(1);
    expect(window.localStorage.getItem('oc.acceso.siguiente')).toBeNull();
  });

  it('un «siguiente» de otro sitio no se guarda', async () => {
    scenario('email-sin-verificar');
    const waiting = show(<VerifyEmailScreen email="sofia@ejemplo.com" next={'/\t/otro.sitio'} />);
    waiting.unmount();
    show(<VerifyEmailScreen token="abc123" next="//otro.sitio" />);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/bienvenida'));
  });

  it('un link vencido ofrece pedir otro', async () => {
    scenario('sin-sesion');
    show(<VerifyEmailScreen token="vencido" email="ana@ejemplo.com" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Este link ya no sirve' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reenviar' })).toBeEnabled();
    expect(nav.replace).not.toHaveBeenCalled();
  });
});

describe('A4 · Recuperar contraseña', () => {
  it('responde lo mismo exista o no el email, y espera antes de reenviar', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<RecoverScreen />);
    await user.click(screen.getByRole('button', { name: 'Mandarme el link' }));
    expect(screen.getByLabelText('Email')).toHaveAccessibleDescription('Escribí tu email.');
    await user.type(screen.getByLabelText('Email'), 'nadie@ejemplo.com');
    await user.click(screen.getByRole('button', { name: 'Mandarme el link' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Si el email existe, te mandamos un link. Revisá tu correo.');
    expect(screen.getByRole('button', { name: /Reenviar en/ })).toBeDisabled();
  });

  it('paso 2: las dos contraseñas tienen que coincidir', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<RecoverScreen token="abc123" email="lucia@estudionorte.com" />);
    expect(screen.getByRole('heading', { level: 1, name: 'Elegí una contraseña nueva' })).toBeInTheDocument();
    expect(screen.getByText('lucia@estudionorte.com')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Contraseña nueva'), 'contrasena-nueva');
    await user.type(screen.getByLabelText('Repetí la contraseña'), 'contrasena-nuev');
    await user.click(screen.getByRole('button', { name: 'Guardar y entrar' }));
    expect(screen.getByLabelText('Repetí la contraseña')).toHaveAccessibleDescription('Las contraseñas no coinciden.');
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('paso 2: guarda la contraseña y entra', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<RecoverScreen token="abc123" email="lucia@estudionorte.com" />);
    await user.type(screen.getByLabelText('Contraseña nueva'), 'contrasena-nueva');
    await user.type(screen.getByLabelText('Repetí la contraseña'), 'contrasena-nueva');
    await user.click(screen.getByRole('button', { name: 'Guardar y entrar' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
    expect(getTokens()).not.toBeNull();
  });

  it('paso 2: sin saber de quién es el link, manda a iniciar sesión', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<RecoverScreen token="abc123" />);
    await user.type(screen.getByLabelText('Contraseña nueva'), 'contrasena-nueva');
    await user.type(screen.getByLabelText('Repetí la contraseña'), 'contrasena-nueva');
    await user.click(screen.getByRole('button', { name: 'Guardar y entrar' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/login'));
    expect(getTokens()).toBeNull();
  });

  it('paso 2: muestra la contraseña que rechaza el servidor y el link vencido', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    const view = show(<RecoverScreen token="abc123" />);
    await user.type(screen.getByLabelText('Contraseña nueva'), '1234567890');
    await user.type(screen.getByLabelText('Repetí la contraseña'), '1234567890');
    await user.click(screen.getByRole('button', { name: 'Guardar y entrar' }));
    await waitFor(() => expect(screen.getByLabelText('Contraseña nueva')).toHaveAccessibleDescription('La contraseña no puede ser solo números.'));
    view.unmount();

    show(<RecoverScreen token="vencido" email="lucia@estudionorte.com" />);
    await user.type(screen.getByLabelText('Contraseña nueva'), 'contrasena-nueva');
    await user.type(screen.getByLabelText('Repetí la contraseña'), 'contrasena-nueva');
    await user.click(screen.getByRole('button', { name: 'Guardar y entrar' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Este link ya no sirve' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Pedir otro link' })).toHaveAttribute('href', '/recuperar?email=lucia%40estudionorte.com');
  });

  it('con las respuestas reales del backend: el 429 cuenta como enviado, y el link vencido o la contraseña débil', async () => {
    scenario('sin-sesion');
    server.use(
      http.post('http://localhost:8000/api/v1/auth/password-reset/', () => HttpResponse.json(real.errors.resend_429.body, { status: 429 })),
      http.post('http://localhost:8000/api/v1/auth/password-reset/confirm/', async ({ request }) => {
        const { token } = (await request.json()) as { token: string };
        const answer = token === 'vencido' ? real.errors.password_reset_confirm_400_token : real.errors.password_reset_confirm_400_password;
        return HttpResponse.json(answer.body, { status: answer.status });
      }),
    );
    const user = userEvent.setup();
    const first = show(<RecoverScreen />);
    await user.type(screen.getByLabelText('Email'), 'lucia@estudionorte.com');
    await user.click(screen.getByRole('button', { name: 'Mandarme el link' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Si el email existe, te mandamos un link. Revisá tu correo.');
    expect(screen.getByRole('button', { name: /Reenviar en (1:00|0:5\d)/ })).toBeDisabled();
    first.unmount();

    const second = show(<RecoverScreen token="abc.123" email="lucia@estudionorte.com" />);
    await user.type(screen.getByLabelText('Contraseña nueva'), '12345678');
    await user.type(screen.getByLabelText('Repetí la contraseña'), '12345678');
    await user.click(screen.getByRole('button', { name: 'Guardar y entrar' }));
    await waitFor(() => expect(screen.getByLabelText('Contraseña nueva')).toHaveAccessibleDescription('Esa contraseña es muy común. Elegí otra.'));
    second.unmount();

    show(<RecoverScreen token="vencido" email="lucia@estudionorte.com" />);
    await user.type(screen.getByLabelText('Contraseña nueva'), 'contrasena-nueva');
    await user.type(screen.getByLabelText('Repetí la contraseña'), 'contrasena-nueva');
    await user.click(screen.getByRole('button', { name: 'Guardar y entrar' }));
    expect(await screen.findByRole('heading', { level: 1, name: 'Este link ya no sirve' })).toBeInTheDocument();
  });
});

describe('A5 · Aceptar invitación', () => {
  it('sin cuenta: la crea ahí mismo y entra al workspace', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    show(<InvitationScreen token="valida" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Lucía Fernández te invitó a Estudio Norte como Editor' })).toBeInTheDocument();
    expect(screen.getByText(/Como Editor podés crear, revisar y publicar/)).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toBeDisabled();
    expect(screen.getByLabelText('Email')).toHaveValue('juan@estudionorte.com');

    await user.click(screen.getByRole('button', { name: 'Crear cuenta y unirme' }));
    expect(screen.getByLabelText('Nombre')).toHaveAccessibleDescription('Escribí tu nombre.');
    expect(screen.getByRole('checkbox')).toBeInvalid();

    await user.type(screen.getByLabelText('Nombre'), 'Juan Ibáñez');
    await user.type(password(), 'contrasena-segura');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Crear cuenta y unirme' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
    expect(getTokens()).not.toBeNull();
    const db = loadDb();
    const juan = db.users.find((item) => item.email === 'juan@estudionorte.com');
    expect(db.members.some((item) => item.user === juan?.id && item.role === 'editor' && item.status === 'active')).toBe(true);
  });

  it('con cuenta y sin sesión: pide iniciar sesión y vuelve a la invitación', async () => {
    scenario('sin-sesion');
    show(<InvitationScreen token="con-cuenta" />);
    const link = await screen.findByRole('link', { name: 'Iniciar sesión para aceptar' });
    expect(link).toHaveAttribute('href', '/login?siguiente=%2Finvitacion%2Fcon-cuenta&email=sofia%40ejemplo.com');
    expect(screen.queryByLabelText('Nombre')).not.toBeInTheDocument();
  });

  it('con la sesión de la cuenta invitada: acepta con un clic', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    show(<InvitationScreen token="con-cuenta" />);
    const button = await screen.findByRole('button', { name: 'Aceptar invitación' });
    // Every heading the screen shows from here on.
    const seen: string[] = [];
    const observer = new MutationObserver(() => {
      const title = document.querySelector('h1')?.textContent;
      if (title && seen[seen.length - 1] !== title) seen.push(title);
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    await user.click(button);
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
    // On the way to the app the card stays as it was: it never shows "ya se usó" or "ya sos parte".
    expect(seen.some((title) => /ya se usó|Ya sos parte/.test(title))).toBe(false);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Lucía Fernández te invitó a Estudio Norte como Admin');
    observer.disconnect();
    const db = loadDb();
    expect(db.invitations.find((item) => item.token === 'con-cuenta')?.status).toBe('used');
    // The app opens on the workspace that was joined, not on the one the user had before.
    expect(getActiveWorkspaceId()).toBe(db.workspaces.find((item) => item.name === 'Estudio Norte')?.id);
  });

  const ACCEPT = 'http://localhost:8000/api/v1/invitations/con-cuenta/accept/';
  const unauthorized = () => HttpResponse.json({ detail: 'Given token not valid for any token type' }, { status: 401 });

  it('con la sesión muerta: la cierra, y «Iniciar sesión» ya no rebota a la invitación', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    // The access token and the refresh token are both dead.
    server.use(http.post(ACCEPT, unauthorized), http.post('http://localhost:8000/api/token/refresh/', unauthorized));
    show(<InvitationScreen token="con-cuenta" />);
    await user.click(await screen.findByRole('button', { name: 'Aceptar invitación' }));
    expect(await screen.findByText('Tu sesión se cerró')).toBeInTheDocument();
    // No tokens left: the login will ask for the password instead of sending a "signed-in" user back here.
    expect(getTokens()).toBeNull();
    expect(await screen.findByRole('link', { name: 'Iniciar sesión para aceptar' })).toHaveAttribute('href', '/login?siguiente=%2Finvitacion%2Fcon-cuenta&email=sofia%40ejemplo.com');
    expect(screen.queryByRole('button', { name: 'Aceptar invitación' })).not.toBeInTheDocument();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('si renovar el token falla por algo pasajero, muestra el error y se puede volver a intentar', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    // The access token aged out and the refresh endpoint is down for a moment: the session is not known to be dead.
    server.use(http.post(ACCEPT, unauthorized, { once: true }), http.post('http://localhost:8000/api/token/refresh/', () => HttpResponse.json({ detail: 'boom' }, { status: 500 }), { once: true }));
    show(<InvitationScreen token="con-cuenta" />);
    await user.click(await screen.findByRole('button', { name: 'Aceptar invitación' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Algo falló de nuestro lado. Probá de nuevo en un momento.');
    expect(screen.queryByText('Tu sesión se cerró')).not.toBeInTheDocument();
    expect(getTokens()).not.toBeNull();
    // Not a dead end: the same button works once the server is back.
    await user.click(screen.getByRole('button', { name: 'Aceptar invitación' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
  });

  it('si el servidor sigue rechazando la sesión aunque se renueve, también la cierra', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    server.use(http.post(ACCEPT, unauthorized));
    show(<InvitationScreen token="con-cuenta" />);
    await user.click(await screen.findByRole('button', { name: 'Aceptar invitación' }));
    expect(await screen.findByText('Tu sesión se cerró')).toBeInTheDocument();
    expect(getTokens()).toBeNull();
  });

  it('con el token de acceso vencido pero la sesión viva: lo renueva y acepta', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    const before = getTokens()?.access;
    // Mock tokens are stamped with the millisecond: make sure the renewed one differs.
    await new Promise((resolve) => setTimeout(resolve, 5));
    server.use(http.post(ACCEPT, unauthorized, { once: true }));
    show(<InvitationScreen token="con-cuenta" />);
    await user.click(await screen.findByRole('button', { name: 'Aceptar invitación' }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'));
    expect(getTokens()?.access).not.toBe(before);
    expect(screen.queryByText('Tu sesión se cerró')).not.toBeInTheDocument();
    expect(loadDb().invitations.find((item) => item.token === 'con-cuenta')?.status).toBe('used');
  });

  it('un 400 sin campo se muestra y la sesión queda', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    server.use(http.post(ACCEPT, () => HttpResponse.json({ error: 'La invitación es para otro email.' }, { status: 400 })));
    show(<InvitationScreen token="con-cuenta" />);
    await user.click(await screen.findByRole('button', { name: 'Aceptar invitación' }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('La invitación es para otro email.'));
    expect(screen.getByRole('button', { name: 'Aceptar invitación' })).not.toHaveAttribute('aria-busy');
    expect(getTokens()).not.toBeNull();
    expect(nav.replace).not.toHaveBeenCalled();
  });

  it('sin sesión, si el email ya tiene cuenta lo dice con el link a Iniciar sesión', async () => {
    scenario('sin-sesion');
    const user = userEvent.setup();
    server.use(http.post('http://localhost:8000/api/v1/invitations/valida/accept/', unauthorized));
    show(<InvitationScreen token="valida" />);
    await user.type(await screen.findByLabelText('Nombre'), 'Juan Ibáñez');
    await user.type(password(), 'contrasena-segura');
    await user.click(screen.getByRole('checkbox'));
    await user.click(screen.getByRole('button', { name: 'Crear cuenta y unirme' }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Hace falta iniciar sesión');
    expect(within(alert).getByRole('link', { name: 'Iniciar sesión' })).toHaveAttribute('href', '/login?siguiente=%2Finvitacion%2Fvalida&email=juan%40estudionorte.com');
  });

  it('si guardar el workspace activo no responde nunca, igual entra a la app', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    const original = joinOptions.switchTimeoutMs;
    joinOptions.switchTimeoutMs = 150;
    try {
      server.use(
        http.patch('http://localhost:8000/api/v1/me/', async () => {
          await delay('infinite');
          return HttpResponse.json({});
        }),
      );
      show(<InvitationScreen token="con-cuenta" />);
      await user.click(await screen.findByRole('button', { name: 'Aceptar invitación' }));
      await waitFor(() => expect(nav.replace).toHaveBeenCalledWith('/proyectos'), { timeout: 4000 });
      // The requests already go to the workspace that was joined.
      expect(getActiveWorkspaceId()).toBe(loadDb().workspaces.find((item) => item.name === 'Estudio Norte')?.id);
    } finally {
      joinOptions.switchTimeoutMs = original;
    }
  });

  it('reconoce al miembro por el id del workspace, no por el nombre', async () => {
    scenario('estudio-norte');
    const db = loadDb();
    // Another workspace with the same name as one of Lucía's.
    const invitation = db.invitations.find((item) => item.token === 'usada');
    if (invitation) invitation.workspace_id = '00000000-0000-4000-8000-00000000abcd';
    window.localStorage.setItem('oc.mock.db', JSON.stringify(db));
    show(<InvitationScreen token="usada" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Esta invitación ya se usó' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Ir a Estudio Norte' })).not.toBeInTheDocument();
  });

  it('con la sesión de otra cuenta: avisa y deja cerrar sesión', async () => {
    scenario('estudio-norte');
    const user = userEvent.setup();
    show(<InvitationScreen token="valida" />);
    const notice = await screen.findByText('Entraste con otra cuenta');
    const banner = notice.closest('[role="status"]') as HTMLElement;
    expect(within(banner).getByText('lucia@estudionorte.com')).toBeInTheDocument();
    expect(within(banner).getByText('juan@estudionorte.com')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Aceptar invitación' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Cerrar sesión y seguir' }));
    // Without the session the invitation offers to create the invited account.
    expect(await screen.findByRole('button', { name: 'Crear cuenta y unirme' })).toBeInTheDocument();
    expect(getTokens()).toBeNull();
  });

  it('vencida, ya usada, ya miembro y la que no existe', async () => {
    scenario('sin-sesion');
    let view = show(<InvitationScreen token="vencida" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Esta invitación venció' })).toBeInTheDocument();
    expect(screen.getByText('Pedile a Lucía Fernández que te mande una nueva.')).toBeInTheDocument();
    view.unmount();

    view = show(<InvitationScreen token="usada" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Esta invitación ya se usó' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'iniciá sesión' })).toHaveAttribute('href', '/login');
    view.unmount();

    view = show(<InvitationScreen token="no-existe" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Esta invitación no existe' })).toBeInTheDocument();
    view.unmount();

    scenario('estudio-norte');
    show(<InvitationScreen token="usada" />);
    expect(await screen.findByRole('heading', { level: 1, name: 'Ya sos parte de Estudio Norte' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ir a Estudio Norte' })).toBeInTheDocument();
  });
});

describe('B1 · Bienvenida', () => {
  it('los tres pasos: nombre del workspace, uso y primer video', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    show(<WelcomeScreen />);
    expect(await screen.findByRole('heading', { level: 1, name: '¿Cómo se llama tu workspace?' })).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Paso 1 de 3' })).toBeInTheDocument();
    const name = screen.getByLabelText('Nombre del workspace');
    expect(name).toHaveValue('Mi Espacio');
    await user.clear(name);
    await user.click(screen.getByRole('button', { name: 'Continuar' }));
    expect(name).toHaveAccessibleDescription('Ponele un nombre a tu workspace.');
    await user.type(name, 'Canal de Sofía');
    await user.click(screen.getByRole('button', { name: 'Continuar' }));

    expect(await screen.findByRole('heading', { level: 1, name: '¿Para qué lo vas a usar?' })).toHaveFocus();
    expect(screen.getByText(/tu primer proyecto en Canal de Sofía/)).toBeInTheDocument();
    const group = screen.getByRole('radiogroup', { name: '¿Para qué lo vas a usar?' });
    expect(within(group).getAllByRole('radio')).toHaveLength(4);
    expect(within(group).getByRole('radio', { name: /Podcast/ })).toBeChecked();
    await user.click(within(group).getByRole('radio', { name: /Gaming/ }));
    await user.keyboard('{ArrowRight}');
    expect(within(group).getByRole('radio', { name: /Educación/ })).toBeChecked();
    expect(within(group).getByRole('radio', { name: /Educación/ })).toHaveFocus();
    await user.keyboard('{ArrowLeft}');
    await user.click(screen.getByRole('button', { name: 'Continuar' }));

    // The focus moves to the new step: a second Enter must not press "Subí tu primer video".
    expect(await screen.findByRole('heading', { level: 1, name: 'Todo listo para tu primer video' })).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(nav.push).not.toHaveBeenCalled();
    expect(screen.getByText('Canal de Sofía')).toBeInTheDocument();
    expect(screen.getByText('Gaming')).toBeInTheDocument();
    expect(screen.getByText('Dinámico')).toBeInTheDocument();
    expect(screen.getByText(/Plan Gratis/)).toHaveTextContent('Plan Gratis · 300 tokens');
    expect(await screen.findByText(/Con el plan Gratis, hasta/)).toHaveTextContent('hasta 10 min por video');

    const db = loadDb();
    const sofia = db.users.find((item) => item.email === 'sofia@ejemplo.com');
    expect(sofia?.onboarding_use_case).toBe('gaming');
    expect(db.workspaces.find((item) => item.id === sofia?.active_workspace_id)?.name).toBe('Canal de Sofía');

    await user.click(screen.getByRole('button', { name: 'Subí tu primer video' }));
    expect(nav.push).toHaveBeenCalledWith('/proyectos/nuevo');
    expect(window.localStorage.getItem('oc.bienvenida.vista')).toBe(sofia?.id);
  });

  it('saltear marca la bienvenida como vista y va a Proyectos sin guardar nada', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    show(<WelcomeScreen />);
    await user.click(await screen.findByRole('button', { name: 'Saltear' }));
    expect(nav.replace).toHaveBeenCalledWith('/proyectos');
    const sofia = loadDb().users.find((item) => item.email === 'sofia@ejemplo.com');
    expect(sofia?.onboarding_use_case).toBeNull();
    expect(window.localStorage.getItem('oc.bienvenida.vista')).toBe(sofia?.id);
    expect(needsWelcome({ id: sofia?.id ?? '', tour_completed: false, onboarding_use_case: null } as Parameters<typeof needsWelcome>[0])).toBe(false);
  });

  it('desde el resumen se puede volver a cambiar cada paso', async () => {
    scenario('usuario-nuevo');
    const user = userEvent.setup();
    show(<WelcomeScreen initialStep={3} />);
    await user.click(await screen.findByRole('button', { name: 'Cambiar para qué lo vas a usar' }));
    expect(screen.getByRole('heading', { level: 1, name: '¿Para qué lo vas a usar?' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Atrás' }));
    expect(screen.getByRole('heading', { level: 1, name: '¿Cómo se llama tu workspace?' })).toBeInTheDocument();
  });

  it('quien no puede renombrar el workspace ve el nombre bloqueado y sigue igual', async () => {
    scenario('rol-editor');
    const user = userEvent.setup();
    show(<WelcomeScreen />);
    const name = await screen.findByLabelText('Nombre del workspace');
    expect(name).toBeDisabled();
    expect(name).toHaveAccessibleDescription(/Solo un admin puede cambiarlo/);
    await user.click(screen.getByRole('button', { name: 'Continuar' }));
    expect(await screen.findByRole('heading', { level: 1, name: '¿Para qué lo vas a usar?' })).toBeInTheDocument();
    expect(loadDb().workspaces.some((item) => item.name === 'Estudio Norte')).toBe(true);
  });
});
