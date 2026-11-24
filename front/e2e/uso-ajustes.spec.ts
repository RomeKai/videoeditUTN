import { expect, test } from '@playwright/test';
import { expectClean, settle, useScenario, watch } from './helpers';

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
    await page.getByRole('link', { name: 'Ver movimiento' }).click();
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
    await expect(page).toHaveURL(/\/uso\/comprar$/);
  });

  test('movimientos: filtrar por tipo y cambiar de mes', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/uso/movimientos');
    await settle(page);
    const table = page.getByRole('table', { name: /Movimientos de/ });
    await expect(table.getByText('Cobrado').first()).toBeVisible();
    await page.getByRole('group', { name: 'Filtrar por tipo' }).getByRole('button', { name: /^Reservados/ }).click();
    await expect(table.getByText('Cobrado')).toHaveCount(0);
    await expect(table.getByText('Se cobra al terminar').first()).toBeVisible();
    await page.getByLabel('Filtrar por mes').selectOption({ label: 'Todos los meses' });
    await expect(page.getByText(/movimientos? en todos los meses/)).toBeVisible();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Exportar CSV' }).click();
    expect((await download).suggestedFilename()).toBe('movimientos-todos.csv');
    expectClean(problems, 'Movimientos');
  });

  test('planes: bajar muestra qué se pierde y queda programado', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/uso/planes');
    await settle(page);
    await expect(page.getByRole('article', { name: 'Plan Pro, tu plan' })).toBeVisible();
    await page.getByRole('button', { name: 'Bajar a Creador' }).click();
    const dialog = page.getByRole('alertdialog', { name: '¿Bajar a Creador?' });
    await expect(dialog.getByText('Publicar en varias redes a la vez')).toBeVisible();
    await dialog.getByRole('button', { name: 'Bajar a Creador' }).click();
    await expect(page.getByRole('article', { name: 'Plan Creador' }).getByText('Próximo plan')).toBeVisible();
    await page.getByRole('link', { name: 'Resumen' }).click();
    await expect(page.getByRole('region', { name: 'Plan actual' })).toContainText('Pasa a Creador el');
    expectClean(problems, 'Planes');
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
    await page.goto('/uso/movimientos');
    await expect(page.getByRole('heading', { name: /No tenés acceso a esto/ })).toBeVisible();
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
    const nav = page.getByRole('navigation', { name: 'Secciones de ajustes' });
    await expect(nav.getByRole('link', { name: /Miembros/ })).toContainText('4 de 5 miembros · 1 invitación pendiente');
    await nav.getByRole('link', { name: /Miembros/ }).click();
    await expect(page).toHaveURL(/\/ajustes\/miembros$/);
    await settle(page);
    const members = page.getByRole('link', { name: /Miembros/ }).last();
    const general = page.getByLabel('Secciones de ajustes').getByRole('link', { name: /General/ });
    const list = page.getByRole('list').filter({ hasText: 'juan@estudionorte.com' });
    await expect(list).toBeVisible();
    const [generalBox, membersBox, listBox] = await Promise.all([general.boundingBox(), members.boundingBox(), list.boundingBox()]);
    expect(generalBox && membersBox && listBox && generalBox.y < membersBox.y && membersBox.y < listBox.y).toBe(true);
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
