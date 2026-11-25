import { expect, test, type Page } from '@playwright/test';
import { expectClean, settle, useScenario, watch } from './helpers';

async function signIn(page: Page, email: string, password = 'contrasena') {
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Contraseña', { exact: true }).fill(password);
  await page.getByRole('button', { name: 'Iniciar sesión' }).click();
}

test.describe('Acceso (A1–A5) y Bienvenida (B1)', () => {
  test('A1: iniciar sesión lleva a Proyectos', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    const problems = watch(page);
    await page.goto('/login');
    await settle(page);
    await signIn(page, 'lucia@estudionorte.com');
    await expect(page).toHaveURL(/\/proyectos$/);
    await expect(page.getByRole('button', { name: /Cambiar de workspace. Actual: Estudio Norte/ })).toBeVisible();
    expectClean(problems, 'el login');
  });

  test('A1: después de entrar vuelve a la página que se había pedido', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    await page.goto('/uso/movimientos');
    await expect(page).toHaveURL(/\/login\?siguiente=%2Fuso%2Fmovimientos$/);
    await signIn(page, 'lucia@estudionorte.com');
    await expect(page).toHaveURL(/\/uso\/movimientos$/);
  });

  test('A1: credenciales incorrectas, y con sesión el login manda a la app', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    await page.goto('/login');
    await signIn(page, 'lucia@estudionorte.com', 'incorrecta');
    await expect(page.locator('#login-error')).toHaveText('El email o la contraseña no coinciden.');
    await expect(page).toHaveURL(/\/login$/);
    await page.getByLabel('Contraseña', { exact: true }).fill('contrasena');
    await page.getByRole('button', { name: 'Iniciar sesión' }).click();
    await expect(page).toHaveURL(/\/proyectos$/);
    await page.goto('/login');
    await expect(page).toHaveURL(/\/proyectos$/);
  });

  test('A1: un «siguiente» armado para salir de la app no redirige afuera', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    const outside: string[] = [];
    page.on('request', (request) => {
      if (request.isNavigationRequest() && !request.url().startsWith('http://127.0.0.1')) outside.push(request.url());
    });
    await page.route((url) => url.hostname === 'otro.sitio', (route) => route.abort());
    // Tab, line feed, carriage return, backslash and a dot segment: all of them are "//otro.sitio" for a browser.
    const attempts = ['/%09/otro.sitio', '/%0A/otro.sitio', '/%0D/otro.sitio', '/%5Cotro.sitio', '/.//otro.sitio', '//otro.sitio', 'https://otro.sitio'];
    await page.goto(`/login?siguiente=${attempts[0]}`);
    await signIn(page, 'lucia@estudionorte.com');
    await expect(page).toHaveURL(/127\.0\.0\.1:\d+\/proyectos$/);
    // Already signed in: the login redirects by itself, with no click.
    for (const attempt of attempts) {
      await page.goto(`/login?siguiente=${attempt}`);
      await expect(page).toHaveURL(/127\.0\.0\.1:\d+\/proyectos$/);
    }
    expect(outside).toEqual([]);
  });

  test('A1 → A3 → A5: verificar el email devuelve a la invitación que se había pedido', async ({ page }) => {
    await useScenario(page, 'email-sin-verificar');
    await page.goto('/invitacion/con-cuenta');
    await page.getByRole('link', { name: 'Iniciar sesión para aceptar' }).click();
    await page.getByLabel('Contraseña', { exact: true }).fill('contrasena');
    await page.getByRole('button', { name: 'Iniciar sesión' }).click();
    await expect(page).toHaveURL(/\/verificar\?email=sofia%40ejemplo\.com&motivo=login&siguiente=%2Finvitacion%2Fcon-cuenta$/);
    // The link of the email does not carry the destination.
    await page.goto('/verificar?token=mock-token');
    await expect(page).toHaveURL(/\/invitacion\/con-cuenta$/);
    await expect(page.getByRole('button', { name: 'Aceptar invitación' })).toBeVisible();
  });

  test('A1 en el teléfono (M12): una columna, sin scroll horizontal y controles de 44 px', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'sin-sesion');
    await page.goto('/login');
    await settle(page);
    await expect(page.getByRole('heading', { level: 1, name: 'Iniciar sesión' })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    for (const control of [page.getByLabel('Email'), page.getByLabel('Contraseña', { exact: true }), page.getByRole('button', { name: 'Iniciar sesión' })]) {
      expect((await control.boundingBox())?.height).toBeGreaterThanOrEqual(44);
    }
  });

  test('A1 → A3: con el email sin verificar lleva a Verificar email', async ({ page }) => {
    await useScenario(page, 'email-sin-verificar');
    await page.goto('/login');
    await signIn(page, 'lucia@estudionorte.com');
    await expect(page).toHaveURL(/\/verificar\?email=lucia%40estudionorte\.com&motivo=login$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Te mandamos un link a tu email' })).toBeVisible();
    await expect(page.getByText('Tu cuenta todavía no está verificada')).toBeVisible();
    await page.getByRole('button', { name: 'Reenviar' }).click();
    await expect(page.getByRole('button', { name: /Reenviar en \d:\d\d/ })).toBeDisabled();
  });

  test('A2 → A3 → B1: crear la cuenta, verificar el email y entrar a la Bienvenida', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    const problems = watch(page);
    await page.goto('/registro');
    await settle(page);
    await page.getByLabel('Nombre').fill('Ana Gómez');
    await page.getByLabel('Email').fill('ana@ejemplo.com');
    await page.getByLabel('Contraseña', { exact: true }).fill('contrasena-segura');
    await page.getByRole('button', { name: 'Crear cuenta' }).click();
    await expect(page.getByText('Para crear la cuenta tenés que aceptar los términos.')).toBeVisible();
    await page.getByRole('checkbox', { name: /Acepto los Términos/ }).check({ force: true });
    await page.getByRole('button', { name: 'Crear cuenta' }).click();

    await expect(page).toHaveURL(/\/verificar\?email=ana%40ejemplo\.com$/);
    await expect(page.getByText('ana@ejemplo.com')).toBeVisible();
    // The email has just been sent: asking again waits a minute, and a reload does not reset the wait.
    await expect(page.getByRole('button', { name: /Reenviar en \d:\d\d/ })).toBeDisabled();
    await page.reload();
    await expect(page.getByRole('button', { name: /Reenviar en \d:\d\d/ })).toBeDisabled();

    // The link of the email.
    await page.goto('/verificar?token=mock-token');
    await expect(page).toHaveURL(/\/bienvenida$/);
    await expect(page.getByRole('heading', { level: 1, name: '¿Cómo se llama tu workspace?' })).toBeVisible();
    expectClean(problems, 'el registro');
  });

  test('A3: un link vencido ofrece pedir otro', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    await page.goto('/verificar?token=vencido&email=sofia%40ejemplo.com');
    await expect(page.getByRole('heading', { level: 1, name: 'Este link ya no sirve' })).toBeVisible();
    await page.getByRole('button', { name: 'Reenviar' }).click();
    await expect(page.getByText(/Te mandamos otro email/)).toBeVisible();
  });

  test('A4: pedir el link y elegir la contraseña nueva', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    const problems = watch(page);
    await page.goto('/login');
    await page.getByRole('link', { name: '¿Olvidaste tu contraseña?' }).click();
    await expect(page).toHaveURL(/\/recuperar$/);
    await page.getByLabel('Email').fill('lucia@estudionorte.com');
    await page.getByRole('button', { name: 'Mandarme el link' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'Si el email existe, te mandamos un link. Revisá tu correo.' })).toBeVisible();
    await expect(page.getByRole('button', { name: /Reenviar en \d:\d\d/ })).toBeDisabled();

    await page.goto('/recuperar?token=mock-token&email=lucia%40estudionorte.com');
    await expect(page.getByRole('heading', { level: 1, name: 'Elegí una contraseña nueva' })).toBeVisible();
    await page.getByLabel('Contraseña nueva').fill('contrasena-nueva');
    await page.getByLabel('Repetí la contraseña').fill('contrasena-nuev');
    await page.getByRole('button', { name: 'Guardar y entrar' }).click();
    await expect(page.getByText('Las contraseñas no coinciden.')).toBeVisible();
    await page.getByLabel('Repetí la contraseña').fill('contrasena-nueva');
    await page.getByRole('button', { name: 'Guardar y entrar' }).click();
    await expect(page).toHaveURL(/\/proyectos$/);
    expectClean(problems, 'recuperar');
  });

  test('A5: sin cuenta, la crea desde la invitación y entra al workspace', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    const problems = watch(page);
    await page.goto('/invitacion/valida');
    await settle(page);
    await expect(page.getByRole('heading', { level: 1, name: 'Lucía Fernández te invitó a Estudio Norte como Editor' })).toBeVisible();
    await expect(page.getByLabel('Email')).toBeDisabled();
    await page.getByLabel('Nombre').fill('Juan Ibáñez');
    await page.getByLabel('Contraseña', { exact: true }).fill('contrasena-segura');
    await page.getByRole('checkbox', { name: /Acepto los Términos/ }).check({ force: true });
    await page.getByRole('button', { name: 'Crear cuenta y unirme' }).click();
    await expect(page).toHaveURL(/\/proyectos$/);
    await expect(page.getByRole('button', { name: /Cambiar de workspace. Actual: Estudio Norte/ })).toBeVisible();
    // The link cannot be used twice.
    await page.goto('/invitacion/valida');
    await expect(page.getByRole('heading', { level: 1, name: 'Ya sos parte de Estudio Norte' })).toBeVisible();
    expectClean(problems, 'la invitación');
  });

  test('A5: con cuenta, inicia sesión, vuelve a la invitación y la acepta', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    await page.goto('/invitacion/con-cuenta');
    await page.getByRole('link', { name: 'Iniciar sesión para aceptar' }).click();
    await expect(page).toHaveURL(/\/login\?siguiente=%2Finvitacion%2Fcon-cuenta&email=sofia%40ejemplo\.com$/);
    await expect(page.getByLabel('Email')).toHaveValue('sofia@ejemplo.com');
    await page.getByLabel('Contraseña', { exact: true }).fill('contrasena');
    await page.getByRole('button', { name: 'Iniciar sesión' }).click();
    await expect(page).toHaveURL(/\/invitacion\/con-cuenta$/);
    await page.getByRole('button', { name: 'Aceptar invitación' }).click();
    await expect(page).toHaveURL(/\/proyectos$/);
    await expect(page.getByRole('button', { name: /Cambiar de workspace. Actual: Estudio Norte/ })).toBeVisible();
  });

  test('A5: vencida, ya usada, inexistente y con la sesión de otra cuenta', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    await page.goto('/invitacion/vencida');
    await expect(page.getByRole('heading', { level: 1, name: 'Esta invitación venció' })).toBeVisible();
    await page.goto('/invitacion/no-existe');
    await expect(page.getByRole('heading', { level: 1, name: 'Esta invitación no existe' })).toBeVisible();
    await page.goto('/invitacion/usada');
    await expect(page.getByRole('heading', { level: 1, name: 'Ya sos parte de Estudio Norte' })).toBeVisible();

    await page.goto('/invitacion/valida');
    await expect(page.getByText('Entraste con otra cuenta')).toBeVisible();
    await page.getByRole('button', { name: 'Cerrar sesión y seguir' }).click();
    await expect(page.getByRole('button', { name: 'Crear cuenta y unirme' })).toBeVisible();
    await page.goto('/invitacion/usada');
    await expect(page.getByRole('heading', { level: 1, name: 'Esta invitación ya se usó' })).toBeVisible();
  });

  test('B1: los tres pasos terminan en Nuevo proyecto y Proyectos deja de mandar a la Bienvenida', async ({ page }) => {
    await useScenario(page, 'usuario-nuevo');
    const problems = watch(page);
    await page.goto('/proyectos');
    await expect(page).toHaveURL(/\/bienvenida$/);
    await settle(page);
    await expect(page.getByRole('list', { name: 'Paso 1 de 3' })).toBeVisible();
    await page.getByLabel('Nombre del workspace').fill('Canal de Sofía');
    await page.getByRole('button', { name: 'Continuar' }).click();

    await expect(page.getByRole('heading', { level: 1, name: '¿Para qué lo vas a usar?' })).toBeVisible();
    await page.getByRole('radio', { name: /Educación/ }).click();
    await page.getByRole('button', { name: 'Continuar' }).click();

    const last = page.getByRole('heading', { level: 1, name: 'Todo listo para tu primer video' });
    await expect(last).toBeFocused();
    // A second Enter does not press the button that took the place of "Continuar".
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/bienvenida$/);
    await expect(page.getByText('Minimalista')).toBeVisible();
    await expect(page.getByText('Plan Gratis · 300 tokens')).toBeVisible();
    await page.getByRole('button', { name: 'Subí tu primer video' }).click();
    await expect(page).toHaveURL(/\/proyectos\/nuevo$/);
    await expect(page.getByRole('button', { name: /Cambiar de workspace. Actual: Canal de Sofía/ })).toBeVisible();

    await page.goto('/proyectos');
    await settle(page);
    await expect(page).toHaveURL(/\/proyectos$/);
    expectClean(problems, 'la bienvenida');
  });

  test('B1: saltear va a Proyectos y no vuelve a aparecer', async ({ page }) => {
    await useScenario(page, 'usuario-nuevo');
    await page.goto('/bienvenida');
    await page.getByRole('button', { name: 'Saltear' }).click();
    await expect(page).toHaveURL(/\/proyectos$/);
    await page.reload();
    await settle(page);
    await expect(page).toHaveURL(/\/proyectos$/);
  });

  test('B1 en el teléfono: las opciones van en una columna, sin scroll horizontal', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'usuario-nuevo');
    await page.goto('/bienvenida?paso=2');
    await settle(page);
    const first = await page.getByRole('radio', { name: /Podcast/ }).boundingBox();
    const second = await page.getByRole('radio', { name: /Gaming/ }).boundingBox();
    expect(second?.x).toBe(first?.x);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });

  test('sesión vencida: pide la contraseña sin perder la pantalla', async ({ page }) => {
    await useScenario(page, 'sesion-vencida');
    await page.goto('/uso');
    const dialog = page.getByRole('dialog', { name: 'Tu sesión venció' });
    await expect(dialog).toBeVisible();
    // The page was opened with the session already expired, so the dialog does not know who it was.
    await dialog.getByLabel('Email').fill('lucia@estudionorte.com');
    await dialog.getByLabel('Contraseña', { exact: true }).fill('contrasena');
    await dialog.getByRole('button', { name: 'Continuar' }).click();
    await expect(dialog).toBeHidden();
    await expect(page).toHaveURL(/\/uso$/);
  });
});
