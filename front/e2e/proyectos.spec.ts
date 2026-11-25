import { expect, test, type Page } from '@playwright/test';
import { expectClean, settle, useScenario as pickScenario, watch } from './helpers';

const PROJECT_URL = /\/proyectos\/[0-9a-f-]{36}$/;

/** A tiny file stands in for the video: in mock mode the name decides the duration ("NNmin") and the errors. */
const video = (name: string) => ({ name, mimeType: 'video/mp4', buffer: Buffer.from('video de prueba '.repeat(64)) });

async function openNew(page: Page, scenario: string, query = '') {
  await pickScenario(page, scenario);
  await page.goto(`/proyectos/nuevo${query}`);
  await settle(page);
  await expect(page.getByRole('heading', { level: 1, name: 'Nuevo proyecto' })).toBeVisible();
}

const summary = (page: Page) => page.getByRole('complementary', { name: 'Resumen del proyecto' });

/** Changes the mocked database behind the screen's back, to reach the answers the form normally prevents. */
async function patchDb(page: Page, patch: string) {
  await page.evaluate((code) => {
    const db = JSON.parse(window.localStorage.getItem('oc.mock.db') ?? '{}');
    new Function('db', code)(db);
    window.localStorage.setItem('oc.mock.db', JSON.stringify(db));
  }, patch);
}

