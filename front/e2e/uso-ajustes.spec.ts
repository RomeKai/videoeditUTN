import { expect, test } from '@playwright/test';
import { expectClean, settle, useScenario, watch } from './helpers';

/**
 * These flows must not depend on the day they run: seeded charges are at least a day old, so on
 * the 1st of a month they belong to the previous one. `E2E_NOW=2026-12-01T04:00:00Z` runs the
 * whole file with the browser's clock starting at that moment (time keeps flowing from there).
 */
test.beforeEach(async ({ page }) => {
  if (process.env.E2E_NOW) await page.clock.install({ time: new Date(process.env.E2E_NOW) });
});

test.describe('Uso y facturación', () => {
  test('comprar tokens: paquete → checkout → compra acreditada y saldo actualizado', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/uso');
    await settle(page);
    await expect(page.getByRole('region', { name: 'Resumen de tokens y plan' }).getByText('4.860')).toBeVisible();
    await page.getByRole('region', { name: 'Resumen de tokens y plan' }).getByRole('link', { name: 'Comprar tokens' }).click();
    await expect(page).toHaveURL(/\/uso\/comprar$/);
    await settle(page);

    const packs = page.getByRole('radiogroup', { name: 'Elegí cuántos tokens comprar' });
    await expect(packs.getByRole('radio').nth(1)).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByRole('region', { name: 'Resumen de la compra' })).toContainText('9.860 tokens');
    await page.getByRole('button', { name: 'Ir al pago' }).click();

    await expect(page).toHaveURL(/\/dev\/checkout\?compra=/);
    await page.getByRole('button', { name: 'Pagar', exact: true }).click();

    await expect(page).toHaveURL(/\/uso\/comprar\/resultado\?compra=/);
    await expect(page.getByText('Estamos confirmando tu pago.')).toBeVisible();
    await expect(page.getByText('Compra acreditada:').first()).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole('link', { name: /Saldo: 9\.860 tokens disponibles/ })).toBeVisible();
    // Reloading the result does not announce the same purchase again.
    await page.reload();
    await settle(page);
    await expect(page.getByRole('region', { name: 'Resumen de tokens y plan' }).getByText('9.860')).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Uso y facturación' }).getByRole('link', { name: 'Resumen' })).toHaveAttribute('aria-current', 'page');
    await expect(page.getByRole('link', { name: 'Ver movimiento' })).toHaveCount(0);
    await page.getByRole('navigation', { name: 'Uso y facturación' }).getByRole('link', { name: 'Movimientos' }).click();
    await expect(page).toHaveURL(/\/uso\/movimientos$/);
    await settle(page);
    await expect(page.getByRole('table', { name: /Movimientos de/ }).getByText('Compra de 5.000 tokens').first()).toBeVisible();
    expectClean(problems, 'la compra de tokens');
  });

  test('pago rechazado: el aviso queda sobre el Resumen y el saldo no cambia', async ({ page }) => {
    await useScenario(page, 'pago-rechazado');
    await page.goto('/uso/comprar');
    await settle(page);
    await page.getByRole('button', { name: 'Ir al pago' }).click();
    await page.getByRole('button', { name: 'Pagar', exact: true }).click();
    const alert = page.getByRole('alert').filter({ hasText: 'El pago no se completó' });
    await expect(alert).toBeVisible({ timeout: 20_000 });
    await expect(alert).toContainText('No se te cobró nada.');
    await expect(alert).toContainText('Tu saldo sigue en 4.860 tokens.');
    await expect(page.getByRole('link', { name: /Saldo: 4\.860 tokens disponibles/ })).toBeVisible();
    await alert.getByRole('link', { name: 'Reintentar' }).click();
    await expect(page).toHaveURL(/\/uso\/comprar\?tokens=5000$/);
    await expect(page.getByRole('radiogroup').getByRole('radio').nth(1)).toHaveAttribute('aria-checked', 'true');
  });

  test('movimientos: filtrar por tipo y cambiar de mes', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/uso/movimientos');
    await settle(page);
    // All months first: which month a seeded charge falls in depends on the day the test runs.
    await page.getByLabel('Filtrar por mes').selectOption({ label: 'Todos los meses' });
    const table = page.getByRole('table', { name: 'Movimientos de todos los meses' });
    await expect(table.getByText('Cobrado').first()).toBeVisible();
    await expect(page.getByText(/movimientos? en todos los meses/)).toBeVisible();
    await page.getByRole('group', { name: 'Filtrar por tipo' }).getByRole('button', { name: /^Reservados/ }).click();
    await expect(table.getByText('Cobrado')).toHaveCount(0);
    await expect(table.getByText('Se cobra al terminar').first()).toBeVisible();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Exportar CSV' }).click();
    expect((await download).suggestedFilename()).toBe('movimientos-todos.csv');
    expectClean(problems, 'Movimientos');
  });

  test('planes: bajar muestra qué se pierde, queda programado y se puede cancelar', async ({ page }) => {
    await useScenario(page, 'plan-estudio');
    const problems = watch(page);
    await page.goto('/uso/planes');
    await settle(page);
    await expect(page.getByRole('article', { name: 'Plan Estudio, tu plan' })).toBeVisible();
    await page.getByRole('button', { name: 'Bajar a Pro' }).click();
    const dialog = page.getByRole('alertdialog', { name: '¿Bajar a Pro?' });
    await expect(dialog.getByText('Miembros: 5 (hoy 15)')).toBeVisible();
    await dialog.getByRole('button', { name: 'Bajar a Pro' }).click();
    await expect(page.getByRole('article', { name: /^Plan Pro/ }).getByText('Próximo plan')).toBeVisible();
    await page.getByRole('link', { name: 'Resumen' }).click();
    await expect(page.getByRole('region', { name: 'Plan actual' })).toContainText('Pasa a Pro el');
    await page.getByRole('region', { name: 'Plan actual' }).getByRole('link', { name: 'Cambiar plan' }).click();
    await page.getByRole('button', { name: 'Seguir con Estudio' }).click();
    await expect(page.getByText('Listo: seguís con el plan Estudio.')).toBeVisible();
    await expect(page.getByText('Próximo plan')).toHaveCount(0);
    expectClean(problems, 'Planes');
  });

  test('planes: con menos lugares que los ocupados no deja bajar', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/uso/planes');
    await settle(page);
    await page.getByRole('button', { name: 'Bajar a Creador' }).click();
    const dialog = page.getByRole('alertdialog', { name: 'Todavía no podés bajar a Creador' });
    await expect(dialog).toContainText('Hoy ocupás 4 lugares');
    await dialog.getByRole('link', { name: 'Ir a Miembros' }).click();
    await expect(page).toHaveURL(/\/ajustes\/miembros$/);
    expectClean(problems, 'Planes con lugares de más');
  });

  test('planes: subir con pago pasa por el checkout y al volver el plan ya cambió', async ({ page }) => {
    await useScenario(page, 'plan-con-checkout');
    const problems = watch(page);
    await page.goto('/uso/planes');
    await settle(page);
    await page.getByRole('button', { name: 'Subir a Estudio' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Subir a Estudio' }).click();
    await expect(page).toHaveURL(/\/dev\/checkout\?compra=/);
    await expect(page.getByText('Plan Estudio')).toBeVisible();
    await page.getByRole('button', { name: 'Pagar', exact: true }).click();
    await expect(page).toHaveURL(/\/uso\/planes\?compra=/);
    await expect(page.getByText('Listo: ya estás en el plan Estudio')).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole('article', { name: 'Plan Estudio, tu plan' })).toBeVisible();
    expectClean(problems, 'Planes con checkout');

    // And a payment that does not go through leaves the plan as it was.
    await page.getByRole('button', { name: 'Cerrar aviso' }).click();
    await expect(page).toHaveURL(/\/uso\/planes$/);
  });

  test('rol Editor: solo el saldo, con las otras pestañas deshabilitadas (R1)', async ({ page }) => {
    await useScenario(page, 'rol-editor');
    await page.goto('/uso');
    await settle(page);
    await expect(page.getByText('El saldo de tokens de Estudio Norte.')).toBeVisible();
    const tabs = page.getByRole('navigation', { name: 'Uso y facturación' });
    await expect(tabs.getByRole('link', { name: 'Resumen' })).toHaveAttribute('aria-current', 'page');
    await expect(tabs.locator('[aria-disabled="true"]')).toHaveCount(3);
    await expect(page.getByRole('button', { name: 'Comprar tokens' })).toHaveAttribute('aria-disabled', 'true');
    await expect(page.getByText('Martín Sosa')).toBeVisible();
    for (const path of ['/uso/movimientos', '/uso/planes', '/uso/comprar']) {
      await page.goto(path);
      await expect(page.getByRole('heading', { name: /No tenés acceso a esto/ })).toBeVisible();
    }
  });

  test('saldo bajo y teléfono: aviso arriba y tarjetas apiladas (M9)', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'saldo-bajo');
    const problems = watch(page);
    await page.goto('/uso');
    await settle(page);
    await expect(page.getByText('Te queda poco saldo')).toBeVisible();
    await expect(page.getByRole('list', { name: 'Total del mes por tipo' }).first()).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expectClean(problems, 'Uso en el teléfono');
  });

  test('precios es pública y lleva a crear la cuenta', async ({ page }) => {
    await useScenario(page, 'sin-sesion');
    const problems = watch(page);
    await page.goto('/precios');
    await settle(page);
    await expect(page.getByRole('article')).toHaveCount(4);
    await expect(page.getByRole('article', { name: 'Plan Pro, recomendado' })).toBeVisible();
    await page.getByRole('link', { name: 'Elegir Creador' }).click();
    await expect(page).toHaveURL(/\/registro$/);
    expectClean(problems, 'Precios');
  });
});

