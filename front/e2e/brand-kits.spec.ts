import { expect, test } from '@playwright/test';
import { expectClean, settle, useScenario, watch } from './helpers';

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

  test('con cambios, el selector de workspace y el menú de comandos preguntan antes de sacarte de la pantalla', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/brand-kits');
    await settle(page);
    await page.getByRole('link', { name: 'Estudio Norte', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await page.getByLabel(/Descripción/).fill('Otra descripción');
    await expect(page.getByRole('region', { name: 'Acciones' })).toBeVisible();

    // The switcher asks first: staying keeps the workspace and the edits.
    const switcher = page.getByRole('button', { name: /Cambiar de workspace/ });
    await switcher.click();
    await page.getByRole('menuitemradio').and(page.locator('[aria-checked="false"]')).first().click();
    const dialog = page.getByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Seguir editando' }).click();
    await expect(switcher).toHaveAccessibleName(/Actual: Estudio Norte/);
    await expect(page.getByLabel(/Descripción/)).toHaveValue('Otra descripción');

    // The command menu, too.
    await page.keyboard.press('Control+k');
    await page.getByRole('combobox', { name: 'Buscar o ejecutar un comando' }).fill('Publicaciones');
    await page.keyboard.press('Enter');
    await expect(dialog).toBeVisible();
    await expect(page).toHaveURL(/\/brand-kits\/[0-9a-f-]{36}$/);
    await dialog.getByRole('button', { name: 'Seguir editando' }).click();
    await expect(page.getByLabel(/Descripción/)).toHaveValue('Otra descripción');

    // Discarding lets the switch through. The screen is left first and the workspace changes on arrival:
    // the kit is never asked for in a workspace that does not have it, so no "Esta página no existe" on the way out.
    await page.evaluate(() => {
      const seen = window as unknown as { __notFoundShown?: boolean };
      const look = () => {
        if (/Esta página no existe|No encontramos lo que buscabas|No pudimos cargar/.test(document.body.innerText)) seen.__notFoundShown = true;
      };
      new MutationObserver(look).observe(document.body, { childList: true, subtree: true, characterData: true });
    });
    await switcher.click();
    await page.getByRole('menuitemradio').and(page.locator('[aria-checked="false"]')).first().click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Descartar' }).click();
    await expect(page).toHaveURL(/\/proyectos$/);
    await expect(switcher).not.toHaveAccessibleName(/Actual: Estudio Norte/);
    await expect(page.getByText(/^Ahora estás en /)).toBeVisible();
    await settle(page);
    expect(await page.evaluate(() => (window as unknown as { __notFoundShown?: boolean }).__notFoundShown ?? false)).toBe(false);
    // No request failed either: nothing was asked of the wrong workspace.
    expectClean(problems, 'cambio de workspace con cambios');
    // Back from there is not held by a guard that is gone.
    await page.goBack();
    await expect(page.getByRole('alertdialog')).toHaveCount(0);
  });

  test('cambiar de workspace en otra pestaña no pisa los cambios sin guardar de esta', async ({ context, page }) => {
    await useScenario(page, 'estudio-norte');
    await page.goto('/brand-kits');
    await settle(page);
    await page.getByRole('link', { name: 'Estudio Norte', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await page.getByLabel(/Descripción/).fill('Cambio sin guardar');
    await expect(page.getByRole('region', { name: 'Acciones' })).toBeVisible();

    // The same person, another tab of the same browser: they switch workspace there.
    const other = await context.newPage();
    await other.goto('/proyectos');
    await settle(other);
    await other.getByRole('button', { name: /Cambiar de workspace/ }).click();
    await other.getByRole('menuitemradio').and(other.locator('[aria-checked="false"]')).first().click();
    await expect(other.getByRole('button', { name: /Cambiar de workspace/ })).not.toHaveAccessibleName(/Actual: Estudio Norte/);
    await settle(other);

    // This tab stays where it was, with what was being edited, and says so.
    const banner = page.getByText(/en otra pestaña\. Esta sigue en Estudio Norte para no perder tus cambios/);
    await expect(banner).toBeVisible();
    await expect(page.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await expect(page.getByLabel(/Descripción/)).toHaveValue('Cambio sin guardar');
    await expect(page.getByRole('button', { name: /Cambiar de workspace/ })).toHaveAccessibleName(/Actual: Estudio Norte/);

    // Going there is the person's call, and it asks like any other way out.
    await page.getByRole('button', { name: /^Cambiar a / }).click();
    const dialog = page.getByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Seguir editando' }).click();
    await expect(page.getByLabel(/Descripción/)).toHaveValue('Cambio sin guardar');
    await page.getByRole('button', { name: /^Cambiar a / }).click();
    await dialog.getByRole('button', { name: 'Descartar' }).click();
    await expect(page).toHaveURL(/\/proyectos$/);
    await expect(page.getByRole('button', { name: /Cambiar de workspace/ })).not.toHaveAccessibleName(/Actual: Estudio Norte/);
    await expect(banner).toHaveCount(0);
  });

  test('una pestaña que abre directo en el editor: sin Atrás que atajar, los links siguen preguntando', async ({ context, page }) => {
    await useScenario(page, 'estudio-norte');
    await page.goto('/brand-kits');
    await settle(page);
    const href = await page.getByRole('link', { name: 'Estudio Norte', exact: true }).getAttribute('href');
    const [tab] = await Promise.all([context.waitForEvent('page'), page.evaluate((url) => void window.open(url ?? '', '_blank'), href)]);
    await tab.waitForLoadState();
    await settle(tab);
    expect(await tab.evaluate(() => window.history.length)).toBe(1);
    await tab.getByLabel(/Descripción/).fill('Otra descripción');
    await expect(tab.getByRole('region', { name: 'Acciones' })).toBeVisible();
    // No extra entry: there is nothing before this screen, so "Descartar" after Back would have nowhere to go.
    expect(await tab.evaluate(() => window.history.length)).toBe(1);
    // Still guarded: closing the tab warns, and a link asks.
    expect(await tab.evaluate(() => !window.dispatchEvent(new Event('beforeunload', { cancelable: true })))).toBe(true);
    await tab.getByRole('navigation', { name: 'Secciones', exact: true }).getByRole('link', { name: 'Publicaciones' }).click();
    const dialog = tab.getByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Descartar' }).click();
    await expect(tab).toHaveURL(/\/publicaciones$/);
    // One step back is the kit, clean and not asking.
    await tab.goBack();
    await expect(tab.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await expect(tab.getByLabel(/Descripción/)).not.toHaveValue('Otra descripción');
  });

  test('en el teléfono: un link del panel de notificaciones pregunta, y al descartar el panel no queda abierto', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'estudio-norte');
    await page.goto('/brand-kits');
    await settle(page);
    await page.getByRole('link', { name: 'Estudio Norte', exact: true }).click();
    await expect(page.getByRole('heading', { level: 1, name: 'Estudio Norte' })).toBeVisible();
    await page.getByLabel(/Descripción/).fill('Otra descripción');
    await page.getByRole('button', { name: /Menú de cuenta/ }).click();
    await page.getByRole('menuitem', { name: /Notificaciones/ }).first().click();
    const drawer = page.getByRole('dialog', { name: 'Notificaciones' });
    await expect(drawer).toBeVisible();
    await drawer.getByRole('link', { name: 'Avisos por email' }).click();
    const dialog = page.getByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Descartar' }).click();
    await expect(page).toHaveURL(/\/perfil\/notificaciones$/);
    await expect(drawer).toBeHidden();
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