test.describe('B2 · Proyectos', () => {
  test('lista, filtra, cambia a grilla y la recuerda', async ({ page }) => {
    await pickScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/proyectos');
    await settle(page);
    const table = page.getByRole('table', { name: 'Proyectos del workspace' });
    await expect(table.getByRole('row')).toHaveCount(9);
    await expect(table.getByRole('row', { name: /Podcast Ep\. 42/ }).getByText('Listo para revisar')).toBeVisible();

    await page.getByRole('group', { name: 'Filtrar por estado' }).getByRole('button', { name: /Para revisar/ }).click();
    await expect(table.getByRole('row')).toHaveCount(2);
    await page.getByRole('group', { name: 'Filtrar por estado' }).getByRole('button', { name: /Todos/ }).click();

    await page.getByRole('button', { name: 'Vista de grilla' }).click();
    await expect(page.getByRole('list', { name: 'Proyectos del workspace' }).getByRole('listitem')).toHaveCount(8);
    await page.reload();
    await settle(page);
    await expect(page.getByRole('list', { name: 'Proyectos del workspace' })).toBeVisible();
    await expect(page.getByRole('table')).toHaveCount(0);

    await page.getByRole('link', { name: 'Revisar clips' }).click();
    await expect(page).toHaveURL(/\/proyectos\/[0-9a-f-]{36}\/revision$/);
    expectClean(problems, 'Proyectos');
  });

  test('renombra y borra un proyecto desde su menú', async ({ page }) => {
    await pickScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/proyectos');
    await settle(page);
    await page.getByRole('button', { name: /Más acciones de Vlog Mendoza/ }).click();
    await page.getByRole('menuitem', { name: 'Renombrar' }).click();
    const dialog = page.getByRole('dialog', { name: 'Renombrar proyecto' });
    await dialog.getByRole('textbox', { name: 'Nombre' }).fill('Vlog Córdoba');
    await dialog.getByRole('button', { name: 'Guardar' }).click();
    await expect(page.getByRole('link', { name: /Vlog Córdoba/ })).toBeVisible();

    await page.getByRole('button', { name: /Más acciones de Vlog Córdoba/ }).click();
    await page.getByRole('menuitem', { name: 'Borrar' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Borrar proyecto' }).click();
    await expect(page.getByText('Proyecto borrado')).toBeVisible();
    await expect(page.getByRole('link', { name: /Vlog Córdoba/ })).toHaveCount(0);
    expectClean(problems, 'Proyectos · menú');
  });

  test('todos los estados de la sección 3 tienen su etiqueta', async ({ page }) => {
    await pickScenario(page, 'todos-los-estados');
    await page.goto('/proyectos');
    await settle(page);
    const table = page.getByRole('table', { name: 'Proyectos del workspace' });
    for (const label of ['En cola', 'Preparando el video', 'Transcribiendo', 'Revisando el contenido', 'Eligiendo los mejores momentos', 'En espera', 'Listo para revisar', 'Renderizando', 'Listo', 'Listo con errores', 'Error']) {
      await expect(table.getByText(label, { exact: true }).first()).toBeVisible();
    }
    await expect(page.getByText(/1–25 de \d+ proyectos/)).toBeVisible();
  });

  test('teléfono: tarjetas apiladas y filtros en un menú', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await pickScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/proyectos');
    await settle(page);
    await expect(page.getByText('8 proyectos · recientes primero')).toBeVisible();
    await expect(page.getByRole('table')).toHaveCount(0);
    const cards = page.getByRole('list', { name: 'Proyectos del workspace' }).getByRole('listitem');
    await expect(cards).toHaveCount(8);
    await page.getByRole('button', { name: 'Filtros' }).click();
    await page.getByRole('menu', { name: 'Filtros y orden' }).getByRole('menuitemradio', { name: /Listos/ }).click();
    await expect(cards).toHaveCount(3);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    expectClean(problems, 'Proyectos en el teléfono');
  });
});

test.describe('C1 · Nuevo proyecto', () => {
  test('crear desde un archivo abre el proyecto, que queda procesando, y recuerda la configuración', async ({ page }) => {
    const problems = watch(page);
    await openNew(page, 'estudio-norte');
    await page.getByLabel('Elegir video').setInputFiles(video('charla-de-ventas-20min.mp4'));
    await expect(page.getByText(/Entra en tu plan Pro/)).toBeVisible();
    await page.getByRole('radio', { name: /^Clase/ }).click();
    await page.getByRole('radio', { name: /1:1/ }).click();
    // 1.200 s × 0,5 × 0,9 = 540
    await expect(summary(page).getByTestId('cost-total')).toHaveText('540');
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    await expect(page).toHaveURL(PROJECT_URL);
    await expect(page.locator('h1').first()).toBeVisible();

    await page.goto('/proyectos');
    await settle(page);
    const row = page.getByRole('row', { name: /charla-de-ventas-20min/ });
    await expect(row).toBeVisible();
    await expect(row.getByText(/En cola|Preparando el video|Transcribiendo|Revisando el contenido|Eligiendo los mejores momentos/)).toBeVisible();
    await expect(page.getByRole('link', { name: /Saldo: 4\.320 tokens disponibles/ })).toBeVisible();

    await page.goto('/proyectos/nuevo');
    await settle(page);
    const last = page.getByRole('radio', { name: /Mi última configuración/ });
    await expect(last).toHaveAttribute('aria-checked', 'true');
    await expect(last).toContainText('Minimalista · Ajustar');
    await expect(page.getByRole('radio', { name: /1:1/ })).toHaveAttribute('aria-checked', 'true');
    expectClean(problems, 'crear desde un archivo');
  });

  test('«Duplicar configuración» abre Nuevo proyecto con la del proyecto', async ({ page }) => {
    await pickScenario(page, 'estudio-norte');
    await page.goto('/proyectos');
    await settle(page);
    await page.getByRole('button', { name: /Más acciones de Vlog Mendoza/ }).click();
    await page.getByRole('menuitem', { name: 'Duplicar configuración' }).click();
    await expect(page).toHaveURL(/\/proyectos\/nuevo\?desde=/);
    await expect(page.getByRole('radio', { name: /Como «Vlog Mendoza»/ })).toHaveAttribute('aria-checked', 'true');
    await expect(page.getByText(/Valores de «Vlog Mendoza»/)).toBeVisible();
  });

  test('un error de subida de la sección 4 se muestra junto al archivo', async ({ page }) => {
    await openNew(page, 'estudio-norte');
    await page.getByLabel('Elegir video').setInputFiles(video('muy-chico-5min.mp4'));
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    const row = page.getByTestId('file-row');
    await expect(row.getByRole('alert')).toHaveText('La resolución del video es muy baja (mínimo 240 px). Probá con una versión de mejor calidad.');
    await expect(page).toHaveURL(/\/proyectos\/nuevo$/);
    await expect(summary(page).getByRole('button', { name: 'Crear proyecto' })).toBeDisabled();

    await page.getByLabel('Elegir otro video').setInputFiles(video('ilegible-5min.mp4'));
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    await expect(row.getByRole('alert')).toHaveText('No pudimos leer el video. Probá exportarlo de nuevo en MP4.');

    await page.getByLabel('Elegir otro video').setInputFiles({ name: 'guion.pdf', mimeType: 'application/pdf', buffer: Buffer.from('pdf') });
    await expect(row.getByRole('alert')).toHaveText('Este archivo no es un video.');
  });

  test('pegar link: lo reconoce, crea el proyecto y lo abre', async ({ page }) => {
    const problems = watch(page);
    await openNew(page, 'estudio-norte', '?origen=link');
    await expect(page.getByRole('tab', { name: /Pegar link/ })).toHaveAttribute('aria-selected', 'true');
    await page.getByRole('textbox', { name: 'Link del video' }).fill('https://www.youtube.com/watch?v=k3Vn8sQx2Lw');
    await expect(page.getByTestId('link-row')).toContainText('YouTube · 45:00');
    await expect(page.getByRole('radio', { name: /Editar el video completo/ })).toHaveAttribute('aria-disabled', 'true');
    await expect(page.getByText('Para editar el video completo, subí el archivo')).toBeVisible();
    await expect(summary(page).getByTestId('cost-total')).toHaveText('10');
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    await expect(page).toHaveURL(PROJECT_URL);
    expectClean(problems, 'crear desde un link');
  });

  test('un link privado se explica debajo del campo', async ({ page }) => {
    await openNew(page, 'estudio-norte', '?origen=link');
    await page.getByRole('textbox', { name: 'Link del video' }).fill('https://vimeo.com/privado-123');
    await expect(page.getByText('No pudimos acceder a ese video: es privado, no existe o pide iniciar sesión.')).toBeVisible();
    await expect(summary(page).getByRole('button', { name: 'Crear proyecto' })).toBeDisabled();
  });

  test('desde un pedido de ejemplo: describir con IA manda el texto y abre el proyecto', async ({ page }) => {
    const problems = watch(page);
    await pickScenario(page, 'workspace-vacio');
    await page.goto('/proyectos');
    await settle(page);
    await page.getByRole('link', { name: /Top 3 consejos/ }).click();
    await expect(page).toHaveURL(/\/proyectos\/nuevo\?origen=ia&prompt=/);
    await expect(page.getByRole('tab', { name: /Describir con IA/ })).toHaveAttribute('aria-selected', 'true');
    const prompt = page.getByRole('textbox', { name: 'Contanos qué querés' });
    await expect(prompt).toHaveValue(/Encontrá los 3 mejores consejos/);

    await page.getByLabel('Elegir video').setInputFiles(video('episodio-30min.mp4'));
    await expect(summary(page).getByText('Entre 810 y 2.430')).toBeVisible();
    await prompt.fill('');
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    await expect(page.getByRole('region', { name: 'Origen' }).getByRole('alert')).toHaveText('Contanos qué querés');
    await prompt.fill('Sacá los 3 mejores consejos, en vertical');
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    await expect(page).toHaveURL(PROJECT_URL);
    expectClean(problems, 'crear con IA');
  });

  test('402 al crear abre «Saldo insuficiente» (D2)', async ({ page }) => {
    await openNew(page, 'estudio-norte');
    await page.getByLabel('Elegir video').setInputFiles(video('charla-20min.mp4'));
    await expect(summary(page).getByTestId('cost-total')).toHaveText('540');
    // Someone else spent the balance after the screen read it.
    await patchDb(page, 'for (const wallet of db.wallets) wallet.available_balance = 100;');
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    const dialog = page.getByRole('alertdialog', { name: 'No te alcanza el saldo' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText('Necesitás 540 tokens');
    await expect(page).toHaveURL(/\/proyectos\/nuevo$/);
  });

  test('403 por plan abre «Límite del plan» (D3) y «Cambiar opciones» marca el campo', async ({ page }) => {
    await openNew(page, 'estudio-norte');
    await page.getByLabel('Elegir video').setInputFiles(video('charla-20min.mp4'));
    await page.getByRole('radio', { name: /1080p/ }).click();
    await expect(summary(page).getByTestId('cost-total')).toHaveText('810');
    // The workspace went down to the free plan after the screen read it.
    await patchDb(page, 'const free = db.plans.find((plan) => plan.name === "Gratis"); for (const workspace of db.workspaces) workspace.subscription_plan = free.id;');
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    const dialog = page.getByRole('alertdialog');
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Cambiar opciones' }).click();
    await expect(dialog).toBeHidden();
    await expect(page.getByTestId('file-row').getByRole('alert')).toContainText('El video dura más de lo que permite tu plan');
  });

  test('saldo bajo: el resumen ofrece comprar tokens', async ({ page }) => {
    await openNew(page, 'saldo-bajo');
    await page.getByLabel('Elegir video').setInputFiles(video('charla-20min.mp4'));
    await expect(summary(page).getByText('Te faltan')).toBeVisible();
    await summary(page).getByRole('link', { name: 'Comprar tokens' }).click();
    await expect(page).toHaveURL(/\/uso\/comprar$/);
  });

  test('plan Gratis: candados, marca de agua y videos de hasta 10 min', async ({ page }) => {
    const problems = watch(page);
    await openNew(page, 'plan-gratis');
    await expect(page.getByRole('radio', { name: /1080p, disponible en el plan Creador/ })).toHaveAttribute('aria-disabled', 'true');
    await expect(summary(page).getByText('Tu plan agrega marca de agua')).toBeVisible();
    await page.getByLabel('Elegir video').setInputFiles(video('clase-30min.mp4'));
    await expect(page.getByTestId('file-row').getByRole('alert')).toHaveText('El video dura más de lo que permite tu plan (máx. 10 min).');
    await page.getByLabel('Elegir otro video').setInputFiles(video('primer-video-8min.mp4'));
    await expect(summary(page).getByTestId('cost-total')).toHaveText('240');
    await summary(page).getByRole('button', { name: 'Crear proyecto' }).click();
    await expect(page).toHaveURL(PROJECT_URL);
    expectClean(problems, 'plan Gratis');
  });

  test('teléfono: una columna, con el costo y el botón fijos abajo', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const problems = watch(page);
    await openNew(page, 'estudio-norte');
    const bar = page.getByRole('region', { name: 'Costo y creación del proyecto' });
    await expect(bar).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Resumen del proyecto' })).toHaveCount(0);
    await page.getByLabel('Elegir video').setInputFiles(video('charla-20min.mp4'));
    await expect(bar.getByRole('button', { name: /Costo estimado: 540 tokens/ })).toBeVisible();
    await bar.getByRole('button', { name: /Costo estimado/ }).click();
    await expect(bar.getByText('Saldo disponible')).toBeVisible();
    const box = await bar.boundingBox();
    expect(Math.round((box?.y ?? 0) + (box?.height ?? 0))).toBe(844);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await bar.getByRole('button', { name: 'Crear proyecto' }).click();
    await expect(page).toHaveURL(PROJECT_URL);
    expectClean(problems, 'Nuevo proyecto en el teléfono');
  });
});
