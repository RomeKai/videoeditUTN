import { expect, test, type Page } from '@playwright/test';
import { EXAMPLE_IDS } from '../src/dev/screens';
import { expectClean, settle, useScenario as pickScenario, watch } from './helpers';

const READY = `/proyectos/${EXAMPLE_IDS.projectReady}`;
const SINGLE = `/proyectos/${EXAMPLE_IDS.projectSingleEdit}`;
/** Ids of the «todos-los-estados» scenario (src/api/mocks/seed.ts · allStateProjects). */
const project = (hex: string) => `/proyectos/00000007-0000-4000-8000-${hex.padStart(12, '0')}`;
const STATES = {
  processing: project('1'),
  waiting: project('19'),
  failedNoSpeech: project('22'),
  noBalance: project('29'),
  singleRendering: project('1c'),
};

async function open(page: Page, scenario: string, path: string) {
  await pickScenario(page, scenario);
  await page.goto(path);
  await expect(page.getByRole('navigation', { name: 'Secciones del proyecto' })).toBeVisible();
  await settle(page);
}

test.describe('Proyecto', () => {
  test('listo: del Resumen a Clips, elegir dos y abrir Publicar', async ({ page }) => {
    const problems = watch(page);
    await open(page, 'estudio-norte', READY);
    await expect(page.getByRole('heading', { level: 1, name: 'Vlog Mendoza' })).toBeVisible();
    await expect(page.getByText('6 clips listos.', { exact: true })).toBeVisible();
    await page.getByRole('link', { name: 'Ver clips' }).click();
    await expect(page).toHaveURL(new RegExp(`${READY}/clips$`));
    // On the Clips tab the header has no primary action.
    await expect(page.getByRole('link', { name: 'Ver clips' })).toHaveCount(0);
    await expect(page.getByRole('listitem')).toHaveCount(6);

    await page.getByRole('button', { name: 'Seleccionar' }).click();
    const bar = page.getByRole('toolbar', { name: 'Acciones para los clips seleccionados' });
    await page.getByRole('checkbox').nth(0).check({ force: true });
    await page.getByRole('checkbox').nth(1).check({ force: true });
    await expect(bar).toContainText('2 seleccionados');
    await bar.getByRole('button', { name: 'Publicar' }).click();
    await expect(page).toHaveURL(/\/clips\?publicar=[0-9a-f-]{36},[0-9a-f-]{36}$/);
    expectClean(problems, 'Clips');
  });

  test('una tarjeta de clip: descargar y abrir el editor', async ({ page }) => {
    await open(page, 'estudio-norte', `${READY}/clips`);
    const card = page.getByRole('listitem').first();
    await card.hover();
    const download = page.waitForEvent('download');
    await card.getByRole('button', { name: /^Descargar «/ }).click();
    expect((await download).suggestedFilename()).toMatch(/\.mp4$/);
    await expect(page.getByText('Clip descargado')).toBeVisible();
    await card.hover();
    await card.getByRole('link', { name: /^Editar «/ }).click();
    await expect(page).toHaveURL(new RegExp(`${READY}/clips/[0-9a-f-]{36}$`));
  });

  test('transcripción: buscar y descargar el .srt', async ({ page }) => {
    const problems = watch(page);
    await open(page, 'estudio-norte', `${READY}/transcripcion`);
    await page.keyboard.press('/');
    const search = page.getByRole('searchbox', { name: 'Buscar en la transcripción' });
    await expect(search).toBeFocused();
    await search.fill('clientes');
    await expect(page.getByText(/^1 de \d+$/)).toBeVisible();
    await search.press('Enter');
    await expect(page.getByText(/^2 de \d+$/)).toBeVisible();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Descargar .srt' }).click();
    expect((await download).suggestedFilename()).toBe('vlog-mendoza.srt');
    expectClean(problems, 'Transcripción');
  });

  test('actividad: movimientos del proyecto y cómo se calculó', async ({ page }) => {
    const problems = watch(page);
    await open(page, 'estudio-norte', `${READY}/actividad`);
    await expect(page.getByRole('table', { name: 'Movimientos de tokens del proyecto' })).toBeVisible();
    await expect(page.getByText('Total cobrado')).toBeVisible();
    expectClean(problems, 'Actividad');
  });

  test('video completo: volver a renderizar con otro estilo pasa por el costo', async ({ page }) => {
    const problems = watch(page);
    await open(page, 'estudio-norte', `${SINGLE}/resultado`);
    await expect(page.getByRole('button', { name: 'Descargar' })).toBeVisible();
    await page.getByRole('button', { name: /Volver a renderizar con otro estilo/ }).click();
    const style = page.getByRole('dialog', { name: 'Volver a renderizar con otro estilo' });
    await style.getByLabel('Estilo').selectOption('hormozi');
    await style.getByRole('button', { name: 'Ver el costo' }).click();
    const cost = page.getByRole('dialog', { name: 'Volver a renderizar' });
    await expect(cost.getByText('Saldo después')).toBeVisible();
    await cost.getByRole('button', { name: 'Renderizar' }).click();
    await expect(page.getByText('Estamos editando tu video…')).toBeVisible();
    await expect(page.getByText('Renderizando', { exact: true })).toBeVisible();
    // The mock finishes the render by itself and the screen follows it.
    await expect(page.getByRole('button', { name: 'Descargar' })).toBeVisible({ timeout: 45_000 });
    expectClean(problems, 'Resultado');
  });

  test('todos los estados: procesando, en espera, error y renderizado detenido', async ({ page }) => {
    const problems = watch(page);
    await open(page, 'todos-los-estados', STATES.processing);
    await expect(page.getByText(/Paso 2 de 4 · faltan ≈/)).toBeVisible();
    await expect(page.getByText('Se actualiza cada 5 s')).toBeVisible();

    await page.goto(STATES.waiting);
    await expect(page.getByText('Hay mucha demanda. Tu video sigue en la fila y arranca solo; no tenés que hacer nada.')).toBeVisible();

    await page.goto(STATES.failedNoSpeech);
    const alert = page.getByRole('alert');
    await expect(alert.getByText('No encontramos voz en el video. OneCreator elige clips a partir de lo que se dice.')).toBeVisible();
    await expect(alert.getByText(/Te devolvimos/)).toBeVisible();
    await expect(alert.getByRole('link', { name: 'Crear de nuevo' })).toBeVisible();

    await page.goto(STATES.noBalance);
    await expect(page.getByText('No alcanzó el saldo para renderizar sin revisar')).toBeVisible();
    await page.getByRole('link', { name: 'Revisar clips' }).click();
    await expect(page).toHaveURL(/\/revision$/);

    await page.goto(`${STATES.singleRendering}/resultado`);
    await expect(page.getByText('Estamos editando tu video…')).toBeVisible();
    expectClean(problems, 'todos los estados');
  });

  test('rol Lector: ve y descarga; lo demás queda deshabilitado', async ({ page }) => {
    await open(page, 'rol-lector', `${READY}/clips`);
    await expect(page.getByRole('button', { name: 'Seleccionar' })).toHaveAttribute('aria-disabled', 'true');
    await expect(page.getByRole('button', { name: /Más acciones: renombrar/ })).toHaveAttribute('aria-disabled', 'true');
    const card = page.getByRole('listitem').first();
    await card.hover();
    await expect(card.getByRole('button', { name: 'Publicar' })).toHaveAttribute('aria-disabled', 'true');
    await expect(card.getByRole('button', { name: /^Descargar «/ })).toBeEnabled();
  });

  test('en el teléfono: las acciones del clip suben en una hoja', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    const problems = watch(page);
    await open(page, 'estudio-norte', `${READY}/clips`);
    await page.getByRole('button', { name: /^Acciones de «/ }).first().click();
    const sheet = page.getByRole('dialog');
    await expect(sheet.getByRole('button', { name: 'Publicar' })).toBeVisible();
    await expect(sheet.getByRole('button', { name: /Descargar/ })).toBeVisible();
    await expect(sheet.getByRole('link', { name: 'Editar' })).toBeVisible();
    await sheet.getByRole('button', { name: 'Copiar link' }).click();
    await expect(sheet).toBeHidden();
    // No horizontal scroll on the phone.
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    expectClean(problems, 'Clips en el teléfono');
  });
});
