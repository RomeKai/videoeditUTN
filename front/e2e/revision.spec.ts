import { expect, test } from '@playwright/test';
import { EXAMPLE_IDS } from '../src/dev/screens';
import { expectClean, settle, useScenario, watch } from './helpers';

const REVIEW = `/proyectos/${EXAMPLE_IDS.projectToReview}/revision`;
const EDITOR = `/proyectos/${EXAMPLE_IDS.projectReady}/clips/${EXAMPLE_IDS.clip}`;

test.describe('Revisión de clips (D2)', () => {
  test('elegir clips con el teclado, aprobar y quedar renderizando', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto(REVIEW);
    await settle(page);
    const bar = page.getByRole('region', { name: 'Acciones' });
    await expect(bar).toContainText('5 clips seleccionados');
    // X excludes the selected clip and A includes it back.
    await page.keyboard.press('x');
    await expect(bar).toContainText('4 clips seleccionados');
    await page.keyboard.press('a');
    await expect(bar).toContainText('5 clips seleccionados');

    await page.getByRole('button', { name: 'Renderizar 5 clips' }).click();
    const dialog = page.getByRole('dialog', { name: 'Renderizar 5 clips' });
    await expect(dialog).toContainText('Saldo después');
    await dialog.getByRole('button', { name: 'Renderizar' }).click();
    await expect(page).toHaveURL(new RegExp(`/proyectos/${EXAMPLE_IDS.projectToReview}/clips$`), { timeout: 20_000 });
    // Clips shows its cards as busy while they render, so the page never "settles" here: wait for its heading.
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.waitForLoadState('networkidle');

    // The project left the review: going back sends you to its clips.
    await page.goto(REVIEW);
    await expect(page).toHaveURL(new RegExp(`/proyectos/${EXAMPLE_IDS.projectToReview}/clips$`), { timeout: 20_000 });
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    expectClean(problems, REVIEW);
  });

  test('salir con cambios pregunta antes de perderlos', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto(REVIEW);
    await settle(page);
    await page.getByRole('checkbox', { name: /Incluir «/ }).first().uncheck();
    await page.getByRole('navigation', { name: 'Ruta' }).getByRole('link', { name: 'Proyectos' }).click();
    const dialog = page.getByRole('alertdialog', { name: 'Tenés cambios sin guardar' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Seguir editando' }).click();
    expectClean(problems, 'seguir editando');
    await expect(page).toHaveURL(new RegExp(`${REVIEW}$`));
    await expect(page.getByRole('region', { name: 'Acciones' })).toContainText('4 clips seleccionados');

    // The draft survives a reload; "Descartar" really drops it.
    await page.reload();
    await settle(page);
    expectClean(problems, 'recarga');
    await expect(page.getByRole('region', { name: 'Acciones' })).toContainText('4 clips seleccionados');
    await page.getByRole('navigation', { name: 'Ruta' }).getByRole('link', { name: 'Proyectos' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Descartar' }).click();
    await expect(page).toHaveURL(/\/proyectos$/);
    await settle(page);
    expectClean(problems, 'descartar');
    await page.goto(REVIEW);
    await settle(page);
    await expect(page.getByRole('region', { name: 'Acciones' })).toContainText('5 clips seleccionados');
    expectClean(problems, REVIEW);
  });

  test('en el teléfono: reproductor arriba, clips y transcripción en pestañas', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto(REVIEW);
    await settle(page);
    await page.getByRole('button', { name: 'Ajustar bordes' }).click();
    const trim = page.getByRole('group', { name: 'Bordes del clip' });
    const before = await trim.locator('output').first().textContent();
    await trim.getByRole('button', { name: 'Mover el inicio medio segundo después' }).click();
    await expect(trim.locator('output').first()).not.toHaveText(before ?? '');
    await page.getByRole('button', { name: 'Transcripción', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Transcripción' })).toContainText('Mantené apretada una palabra');
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expectClean(problems, REVIEW);
  });
});

test.describe('Editor de clip (D4)', () => {
  test('recortar, ver la vista previa gratis y volver a renderizar', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto(EDITOR);
    await settle(page);
    await page.getByRole('button', { name: 'Mover el fin 0,5 segundos después' }).click();
    await expect(page.getByText('Cambios sin guardar', { exact: true })).toBeVisible();

    // The preview shows the saved trim, so asking for it saves first (free).
    await page.getByRole('button', { name: 'Guardar y ver vista previa' }).click();
    await expect(page.getByText('Lista. Es gratis y vence en una hora.')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole('region', { name: 'Acciones' })).toContainText('El recorte guardado todavía no se renderizó');

    await page.getByRole('button', { name: /Volver a renderizar/ }).click();
    await page.getByRole('dialog', { name: 'Volver a renderizar' }).getByRole('button', { name: 'Renderizar' }).click();
    await expect(page).toHaveURL(new RegExp(`/proyectos/${EXAMPLE_IDS.projectReady}/clips$`), { timeout: 20_000 });
    // The clip is rendering again: Clips keeps its card busy until it ends.
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
    await page.waitForLoadState('networkidle');
    expectClean(problems, EDITOR);
  });

  test('el recorte sin guardar sigue ahí después de recargar y de ir y volver', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto(EDITOR);
    await settle(page);
    await page.getByRole('button', { name: 'Mover el inicio 0,5 segundos antes' }).click();
    const start = await page.getByRole('textbox', { name: /Inicio/ }).inputValue();
    await page.reload();
    await settle(page);
    await expect(page.getByText('Cambios sin guardar', { exact: true })).toBeVisible();
    await expect(page.getByRole('textbox', { name: /Inicio/ })).toHaveValue(start);
    // Leaving for the token shop does not ask (the work is kept) and Back brings it back.
    await page.goto('/uso/comprar');
    await settle(page);
    await page.goBack();
    await settle(page);
    await expect(page.getByRole('textbox', { name: /Inicio/ })).toHaveValue(start);
    await expect(page.getByRole('region', { name: 'Acciones' })).toContainText('Cambios sin guardar: recorte');
    expectClean(problems, EDITOR);
  });

  test('un recorte guardado en el editor es el que muestra la revisión al volver', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    // The review has a session draft that only excludes one clip.
    await page.goto(REVIEW);
    await settle(page);
    const before = await page.getByRole('textbox', { name: /Fin/ }).inputValue();
    await page.getByRole('checkbox', { name: /Incluir «/ }).nth(1).uncheck();
    await expect(page.getByRole('region', { name: 'Acciones' })).toContainText('4 clips seleccionados');

    // The best clip gets a longer trim in the editor (saving is free while the project is in review).
    await page.goto(`/proyectos/${EXAMPLE_IDS.projectToReview}/clips/00000008-0000-4000-8000-00000000012d`);
    await settle(page);
    await page.getByRole('button', { name: 'Mover el fin 0,5 segundos después' }).click();
    const saved = await page.getByRole('textbox', { name: /Fin/ }).inputValue();
    expect(saved).not.toBe(before);
    await page.getByRole('button', { name: 'Guardar cambios' }).click();
    await expect(page.getByText('Cambios guardados')).toBeVisible();

    await page.getByRole('link', { name: 'Ir a la revisión' }).click();
    await expect(page).toHaveURL(new RegExp(`${REVIEW}$`));
    await settle(page);
    await expect(page.getByRole('textbox', { name: /Fin/ })).toHaveValue(saved);
    await expect(page.getByRole('region', { name: 'Acciones' })).toContainText('4 clips seleccionados');
    expectClean(problems, REVIEW);
  });

  test('un Lector lo ve en modo lectura', async ({ page }) => {
    await useScenario(page, 'rol-lector');
    const problems = watch(page);
    await page.goto(EDITOR);
    await settle(page);
    await expect(page.getByText('Estás viendo el clip en modo lectura')).toBeVisible();
    // The 9:16 stage keeps its shape inside the narrow player card.
    const stage = await page.getByRole('group', { name: /^Reproductor:/ }).boundingBox();
    expect(stage?.height ?? 0).toBeGreaterThan(300);
    await expect(page.getByRole('button', { name: /Volver a renderizar/ })).toHaveAttribute('aria-disabled', 'true');
    expectClean(problems, EDITOR);
  });
});
