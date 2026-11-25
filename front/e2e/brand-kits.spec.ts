import { expect, test, type Page } from '@playwright/test';
import { expectClean, settle, useScenario, type Problems } from './helpers';

/**
 * Same as the shared `watch()`, minus one thing that is the browser's and not the screen's: the app's icon
 * is /icon.svg, and when the editor adds a history entry (unsaved changes) Chromium sometimes asks for
 * /favicon.ico on its own. That request cannot be intercepted, so its 404 is left out by its URL.
 */
function watch(page: Page): Problems {
  const problems: Problems = { consoleErrors: [], failedRequests: [] };
  page.on('console', (message) => {
    if (message.type() !== 'error' || message.location().url.endsWith('/favicon.ico')) return;
    problems.consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => problems.consoleErrors.push(`pageerror: ${error.message}`));
  page.on('requestfailed', (request) => {
    const failure = request.failure()?.errorText ?? '';
    if (failure.includes('ERR_ABORTED')) return;
    problems.failedRequests.push(`${request.method()} ${request.url()} · ${failure}`);
  });
  page.on('response', (response) => {
    if (response.status() >= 400 && !response.url().endsWith('/favicon.ico')) problems.failedRequests.push(`${response.request().method()} ${response.url()} · ${response.status()}`);
  });
  return problems;
}

test.describe('F1 · Brand kits y F2 · Editar brand kit', () => {
  test('crear un kit, editarlo, usarlo por defecto y borrarlo', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/brand-kits');
    await settle(page);
    await expect(page.getByRole('article', { name: 'Brand kit Estudio Norte' }).getByText('Por defecto')).toBeVisible();

    await page.getByRole('button', { name: /Nuevo brand kit/ }).click();
    await expect(page).toHaveURL(/\/brand-kits\/nuevo$/);
    await page.getByLabel('Nombre').fill('Podcast Vender');
    await page.getByLabel('Palabra activa', { exact: true }).fill('#3DD6C4');
    await page.getByLabel('Logo de marca de agua').setInputFiles({ name: 'logo-podcast.png', mimeType: 'image/png', buffer: Buffer.from('png') });
    await expect(page.getByText('logo-podcast.png')).toBeVisible();
    await page.getByRole('radio', { name: 'Arriba a la derecha' }).click();
    await expect(page.getByText(/el logo arriba a la derecha/)).toBeVisible();
    await page.getByRole('button', { name: 'Crear brand kit' }).click();
    await expect(page.getByText('Se creó «Podcast Vender»')).toBeVisible();
    await expect(page).toHaveURL(/\/brand-kits\/[0-9a-f-]{36}$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Podcast Vender' })).toBeVisible();
    await expect(page.getByRole('radio', { name: 'Arriba a la derecha' })).toBeChecked();

    // Editing: the save bar says what changed; saving clears it.
    await page.getByLabel(/Descripción/).fill('Para los clips del podcast de ventas.');
    await page.getByRole('switch', { name: 'Usar por defecto' }).click();
    const bar = page.getByRole('region', { name: 'Acciones' });
    await expect(bar).toContainText('Cambiaste la descripción y si es el kit por defecto.');
    await bar.getByRole('button', { name: 'Guardar' }).click();
    await expect(page.getByText('Cambios guardados')).toBeVisible();
    await expect(bar).toBeHidden();

    await page.getByRole('link', { name: 'Brand kits' }).last().click();
    await expect(page).toHaveURL(/\/brand-kits$/);
    const created = page.getByRole('article', { name: 'Brand kit Podcast Vender' });
    await expect(created.getByText('Por defecto')).toBeVisible();
    await expect(page.getByRole('article', { name: 'Brand kit Estudio Norte' }).getByText('Por defecto')).toHaveCount(0);

    await created.getByRole('button', { name: 'Acciones de Podcast Vender' }).click();
    await page.getByRole('menuitem', { name: 'Duplicar' }).click();
    await expect(page.getByRole('article', { name: 'Brand kit Copia de Podcast Vender' })).toBeVisible();

    await created.getByRole('button', { name: 'Acciones de Podcast Vender' }).click();
    await page.getByRole('menuitem', { name: 'Borrar' }).click();
    const dialog = page.getByRole('alertdialog', { name: '¿Borrar «Podcast Vender»?' });
    await expect(dialog).toContainText('Es tu kit por defecto');
    await dialog.getByRole('button', { name: 'Borrar brand kit' }).click();
    await expect(created).toHaveCount(0);
    expectClean(problems, 'brand kits');
  });

  test('salir del editor con cambios pide confirmar', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/brand-kits');
    await settle(page);
    await page.getByRole('link', { name: 'Estudio Norte', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await page.getByLabel('Texto', { exact: true }).fill('#FFEEDD');
    await expect(page.getByRole('region', { name: 'Acciones' })).toContainText('Cambiaste el color del texto.');

    await page.getByRole('navigation', { name: 'Secciones', exact: true }).getByRole('link', { name: 'Publicaciones' }).click();
    const dialog = page.getByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Seguir editando' }).click();
    await expect(page).toHaveURL(/\/brand-kits\/[0-9a-f-]{36}$/);
    await expect(page.getByLabel('Texto', { exact: true })).toHaveValue('#FFEEDD');

    await page.getByRole('navigation', { name: 'Secciones', exact: true }).getByRole('link', { name: 'Publicaciones' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Descartar' }).click();
    await expect(page).toHaveURL(/\/publicaciones$/);
    expectClean(problems, 'cambios sin guardar');
  });

  test('el botón Atrás con cambios también pide confirmar', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/brand-kits');
    await settle(page);
    await page.getByRole('link', { name: 'Estudio Norte', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await page.getByLabel(/Descripción/).fill('Otra descripción');
    await expect(page.getByRole('region', { name: 'Acciones' })).toBeVisible();

    await page.goBack();
    const dialog = page.getByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    await expect(dialog).toBeVisible();
    await expect(page).toHaveURL(/\/brand-kits\/[0-9a-f-]{36}$/);
    await dialog.getByRole('button', { name: 'Seguir editando' }).click();
    await expect(page.getByLabel(/Descripción/)).toHaveValue('Otra descripción');

    await page.goBack();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Descartar' }).click();
    await expect(page).toHaveURL(/\/brand-kits$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Brand kits' })).toBeVisible();
    expectClean(problems, 'atrás con cambios');
  });

  test('después de crear o de descartar, Atrás vuelve una sola vez y a donde corresponde', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/brand-kits');
    await settle(page);
    await page.getByRole('button', { name: 'Nuevo brand kit' }).first().click();
    await expect(page).toHaveURL(/\/brand-kits\/nuevo$/);
    await page.getByLabel('Nombre').fill('Kit de prueba');
    await page.getByRole('button', { name: 'Crear brand kit' }).click();
    await expect(page).toHaveURL(/\/brand-kits\/[0-9a-f-]{36}$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Kit de prueba' })).toBeVisible();
    // The new kit took the place of `nuevo`: Back is the list, not an empty form.
    await page.goBack();
    await expect(page).toHaveURL(/\/brand-kits$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Brand kits' })).toBeVisible();

    await page.getByRole('link', { name: 'Estudio Norte', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await page.getByLabel(/Descripción/).fill('Otra descripción');
    await expect(page.getByRole('region', { name: 'Acciones' })).toBeVisible();
    await page.getByRole('navigation', { name: 'Secciones', exact: true }).getByRole('link', { name: 'Publicaciones' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Descartar' }).click();
    await expect(page).toHaveURL(/\/publicaciones$/);
    // One press goes back to the kit, a second one to the list.
    await page.goBack();
    await expect(page).toHaveURL(/\/brand-kits\/[0-9a-f-]{36}$/);
    await expect(page.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/\/brand-kits$/);
    expectClean(problems, 'historial');
  });

  test('sin kits, y en el teléfono', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'workspace-vacio');
    const problems = watch(page);
    await page.goto('/brand-kits');
    await settle(page);
    await expect(page.getByRole('heading', { name: 'Creá tu brand kit para que todos tus clips salgan con tu marca' })).toBeVisible();
    await page.getByRole('link', { name: 'Nuevo brand kit' }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Nuevo brand kit' })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expectClean(problems, 'brand kits vacío en el teléfono');
  });

  test('rol Lector: los kits se ven, no se editan', async ({ page }) => {
    await useScenario(page, 'rol-lector');
    const problems = watch(page);
    await page.goto('/brand-kits');
    await settle(page);
    await expect(page.getByRole('button', { name: /Nuevo brand kit/ })).toHaveAttribute('aria-disabled', 'true');
    await page.getByRole('link', { name: 'Gaming nocturno' }).click();
    await expect(page.getByText('Solo lectura')).toBeVisible();
    await expect(page.getByLabel('Nombre')).toBeDisabled();
    expectClean(problems, 'brand kits como Lector');
  });
});