test.describe('Ajustes y cuenta', () => {
  test('miembros: invitar ocupa el último lugar y deshabilita «Invitar»', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/ajustes');
    await settle(page);
    await page.getByRole('navigation', { name: 'Secciones de ajustes' }).getByRole('link', { name: /Miembros/ }).click();
    await expect(page).toHaveURL(/\/ajustes\/miembros$/);
    await settle(page);
    await page.getByRole('button', { name: 'Invitar' }).click();
    const dialog = page.getByRole('dialog', { name: 'Invitar a Estudio Norte' });
    await dialog.getByLabel('Emails').fill('juan@ejemplo.com');
    await dialog.getByLabel('Rol').selectOption({ label: 'Admin' });
    await dialog.getByRole('button', { name: 'Enviar invitación' }).click();
    await expect(page.getByText('Invitación enviada a juan@ejemplo.com')).toBeVisible();
    const row = page.getByRole('row').filter({ hasText: 'juan@ejemplo.com' });
    await expect(row.getByText('Invitación pendiente')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Invitar' })).toHaveAttribute('aria-disabled', 'true');
    await expect(page.getByText(/La invitación pendiente ocupa el último lugar del plan Pro/)).toBeVisible();

    await page.getByRole('button', { name: 'Rol de Carla Ruiz: Editor. Cambiar' }).click();
    await page.getByRole('menuitemradio', { name: 'Lector' }).click();
    await expect(page.getByRole('button', { name: 'Rol de Carla Ruiz: Lector. Cambiar' })).toBeVisible();
    expectClean(problems, 'Miembros');
  });

  test('redes: conectar abre la ventana de la red y vuelve conectada', async ({ page }) => {
    await useScenario(page, 'redes-vencidas');
    const problems = watch(page);
    await page.goto('/ajustes/redes');
    await settle(page);
    await expect(page.getByText('Vencida', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Reconectar TikTok' }).click();
    const dialog = page.getByRole('dialog', { name: 'Conectar TikTok' });
    const popupPromise = page.waitForEvent('popup');
    await dialog.getByRole('button', { name: 'Continuar en TikTok' }).click();
    const popup = await popupPromise;
    await popup.getByRole('button', { name: 'Autorizar' }).click();
    await expect(page.getByRole('dialog', { name: 'TikTok quedó conectada' })).toBeVisible({ timeout: 15_000 });
    await page.getByRole('button', { name: 'Listo' }).click();
    await expect(page.getByRole('button', { name: 'Desconectar TikTok' })).toBeVisible();
    await expect(page.getByText('Vencida', { exact: true })).toHaveCount(0);
    expectClean(problems, 'Redes sociales');
  });

  test('general: renombrar el workspace actualiza la barra', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/ajustes');
    await settle(page);
    await expect(page.getByRole('heading', { name: 'Zona de peligro' })).toBeVisible();
    await page.getByLabel('Nombre del workspace').fill('Estudio Sur');
    await page.getByRole('button', { name: 'Guardar' }).click();
    await expect(page.getByRole('button', { name: /Cambiar de workspace. Actual: Estudio Sur/ })).toBeVisible();
    await expect(page.getByText('No hay cambios para guardar.')).toBeVisible();
    expectClean(problems, 'Ajustes › General');
  });

  test('rol Editor: Ajustes solo muestra General y no deja entrar a Miembros', async ({ page }) => {
    await useScenario(page, 'rol-editor');
    await page.goto('/ajustes');
    await settle(page);
    await expect(page.getByRole('navigation', { name: 'Secciones de ajustes' }).getByRole('link')).toHaveCount(1);
    await expect(page.getByLabel('Nombre del workspace')).toBeDisabled();
    await expect(page.getByRole('heading', { name: 'Zona de peligro' })).toHaveCount(0);
    await page.goto('/ajustes/miembros');
    await expect(page.getByRole('heading', { name: /No tenés acceso a esto/ })).toBeVisible();
  });

  test('teléfono: Ajustes es una lista y la sección abierta queda debajo de su fila (M10)', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'miembros-al-limite');
    const problems = watch(page);
    await page.goto('/ajustes');
    await settle(page);
    const membersRow = page.getByRole('link', { name: /Miembros/ }).filter({ hasText: 'invitación pendiente' });
    await expect(membersRow).toContainText('4 de 5 miembros · 1 invitación pendiente');
    await membersRow.click();
    await expect(page).toHaveURL(/\/ajustes\/miembros$/);
    await settle(page);
    const general = page.getByRole('link', { name: /General/ });
    const networks = page.getByRole('link', { name: /Redes sociales/ });
    const list = page.getByRole('list').filter({ hasText: 'juan@estudionorte.com' });
    await expect(list).toBeVisible();
    const [generalBox, membersBox, listBox, networksBox] = await Promise.all([general.boundingBox(), membersRow.boundingBox(), list.boundingBox(), networks.boundingBox()]);
    expect(generalBox && membersBox && listBox && networksBox && generalBox.y < membersBox.y && membersBox.y < listBox.y && listBox.y < networksBox.y).toBe(true);
    // Focus follows what is seen: from the open row into its content, not to the next row.
    await membersRow.focus();
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => document.activeElement?.closest('nav') === null)).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expectClean(problems, 'Ajustes en el teléfono');
  });

  test('perfil: tema claro, notificaciones que se guardan solas y cerrar sesión', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/perfil');
    await settle(page);
    await page.getByRole('group', { name: 'Apariencia' }).getByRole('button', { name: 'Claro' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await page.getByRole('navigation', { name: 'Secciones de tu cuenta' }).getByRole('link', { name: /Notificaciones/ }).click();
    await settle(page);
    const toggle = page.getByRole('switch', { name: 'Email: El render terminó' });
    await toggle.click();
    await expect(page.getByText('Guardado. Ya no te avisamos por email cuando termina un render.')).toBeVisible();
    await page.reload();
    await settle(page);
    await expect(page.getByRole('switch', { name: 'Email: El render terminó' })).toHaveAttribute('aria-checked', 'false');
    await page.goto('/perfil');
    await settle(page);
    expectClean(problems, 'Perfil');
    await page.getByRole('button', { name: 'Cerrar sesión', exact: true }).click();
    await expect(page).toHaveURL(/\/login/);
  });
});
