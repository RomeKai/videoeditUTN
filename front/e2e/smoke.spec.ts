import { expect, test } from '@playwright/test';
import { ROUTELESS, SCREENS } from '../src/dev/screens';
import { expectClean, settle, useScenario, watch } from './helpers';

const screens = Object.values(SCREENS);

test.describe('Smoke en modo mock', () => {
  for (const screen of screens) {
    test(`${screen.code} · ${screen.name} (${screen.example})`, async ({ page }) => {
      // Public screens are visited without a session; the rest with the default scenario's user.
      await useScenario(page, screen.area === 'public' ? 'sin-sesion' : 'estudio-norte');
      const problems = watch(page);
      await page.goto(screen.example);
      await settle(page);
      await expect(page).toHaveURL(new RegExp(`${screen.example.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}/?$`));
      // Every route renders something of its own: the placeholder with its code, or the built screen's heading.
      await expect(page.locator('h1').first()).toBeVisible();
      if (screen.area === 'app') await expect(page.getByRole('navigation', { name: 'Secciones', exact: true })).toBeVisible();
      expectClean(problems, screen.example);
    });
  }

  test('el kit muestra todos los componentes en los dos temas', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/dev/kit');
    // The kit shows skeletons on purpose, so it never looks "settled".
    await page.waitForLoadState('networkidle');
    await expect(page.getByRole('heading', { name: 'Kit de componentes' })).toBeVisible();
    await expect(page.locator('[data-theme="dark"]').first()).toBeVisible();
    await expect(page.locator('[data-theme="light"]').first()).toBeVisible();
    // The vertical player keeps its frame when the parent does not stretch it.
    for (const name of [/Reproductor vertical centrado/, /Reproductor vertical en una caja ajustada/]) {
      for (const player of await page.getByRole('group', { name }).all()) {
        const frame = await player.locator('div').first().boundingBox();
        expect(frame?.width ?? 0, String(name)).toBeGreaterThan(150);
        expect((frame?.height ?? 0) / (frame?.width ?? 1), String(name)).toBeCloseTo(16 / 9, 1);
      }
    }
    expectClean(problems, '/dev/kit');
  });

  // A full reload goes through hydration and the start of the mock worker again: neither may fail.
  for (const path of ['/proyectos', '/mantenimiento', '/uso/comprar/resultado']) {
    test(`recargar ${path} no deja errores de consola ni pedidos sin simular`, async ({ page }) => {
      await useScenario(page, 'estudio-norte');
      const problems = watch(page);
      await page.goto(path);
      await settle(page);
      // Quick reloads first (the mock worker used to unregister itself between two of them)…
      for (let round = 0; round < 6; round += 1) await page.reload({ waitUntil: 'load' });
      // …then slow ones, each waiting for the screen.
      for (let round = 0; round < 2; round += 1) {
        await page.reload();
        await settle(page);
        await expect(page.locator('h1').first()).toBeVisible();
      }
      // The browser checks the worker for updates about a second after the load: a failure shows up then.
      await page.waitForTimeout(1500);
      expectClean(problems, `${path} recargada`);
    });
  }

  test('una ruta que no existe muestra el 404 (K1)', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const response = await page.goto('/esto-no-existe');
    expect(response?.status()).toBe(404);
    await expect(page.getByRole('heading', { name: /Esta página no existe/ })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Ir a Proyectos', exact: true })).toBeVisible();
  });

  test('sin sesión, la app lleva a Iniciar sesión', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    await page.goto('/uso/movimientos');
    await expect(page).toHaveURL(/\/login\?siguiente=%2Fuso%2Fmovimientos$/);
  });

  test('usuario nuevo: Proyectos lleva a la Bienvenida', async ({ page }) => {
    await useScenario(page, 'usuario-nuevo');
    await page.goto('/proyectos');
    await expect(page).toHaveURL(/\/bienvenida$/);
  });

  test('la barra muestra workspace, saldo y notificaciones; ⌘K abre el menú de comandos', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/proyectos');
    await settle(page);
    await expect(page.getByRole('button', { name: /Cambiar de workspace. Actual: Estudio Norte/ })).toBeVisible();
    await expect(page.getByRole('link', { name: /Saldo: 4\.860 tokens disponibles/ })).toBeVisible();
    await page.getByRole('button', { name: /Notificaciones/ }).click();
    await expect(page.getByRole('dialog', { name: 'Notificaciones' })).toBeVisible();
    await page.keyboard.press('Escape');
    await page.keyboard.press('Control+k');
    const menu = page.getByRole('dialog', { name: 'Menú de comandos' });
    await expect(menu).toBeVisible();
    await menu.getByRole('combobox').fill('pod');
    await expect(menu.getByRole('option', { name: /Podcast Ep\. 42/ })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(menu).toBeHidden();
    expectClean(problems, 'la barra');
  });

  test('rol Lector: «Nuevo proyecto» queda deshabilitado, no oculto', async ({ page }) => {
    await useScenario(page, 'rol-lector');
    await page.goto('/proyectos');
    await settle(page);
    const button = page.getByRole('button', { name: 'Nuevo proyecto' });
    await expect(button).toBeVisible();
    await expect(button).toHaveAttribute('aria-disabled', 'true');
  });

  test('el tema elegido se recuerda', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    await page.goto('/login');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await page.getByRole('button', { name: 'Cambiar a tema claro' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.reload();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  });

  test('en el teléfono el menú de cuenta reúne workspace y notificaciones', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/proyectos');
    await settle(page);
    await expect(page.getByRole('link', { name: 'Nuevo proyecto' })).toBeVisible();
    await page.getByRole('button', { name: /Menú de cuenta/ }).click();
    const menu = page.getByRole('menu', { name: 'Cuenta y workspace' });
    await expect(menu.getByRole('menuitemradio', { name: /Estudio Norte/ })).toBeVisible();
    await expect(menu.getByRole('menuitem', { name: /Notificaciones/ })).toBeVisible();
    // The arrow keys walk the items, like any menu.
    await page.keyboard.press('Home');
    const first = await menu.locator(':focus').textContent();
    await page.keyboard.press('ArrowDown');
    await expect(menu.locator(':focus')).toHaveAttribute('role', /^menuitem/);
    expect(await menu.locator(':focus').textContent()).not.toBe(first);
    await page.keyboard.press('ArrowUp');
    expect(await menu.locator(':focus').textContent()).toBe(first);
    expectClean(problems, 'la barra en el teléfono');
  });

  test('el mapa completo está cubierto', () => {
    // 30 routed screens plus the pieces without a route: a new screen in the map must be added to src/dev/screens.ts.
    expect(screens.length).toBeGreaterThanOrEqual(30);
    expect(ROUTELESS.map((item) => item.code)).toEqual(expect.arrayContaining(['E1', 'J1', 'J2', 'J3', 'J4', 'K1', 'K3', 'K4']));
  });
});
