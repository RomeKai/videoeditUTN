import { expect, test } from '@playwright/test';
import { EXAMPLE_IDS } from '../src/dev/screens';
import { expectClean, settle, useScenario, watch } from './helpers';

const CLIP = EXAMPLE_IDS.clip;
const SECOND_CLIP = '00000008-0000-4000-8000-0000000001f7';

test.describe('E1 · Publicar y E2 · Publicaciones', () => {
  test('publicar un clip ahora: elegir la privacidad de TikTok, publicar y verlo en cola', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto(`/publicaciones?vista=lista&publicar=${CLIP}`);
    const panel = page.getByRole('dialog', { name: 'Publicar clip' });
    await expect(panel.getByRole('heading', { name: 'Redes' })).toBeVisible();
    await expect(panel.getByRole('checkbox', { name: 'Publicar en TikTok' })).toBeChecked();
    await expect(panel.getByRole('checkbox', { name: 'Publicar en Instagram Reels' })).toBeChecked();
    await expect(panel.getByText('Sin conectar')).toBeVisible();

    const publish = panel.getByRole('region', { name: 'Acciones' }).getByRole('button', { name: 'Publicar' });
    await expect(publish).toBeDisabled();
    await panel.getByRole('button', { name: 'Elegí la privacidad de TikTok' }).click();
    await expect(panel.getByLabel('Quién puede ver este video')).toBeFocused();
    await panel.getByLabel('Quién puede ver este video').selectOption({ label: 'Todos' });
    await expect(panel.getByRole('tab', { name: /TikTok/ })).toContainText('Listo');

    // The project asked for SEO: the text arrives by itself, marked as suggested by the AI.
    await expect(panel.getByText('Sugerido por IA')).toBeVisible({ timeout: 15_000 });
    await expect(panel.getByLabel('Caption')).toHaveValue(/^Lo mejor de «/);

    await publish.click();
    await expect(page.getByText(/se está publicando en TikTok e Instagram/)).toBeVisible();
    await expect(panel).toBeHidden();
    await expect(page).toHaveURL(/\/publicaciones\?vista=lista$/);
    // Both publications are in the list, on their way out; the drafts used for the text are gone.
    await expect(page.getByRole('button', { name: /Borradores/ })).toContainText('1');
    await expect(page.getByRole('row').filter({ hasText: /En cola|Publicando/ })).not.toHaveCount(0);
    expectClean(problems, 'publicar ahora');
  });

  test('programar dos clips, uno por día', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto(`/publicaciones?publicar=${CLIP},${SECOND_CLIP}`);
    const panel = page.getByRole('dialog', { name: 'Publicar 2 clips' });
    await expect(panel.getByText('2 clips seleccionados')).toBeVisible();
    await panel.getByRole('group', { name: 'Cuándo publicar' }).getByRole('button', { name: 'Programar' }).click();
    await expect(panel.getByRole('button', { name: 'Uno por día' })).toHaveAttribute('aria-pressed', 'true');
    await expect(panel.getByRole('list', { name: 'Orden y fecha de cada clip' }).getByRole('listitem')).toHaveCount(2);
    await expect(panel.getByText(/Zona horaria:/)).toBeVisible();
    await expect(panel.getByText('2 clips × 2 redes = 4 publicaciones')).toBeVisible();

    await panel.getByLabel('Quién puede ver estos videos').selectOption({ label: 'Solo yo' });
    await panel.getByRole('region', { name: 'Acciones' }).getByRole('button', { name: 'Programar' }).click();
    await expect(page.getByText('4 publicaciones programadas en TikTok e Instagram')).toBeVisible({ timeout: 20_000 });
    await expect(panel).toBeHidden();
    expectClean(problems, 'programar varios');
  });

  test('cerrar el panel sin publicar deja todo como estaba', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto(`/publicaciones?vista=lista&publicar=${CLIP}`);
    const panel = page.getByRole('dialog', { name: 'Publicar clip' });
    await expect(panel.getByText('Sugerido por IA')).toBeVisible({ timeout: 15_000 });
    await panel.getByRole('button', { name: 'Cancelar' }).click();
    await expect(panel).toBeHidden();
    await expect(page).toHaveURL(/\/publicaciones\?vista=lista$/);
    await expect(page.getByRole('button', { name: /Borradores/ })).toContainText('1');
    await expect(page.getByRole('button', { name: /^Todas/ })).toContainText('8');
    expectClean(problems, 'cerrar el panel');
  });

  test('plan Gratis: una red por publicación y «Programar» con candado', async ({ page }) => {
    await useScenario(page, 'plan-gratis');
    const problems = watch(page);
    await page.goto(`/brand-kits?publicar=${CLIP}`);
    const panel = page.getByRole('dialog', { name: 'Publicar clip' });
    await expect(panel.getByText('Tu plan permite una red por publicación')).toBeVisible();
    await panel.getByRole('checkbox', { name: 'Publicar en Instagram Reels' }).check();
    await expect(panel.getByRole('checkbox', { name: 'Publicar en TikTok' })).not.toBeChecked();
    await expect(panel.getByRole('group', { name: 'Cuándo publicar' }).getByRole('button', { name: 'Programar' })).toHaveAttribute('aria-disabled', 'true');
    await expect(panel.getByRole('region', { name: 'Acciones' }).getByRole('button', { name: 'Publicar' })).toBeEnabled();
    expectClean(problems, 'publicar con plan Gratis');
  });

  test('lista: reintentar la que falló, reprogramar y cancelar', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/publicaciones?vista=lista');
    await settle(page);
    await expect(page.getByText(/no salió en TikTok/)).toBeVisible();
    await page.getByRole('table').getByRole('button', { name: /^Reintentar «/ }).click();
    await expect(page.getByText(/^Reintentando «/)).toBeVisible();
    await expect(page.getByText(/no salió en TikTok/)).toBeHidden();

    await page.getByRole('table').getByRole('button', { name: /^Reprogramar/ }).first().click();
    const detail = page.getByRole('complementary', { name: 'Detalle de la publicación' });
    await expect(detail.getByRole('form', { name: 'Editar la publicación' })).toBeVisible();
    await detail.getByLabel('Hora').fill('07:45');
    await detail.getByRole('button', { name: 'Guardar' }).click();
    await expect(page.getByText(/^Reprogramada para el .*07:45$/)).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: '07:45' })).toHaveCount(1);

    await detail.getByRole('button', { name: 'Cancelar publicación' }).click();
    await page.getByRole('alertdialog').getByRole('button', { name: 'Cancelar publicación' }).click();
    await expect(page.getByText('Publicación cancelada')).toBeVisible();
    await expect(page.getByRole('row').filter({ hasText: '07:45' })).toHaveCount(0);
    await expect(detail).toBeHidden();
    expectClean(problems, 'lista de publicaciones');
  });

  test('semana, mes y vacío', async ({ page }) => {
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/publicaciones');
    await settle(page);
    await expect(page.getByRole('grid', { name: /Publicaciones de la semana/ })).toBeVisible();
    await expect(page.getByText('Arrastrá una publicación programada')).toBeVisible();
    await page.getByRole('group', { name: 'Vista' }).getByRole('button', { name: 'Mes' }).click();
    await expect(page.getByRole('grid', { name: /^Publicaciones de [a-z]+ de \d{4}$/ })).toBeVisible();
    await expect(page.getByRole('gridcell').filter({ hasText: 'Hoy' })).toHaveCount(1);
    // Opening a card shows its detail in the side panel.
    await page.getByRole('grid').getByRole('button', { name: /TikTok: Error/ }).click();
    const detail = page.getByRole('dialog', { name: 'Detalle de la publicación' });
    await expect(detail.getByText('La red rechazó el video.', { exact: false })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(detail).toBeHidden();
    expectClean(problems, 'calendario');
  });

  test('sin publicaciones: «Todavía no programaste nada»', async ({ page }) => {
    await useScenario(page, 'workspace-vacio');
    const problems = watch(page);
    await page.goto('/publicaciones');
    await settle(page);
    await expect(page.getByRole('heading', { name: 'Todavía no programaste nada' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Ir a mis clips' })).toBeVisible();
    expectClean(problems, 'publicaciones vacío');
  });

  test('en el teléfono: la lista por día y el panel a pantalla completa', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await useScenario(page, 'estudio-norte');
    const problems = watch(page);
    await page.goto('/publicaciones');
    await settle(page);
    await expect(page.getByRole('group', { name: 'Vista' })).toHaveCount(0);
    await expect(page.getByLabel('Filtrar por estado')).toBeVisible();
    await expect(page.getByRole('heading', { level: 2 }).first()).toBeVisible();
    await page.getByRole('button', { name: /^Programar «/ }).click();
    const panel = page.getByRole('dialog', { name: 'Publicar clip' });
    await expect(panel.getByRole('heading', { name: 'Redes' })).toBeVisible();
    const box = await panel.boundingBox();
    expect(box?.width).toBe(390);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(390);
    expectClean(problems, 'publicaciones en el teléfono');
  });
});
