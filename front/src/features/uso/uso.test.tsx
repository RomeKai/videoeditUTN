import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Plan } from '@/api/contracts/billing';
import { loadDb, saveDb } from '@/api/mocks/state';
import { BuyTokensScreen, parseTokenAmount } from './BuyTokensScreen';
import { leaveTo } from './leave';
import { movementConcept, signedAmount } from './movements';
import { MovementsScreen } from './MovementsScreen';
import { monthKey, monthLong, monthTitle, recentMonths, shiftMonth, workspaceDay } from './period';
import { planFeatures, planLosses } from './PlanCard';
import { PlansScreen } from './PlansScreen';
import { PricingScreen } from './PricingScreen';
import { PurchaseResultScreen } from './PurchaseResultScreen';
import { failRead, renderScreen, startMockApi } from './test-utils';
import { gridStep, usageTotals } from './UsageChart';
import { UsageHeader } from './UsageHeader';
import { UsageOverview } from './UsageOverview';

const navigation = vi.hoisted(() => ({ pathname: '/uso', search: '', replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: navigation.replace, back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(navigation.search),
  useParams: () => ({}),
}));
vi.mock('./leave', () => ({ leaveTo: vi.fn() }));
const download = vi.hoisted(() => ({ downloadText: vi.fn() }));
vi.mock('@/lib/download', () => download);

startMockApi();

/** The seed is relative to "now": mid-month keeps the seeded charges (1 to 20 days old) inside the current month. */
const MID_MONTH = new Date(2026, 10, 15, 12, 0, 0);
/** The first hour of a month: everything seeded more than an hour ago belongs to the previous one. */
const FIRST_OF_MONTH = new Date('2026-12-01T04:00:00Z'); // 01:00 in Buenos Aires
/** Already December in UTC (where these tests and CI run), still 30 November 23:50 for the workspace. */
const MONTH_EDGE = new Date('2026-12-01T02:50:00Z');
const pinClock = (date: Date) => {
  vi.useFakeTimers({ toFake: ['Date'] });
  vi.setSystemTime(date);
};

afterEach(() => vi.useRealTimers());

beforeEach(() => {
  pinClock(MID_MONTH);
  navigation.pathname = '/uso';
  navigation.search = '';
  navigation.replace.mockClear();
  vi.mocked(leaveTo).mockClear();
  download.downloadText.mockClear();
});

const plan = (overrides: Partial<Plan>): Plan => ({
  id: '00000000-0000-4000-8000-000000000001',
  name: 'Pro',
  max_video_duration_seconds: 180 * 60,
  max_resolution: '4K',
  has_watermark: false,
  allow_scheduling: true,
  allow_crossposting: true,
  has_seo_optimization: true,
  has_thumbnail_engine: false,
  base_discount_rate: 0.1,
  max_members: 10,
  is_active: true,
  price_usd_monthly: 49,
  description: '',
  sort_order: 3,
  is_recommended: true,
  ...overrides,
});

describe('utilidades de Uso', () => {
  it('arma los meses del filtro y sus nombres', () => {
    const november = new Date(2026, 10, 24);
    expect(monthKey(november)).toBe('2026-11');
    expect(shiftMonth('2026-01', -1)).toBe('2025-12');
    expect(shiftMonth('2026-12', 1)).toBe('2027-01');
    expect(shiftMonth('2026-11', -11)).toBe('2025-12');
    expect(recentMonths(3, november)).toEqual(['2026-11', '2026-10', '2026-09']);
    expect(monthTitle('2026-11')).toBe('Noviembre 2026');
    expect(monthLong('2026-11')).toBe('noviembre de 2026');
  });

  it('cuenta el mes y el día en la zona horaria del workspace, no en la del navegador', () => {
    expect(new Date(2026, 0, 1, 0, 0).getTimezoneOffset()).toBe(0); // the tests run in UTC, like CI
    expect(workspaceDay(MONTH_EDGE)).toBe('2026-11-30');
    expect(monthKey(MONTH_EDGE)).toBe('2026-11');
    expect(recentMonths(2, MONTH_EDGE)).toEqual(['2026-11', '2026-10']);
    expect(workspaceDay(new Date('2026-12-01T03:00:00Z'))).toBe('2026-12-01');
    expect(monthKey(FIRST_OF_MONTH)).toBe('2026-12');
  });

  it('elige un paso redondo para la grilla del gráfico y suma por tipo', () => {
    expect(gridStep(6507)).toBe(2000);
    expect(gridStep(900)).toBe(200);
    expect(gridStep(0)).toBe(1);
    expect(usageTotals([{ date: '2026-11-01', projects: 10, renders: 2.5, seo: 5 }, { date: '2026-11-02', projects: 1, renders: 0, seo: 0 }])).toEqual({
      projects: 11,
      renders: 2.5,
      seo: 5,
      total: 18.5,
    });
  });

  it('muestra el signo según lo que el movimiento le hace al saldo', () => {
    expect(signedAmount(1404.9, false)).toBe('−1.404,90');
    expect(signedAmount(5000, true)).toBe('+5.000');
    expect(movementConcept({ description: 'Procesamiento: Vlog Mendoza', project: { id: 'x', title: 'Vlog Mendoza' }, kind: 'charged', amount: 10 })).toBe('Procesamiento');
    expect(movementConcept({ description: 'Compra de 5000 tokens', project: null, kind: 'purchase', amount: 5000 })).toBe('Compra de 5.000 tokens');
  });

  it('lee la cantidad libre de tokens con o sin puntos', () => {
    expect(parseTokenAmount('2.500')).toBe(2500);
    expect(parseTokenAmount('2500')).toBe(2500);
    expect(parseTokenAmount('15.000')).toBe(15000);
    expect(parseTokenAmount('1.500.000')).toBe(1500000);
    expect(parseTokenAmount('12,5')).toBeNull();
    expect(parseTokenAmount('2.5')).toBeNull();
    expect(parseTokenAmount('2.50')).toBeNull();
    expect(parseTokenAmount('25.0000')).toBeNull();
    expect(parseTokenAmount('1 000')).toBeNull();
    expect(parseTokenAmount('0')).toBeNull();
    expect(parseTokenAmount('abc')).toBeNull();
  });

  it('dice qué se pierde al bajar de plan y qué plan habilita cada función', () => {
    const pro = plan({});
    const creator = plan({ name: 'Creador', sort_order: 2, max_video_duration_seconds: 3600, max_resolution: '1080p', allow_crossposting: false, base_discount_rate: 0.05, max_members: 5 });
    const free = plan({ name: 'Gratis', sort_order: 1, has_watermark: true, allow_scheduling: false, allow_crossposting: false, has_seo_optimization: false, base_discount_rate: 0, max_members: 2 });
    expect(planLosses(pro, creator)).toEqual([
      'Videos de hasta 60 min (hoy 180 min)',
      'Resolución hasta 1080p (hoy 4K)',
      'Publicar en varias redes a la vez',
      'Miembros: 5 (hoy 10)',
      'Descuento en tokens: 5 % (hoy 10 %)',
    ]);
    expect(planLosses(creator, pro)).toEqual([]);
    expect(planLosses(pro, free)).toContain('Miembros: 2 (hoy 10)');
    const features = planFeatures(free, [free, creator, pro]);
    expect(features.find((item) => item.key === 'crossposting')).toMatchObject({ included: false, availableIn: 'Pro' });
    expect(features.find((item) => item.key === 'watermark')).toMatchObject({ included: false, availableIn: 'Creador' });
    expect(features.find((item) => item.key === 'members')).toMatchObject({ included: true, value: '2' });
  });
});

describe('G1 · Resumen', () => {
  it('muestra saldo, reservas por proyecto, plan y últimos movimientos al dueño', async () => {
    renderScreen(<UsageOverview />);
    const summary = await screen.findByRole('region', { name: 'Resumen de tokens y plan' });
    expect(await within(summary).findByText('4.860')).toBeInTheDocument();
    const holds = await within(summary).findByRole('list', { name: 'Reservas por proyecto' });
    expect(within(holds).getByRole('link', { name: 'Clase 3: Finanzas personales' })).toHaveAttribute('href', expect.stringMatching(/^\/proyectos\//));
    expect(within(summary).getByRole('link', { name: 'Comprar tokens' })).toHaveAttribute('href', '/uso/comprar');
    const planCard = await screen.findByRole('region', { name: 'Plan actual' });
    expect(within(planCard).getByText('Pro')).toBeInTheDocument();
    expect(within(planCard).getByText(/Incluye 10 % de descuento en tokens/)).toBeInTheDocument();
    const latest = await screen.findByRole('region', { name: 'Últimos movimientos' });
    expect((await within(latest).findAllByText('Reservado')).length).toBeGreaterThan(0);
    expect(await within(latest).findByText('Compra de 1.000 tokens')).toBeInTheDocument();
    expect(await screen.findByRole('img', { name: /Consumo por día en/ })).toBeInTheDocument();
    expect(screen.queryByText('Te queda poco saldo')).not.toBeInTheDocument();
  });

  it('con el navegador ya en diciembre (UTC) y el workspace todavía en noviembre, el mes es noviembre', async () => {
    pinClock(MONTH_EDGE);
    renderScreen(<UsageOverview />);
    const spent = await screen.findByRole('region', { name: 'Gastado este mes' });
    expect(await within(spent).findByText(/Del 1 al 30 de noviembre, en \d+ cobros?\./)).toBeInTheDocument();
    expect(await screen.findByText('Noviembre 2026 · tokens cobrados')).toBeInTheDocument();
    expect(await screen.findByRole('img', { name: /Consumo por día en noviembre de 2026\. Total cobrado/ })).toBeInTheDocument();
    expect(screen.queryByText(/Todavía no hubo cobros/)).not.toBeInTheDocument();
  });

  it('avisa cuando el saldo no alcanza para un video promedio', async () => {
    renderScreen(<UsageOverview />, { scenario: 'saldo-bajo' });
    expect(await screen.findByText('Te queda poco saldo')).toBeInTheDocument();
  });

  it('sin proyectos no hay reservas ni movimientos', async () => {
    renderScreen(<UsageOverview />, { scenario: 'workspace-vacio' });
    expect(await screen.findByText(/No hay trabajos en curso/)).toBeInTheDocument();
    expect(await screen.findByText(/Todavía no hay movimientos/)).toBeInTheDocument();
    expect(await screen.findByText(/Todavía no hubo consumo este mes/)).toBeInTheDocument();
  });

  it('a un Editor le muestra solo el saldo y a quién pedirle más (R1)', async () => {
    renderScreen(
      <>
        <UsageHeader />
        <UsageOverview />
      </>,
      { scenario: 'rol-editor' },
    );
    expect(await screen.findByText('El saldo de tokens de Estudio Norte.')).toBeInTheDocument();
    const tabs = screen.getByRole('navigation', { name: 'Uso y facturación' });
    expect(within(tabs).getByRole('link', { name: 'Resumen' })).toHaveAttribute('aria-current', 'page');
    for (const name of ['Movimientos', 'Planes', 'Comprar tokens']) {
      expect(within(tabs).getByText(name).closest('[aria-disabled="true"]')).not.toBeNull();
    }
    expect(await screen.findByText(/Como Editor ves el saldo y lo reservado/)).toBeInTheDocument();
    expect(await screen.findByText('Lucía Fernández')).toBeInTheDocument();
    expect(screen.getByText('Martín Sosa')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Comprar tokens' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.queryByText('Últimos movimientos')).not.toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Reservas por proyecto' })).not.toBeInTheDocument();
  });
});

describe('G2 · Movimientos', () => {
  it('lista los movimientos con su tipo, filtra y exporta', async () => {
    const user = userEvent.setup();
    renderScreen(<MovementsScreen />);
    const table = await screen.findByRole('table', { name: /Movimientos de/ });
    expect(within(table).getAllByText('Reservado').length).toBeGreaterThan(0);
    expect(within(table).getAllByText('Cobrado').length).toBeGreaterThan(0);
    expect(within(table).getByText('Pendiente')).toBeInTheDocument();
    expect(within(table).getByText('Todavía no suma al saldo')).toBeInTheDocument();

    const filters = screen.getByRole('group', { name: 'Filtrar por tipo' });
    await user.click(within(filters).getByRole('button', { name: /^Reservados/ }));
    await waitFor(() => expect(within(screen.getByRole('table', { name: /Movimientos de/ })).queryByText('Cobrado')).not.toBeInTheDocument());
    expect(within(screen.getByRole('table', { name: /Movimientos de/ })).getAllByText('Se cobra al terminar').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: 'Exportar CSV' }));
    await waitFor(() => expect(download.downloadText).toHaveBeenCalledTimes(1));
    const [csv, filename] = download.downloadText.mock.calls[0] as [string, string];
    expect(csv.split('\n')[0]).toBe('fecha,concepto,tipo,monto');
    expect(csv).toContain('reserved');
    expect(csv).not.toContain('charged');
    // Lo reservado sale con el mismo signo que en la pantalla.
    expect(csv.split('\n').slice(1).every((line) => line.split(',').pop()?.startsWith('-'))).toBe(true);
    expect(filename).toMatch(/^movimientos-\d{4}-\d{2}\.csv$/);
  });

  it('el día 1 los cobros de ayer quedan en el mes anterior y se llegan con el filtro de mes', async () => {
    pinClock(FIRST_OF_MONTH);
    const user = userEvent.setup();
    renderScreen(<MovementsScreen />);
    const filters = await screen.findByRole('group', { name: 'Filtrar por tipo' });
    // Only the project that reached its review in the last hour was charged this month (its processing is charged before the review).
    await waitFor(() => expect(within(filters).getByRole('button', { name: /^Cobrados/ })).toHaveTextContent('Cobrados1'));
    expect(screen.getByLabelText('Filtrar por mes')).toHaveValue('2026-12');
    await user.selectOptions(screen.getByLabelText('Filtrar por mes'), 'Todos los meses');
    const table = await screen.findByRole('table', { name: 'Movimientos de todos los meses' });
    expect(within(table).getAllByText('Cobrado').length).toBeGreaterThan(0);
    await user.selectOptions(screen.getByLabelText('Filtrar por mes'), 'Noviembre 2026');
    expect(within(await screen.findByRole('table', { name: 'Movimientos de noviembre de 2026' })).getAllByText('Cobrado').length).toBeGreaterThan(0);
  });

  it('en el borde del mes abre en el mes del workspace y encuentra los cobros', async () => {
    pinClock(MONTH_EDGE);
    renderScreen(<MovementsScreen />);
    const table = await screen.findByRole('table', { name: 'Movimientos de noviembre de 2026' });
    expect(screen.getByLabelText('Filtrar por mes')).toHaveValue('2026-11');
    expect(within(table).getAllByText('Cobrado').length).toBeGreaterThan(0);
    expect(within(table).getAllByText('Reservado').length).toBeGreaterThan(0);
  });

  it('sin movimientos explica qué va a aparecer', async () => {
    renderScreen(<MovementsScreen />, { scenario: 'workspace-vacio' });
    expect(await screen.findByRole('heading', { name: /No hay movimientos en/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Exportar CSV' })).toBeDisabled();
  });

  it('no se muestra a un Lector', async () => {
    renderScreen(<MovementsScreen />, { scenario: 'rol-lector' });
    expect(await screen.findByRole('heading', { name: /No tenés acceso a esto/ })).toBeInTheDocument();
  });
});

describe('G3 · Planes', () => {
  it('marca el plan actual; bajar muestra qué se pierde, queda programado y se puede cancelar', async () => {
    const user = userEvent.setup();
    renderScreen(<PlansScreen />, { scenario: 'plan-estudio' });
    const current = await screen.findByRole('article', { name: 'Plan Estudio, tu plan' });
    expect(within(current).getAllByText('Tu plan').length).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: 'Bajar a Pro' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Bajar a Pro?' });
    // Pro and Estudio both allow 3 h (the upload ceiling): the duration is not lost.
    expect(within(dialog).queryByText(/^Videos de hasta/)).not.toBeInTheDocument();
    expect(within(dialog).getByText('Miembros: 10 (hoy 15)')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Bajar a Pro' }));

    expect(await screen.findByText(/^Tu plan pasa a Pro el /, { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByRole('article', { name: 'Plan Estudio, tu plan' })).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: /^Plan Pro/ })).getByText('Próximo plan')).toBeInTheDocument();
    expect(loadDb().workspaces[0]?.pending_plan).not.toBeNull();

    await user.click(screen.getByRole('button', { name: 'Seguir con Estudio' }));
    expect(await screen.findByText('Listo: seguís con el plan Estudio.')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText('Próximo plan')).not.toBeInTheDocument());
    expect(loadDb().workspaces[0]?.pending_plan).toBeNull();
    expect(screen.getByRole('button', { name: 'Bajar a Pro' })).toBeInTheDocument();
  });

  it('no deja bajar a un plan con menos lugares que los ocupados', async () => {
    const user = userEvent.setup();
    renderScreen(<PlansScreen />);
    await screen.findByRole('article', { name: 'Plan Pro, tu plan' });
    await waitFor(() => expect(loadDb().members.length).toBeGreaterThan(0));
    await user.click(screen.getByRole('button', { name: 'Bajar a Gratis' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'Todavía no podés bajar a Gratis' });
    expect(within(dialog).getByText(/Hoy ocupás 4 lugares, contando las invitaciones pendientes, y Gratis tiene 2/)).toBeInTheDocument();
    expect(within(dialog).getByRole('link', { name: 'Ir a Miembros' })).toHaveAttribute('href', '/ajustes/miembros');
    expect(within(dialog).queryByRole('button', { name: 'Bajar a Gratis' })).not.toBeInTheDocument();
    expect(loadDb().workspaces[0]?.pending_plan).toBeNull();
  });

  it('subir de plan se aplica al momento', async () => {
    const user = userEvent.setup();
    renderScreen(<PlansScreen />, { scenario: 'plan-gratis' });
    await screen.findByRole('article', { name: 'Plan Gratis, tu plan' });
    await user.click(screen.getByRole('button', { name: 'Subir a Creador' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Subir a Creador?' });
    await user.click(within(dialog).getByRole('button', { name: 'Subir a Creador' }));
    expect(await screen.findByRole('article', { name: 'Plan Creador, tu plan' })).toBeInTheDocument();
    expect(await screen.findByText('Listo: ya estás en el plan Creador.')).toBeInTheDocument();
  });

  it('si subir pide un pago, va al checkout y el plan no cambia todavía', async () => {
    const user = userEvent.setup();
    renderScreen(<PlansScreen />, { scenario: 'plan-con-checkout' });
    await screen.findByRole('article', { name: 'Plan Pro, tu plan' });
    await user.click(screen.getByRole('button', { name: 'Subir a Estudio' }));
    await user.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Subir a Estudio' }));
    await waitFor(() => expect(leaveTo).toHaveBeenCalledTimes(1));
    expect(vi.mocked(leaveTo).mock.calls[0]?.[0]).toMatch(/\/dev\/checkout\?compra=.+&volver=.+%2Fuso%2Fplanes$/);
    expect(screen.getByRole('article', { name: 'Plan Pro, tu plan' })).toBeInTheDocument();
    expect(loadDb().purchases[0]).toMatchObject({ status: 'pending', tokens: 0 });
  });

  it('al volver del checkout avisa si el plan cambió o si el pago no salió', async () => {
    const PAYMENT_ID = '22222222-2222-4222-8222-222222222222';
    const seed = (status: 'approved' | 'rejected') => () => {
      const db = loadDb();
      const workspace = db.workspaces[0];
      const studio = db.plans.find((item) => item.name === 'Estudio');
      if (!workspace || !studio) throw new Error('faltan datos');
      db.purchases.push({
        id: PAYMENT_ID,
        workspace: workspace.id,
        status,
        tokens: 0,
        amount_usd: 129,
        plan_id: studio.id,
        checkout_url: null,
        failure_reason: status === 'rejected' ? 'El banco rechazó el pago. Probá con otra tarjeta.' : null,
        created_at: new Date().toISOString(),
        balance_after: null,
        _settleAt: null,
        _reject: status === 'rejected',
      });
      saveDb(db);
    };
    navigation.search = `compra=${PAYMENT_ID}`;
    const approved = renderScreen(<PlansScreen />, { scenario: 'plan-con-checkout', prepare: seed('approved') });
    // The subscription on screen is still Pro (it refetches after): the notice names the plan that was paid.
    expect(await screen.findByText('Listo: ya estás en el plan Estudio')).toBeInTheDocument();
    expect(screen.queryByText('Listo: ya estás en el plan Pro')).not.toBeInTheDocument();
    approved.unmount();

    renderScreen(<PlansScreen />, { scenario: 'plan-con-checkout', prepare: seed('rejected') });
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('El pago no se completó. No se te cobró nada.')).toBeInTheDocument();
    expect(await within(alert).findByText(/Seguís con el plan Pro/)).toBeInTheDocument();
    expect(screen.getByRole('article', { name: 'Plan Pro, tu plan' })).toBeInTheDocument();
  });

  it('un Editor que entra por URL ve lo mismo que dice la pestaña bloqueada: sin acceso', async () => {
    renderScreen(<PlansScreen />, { scenario: 'rol-editor' });
    expect(await screen.findByRole('heading', { name: /No tenés acceso a esto/ })).toBeInTheDocument();
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
  });
});

describe('A6 · Precios', () => {
  it('muestra los cuatro planes, el recomendado y el ejemplo de costo', async () => {
    renderScreen(<PricingScreen />, { scenario: 'sin-sesion' });
    expect(screen.getByRole('heading', { level: 1, name: 'Planes y precios' })).toBeInTheDocument();
    expect(await screen.findByRole('article', { name: 'Plan Pro, recomendado' })).toBeInTheDocument();
    expect(screen.getAllByRole('article')).toHaveLength(4);
    const free = screen.getByRole('article', { name: 'Plan Gratis' });
    expect(within(free).getByRole('link', { name: 'Empezar gratis' })).toHaveAttribute('href', '/registro');
    expect(within(free).getByText('Publicar en varias redes').closest('li')).toHaveTextContent('Disponible desde el Plan Pro');
    expect(await screen.findByText(/≈ 900 tokens/, { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByText('Con Estudio, −20 %').nextElementSibling).toHaveTextContent('720 tokens');
  });
});

describe('G4 · Comprar tokens', () => {
  it('arranca con el paquete del medio, calcula el saldo después y va al checkout', async () => {
    const user = userEvent.setup();
    renderScreen(<BuyTokensScreen />);
    const packs = await screen.findByRole('radiogroup', { name: 'Elegí cuántos tokens comprar' });
    const radios = within(packs).getAllByRole('radio');
    expect(radios).toHaveLength(3);
    expect(radios[1]).toHaveAttribute('aria-checked', 'true');
    const summary = screen.getByRole('region', { name: 'Resumen de la compra' });
    await waitFor(() => expect(within(summary).getByText('9.860 tokens')).toBeInTheDocument());
    expect(within(summary).getByText('USD 45')).toBeInTheDocument();

    await user.click(radios[0] as HTMLElement);
    expect(within(summary).getByText('5.860 tokens')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Ir al pago' }));
    await waitFor(() => expect(leaveTo).toHaveBeenCalledTimes(1));
    expect(vi.mocked(leaveTo).mock.calls[0]?.[0]).toMatch(/\/dev\/checkout\?compra=.+&volver=.+resultado/);
    expect(loadDb().purchases).toHaveLength(1);
    expect(loadDb().purchases[0]).toMatchObject({ tokens: 1000, status: 'pending' });
  });

  it('la cantidad libre reemplaza al paquete y respeta el mínimo', async () => {
    const user = userEvent.setup();
    renderScreen(<BuyTokensScreen />);
    const input = await screen.findByLabelText('Otra cantidad');
    await user.type(input, '50');
    expect(await screen.findByText(/^El mínimo es /)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Ir al pago' })).toBeDisabled();
    await user.clear(input);
    await user.type(input, '2.500');
    const summary = screen.getByRole('region', { name: 'Resumen de la compra' });
    expect(within(summary).getByText('2.500 tokens')).toBeInTheDocument();
    expect(within(summary).getByText('USD 25')).toBeInTheDocument();
    expect(screen.getAllByRole('radio').every((radio) => radio.getAttribute('aria-checked') === 'false')).toBe(true);
    await user.click(screen.getByRole('button', { name: 'Ir al pago' }));
    await waitFor(() => expect(loadDb().purchases[0]).toMatchObject({ tokens: 2500 }));
  });

  it('al reintentar una compra vuelve con la misma cantidad elegida', async () => {
    navigation.search = 'tokens=15000';
    const pack = renderScreen(<BuyTokensScreen />);
    const radios = within(await screen.findByRole('radiogroup')).getAllByRole('radio');
    expect(radios[2]).toHaveAttribute('aria-checked', 'true');
    pack.unmount();
    navigation.search = 'tokens=2500';
    renderScreen(<BuyTokensScreen />);
    await waitFor(async () => expect(await screen.findByLabelText('Otra cantidad')).toHaveValue('2.500'));
    expect(within(screen.getByRole('region', { name: 'Resumen de la compra' })).getByText('USD 25')).toBeInTheDocument();
  });

  it('volver del checkout con el botón Atrás deja «Ir al pago» listo otra vez', async () => {
    const user = userEvent.setup();
    renderScreen(<BuyTokensScreen />);
    await screen.findByRole('radiogroup');
    const pay = screen.getByRole('button', { name: 'Ir al pago' });
    await user.click(pay);
    await waitFor(() => expect(leaveTo).toHaveBeenCalledTimes(1));
    expect(screen.getByRole('button', { name: 'Ir al pago' })).toHaveAttribute('aria-busy', 'true');
    const event = new Event('pageshow');
    Object.defineProperty(event, 'persisted', { value: true });
    window.dispatchEvent(event);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Ir al pago' })).not.toHaveAttribute('aria-busy', 'true'));
    await user.click(screen.getByRole('button', { name: 'Ir al pago' }));
    await waitFor(() => expect(leaveTo).toHaveBeenCalledTimes(2));
  });

  it('un Editor no puede comprar', async () => {
    renderScreen(<BuyTokensScreen />, { scenario: 'rol-editor' });
    expect(await screen.findByRole('heading', { name: /No tenés acceso a esto/ })).toBeInTheDocument();
  });
});

describe('G5 · Resultado de la compra', () => {
  const PURCHASE_ID = '11111111-1111-4111-8111-111111111111';
  function seedPurchase(status: 'approved' | 'rejected') {
    const db = loadDb();
    const workspace = db.workspaces[0];
    if (!workspace) throw new Error('sin workspace');
    db.purchases.push({
      id: PURCHASE_ID,
      workspace: workspace.id,
      status,
      tokens: 5000,
      amount_usd: 45,
      checkout_url: null,
      failure_reason: status === 'rejected' ? 'El banco rechazó el pago. Probá con otra tarjeta.' : null,
      created_at: new Date().toISOString(),
      balance_after: status === 'approved' ? 9860 : null,
      _settleAt: null,
      _reject: status === 'rejected',
    });
    saveDb(db);
  }

  it('compra acreditada: avisa con un toast sobre el Resumen', async () => {
    navigation.search = `compra=${PURCHASE_ID}`;
    const first = renderScreen(<PurchaseResultScreen />, { prepare: () => seedPurchase('approved') });
    expect(await screen.findByText(/Compra acreditada:/, { selector: 'span' })).toBeInTheDocument();
    expect(await screen.findByRole('link', { name: 'Ver movimiento' })).toHaveAttribute('href', '/uso/movimientos');

    // Recargar el resultado no vuelve a avisar la misma compra.
    first.unmount();
    renderScreen(<PurchaseResultScreen />, { prepare: () => seedPurchase('approved') });
    expect(await screen.findByText('Compra acreditada: +5.000 tokens.')).toHaveClass('sr-only');
    await screen.findByRole('region', { name: 'Plan actual' });
    expect(screen.queryByRole('link', { name: 'Ver movimiento' })).not.toBeInTheDocument();
  });

  it('en el resultado la pestaña actual es Resumen', async () => {
    navigation.pathname = '/uso/comprar/resultado';
    renderScreen(<UsageHeader />);
    const tabs = await screen.findByRole('navigation', { name: 'Uso y facturación' });
    expect(within(tabs).getByRole('link', { name: 'Resumen' })).toHaveAttribute('aria-current', 'page');
    expect(within(tabs).getByRole('link', { name: 'Comprar tokens' })).not.toHaveAttribute('aria-current');
  });

  it('pago rechazado: el aviso queda arriba y el saldo no cambia', async () => {
    navigation.search = `compra=${PURCHASE_ID}`;
    renderScreen(<PurchaseResultScreen />, { scenario: 'pago-rechazado', prepare: () => seedPurchase('rejected') });
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('El pago no se completó. No se te cobró nada.')).toBeInTheDocument();
    expect(await within(alert).findByText(/Tu saldo sigue en/)).toHaveTextContent('4.860');
    expect(within(alert).getByRole('link', { name: 'Reintentar' })).toHaveAttribute('href', '/uso/comprar?tokens=5000');
    expect(within(alert).getByRole('link', { name: 'Elegir otro medio' })).toHaveAttribute('href', '/uso/comprar?tokens=5000');
  });

  it('el pago de un plan no se anuncia acá: sigue en Planes', async () => {
    navigation.search = `compra=${PURCHASE_ID}`;
    renderScreen(<PurchaseResultScreen />, {
      scenario: 'plan-con-checkout',
      prepare: () => {
        const db = loadDb();
        const workspace = db.workspaces[0];
        const studio = db.plans.find((item) => item.name === 'Estudio');
        if (!workspace || !studio) throw new Error('faltan datos');
        db.purchases.push({ id: PURCHASE_ID, workspace: workspace.id, status: 'approved', tokens: 0, amount_usd: 129, plan_id: studio.id, checkout_url: null, failure_reason: null, created_at: new Date().toISOString(), balance_after: null, _settleAt: null, _reject: false });
        saveDb(db);
      },
    });
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith(`/uso/planes?compra=${PURCHASE_ID}`));
    expect(screen.queryByText(/Compra acreditada/)).not.toBeInTheDocument();
  });

  it('sin el id de la compra ofrece volver a comprar', async () => {
    renderScreen(<PurchaseResultScreen />);
    expect(await screen.findByText('No encontramos esa compra')).toBeInTheDocument();
  });
});

describe('errores de carga: banner con Reintentar', () => {
  it('Resumen: si falla la billetera, el resto queda a la vista y Reintentar la trae', async () => {
    const user = userEvent.setup();
    const restore = failRead('/api/v1/wallet/');
    renderScreen(<UsageOverview />);
    const alert = await screen.findByRole('alert');
    expect(await screen.findByRole('region', { name: 'Plan actual' })).toBeInTheDocument();
    expect(screen.queryByText('Saldo disponible')).not.toBeInTheDocument();
    restore();
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('Saldo disponible')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
  });

  it('Movimientos: el banner reemplaza a la tabla hasta reintentar', async () => {
    const user = userEvent.setup();
    const restore = failRead(/\/api\/v1\/transactions\/(\?.*)?$/);
    renderScreen(<MovementsScreen />);
    const alert = await screen.findByRole('alert');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    restore();
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByRole('table', { name: /Movimientos de/ })).toBeInTheDocument();
  });

  it('Planes y Comprar tokens muestran el banner cuando no cargan sus datos', async () => {
    failRead('/api/v1/plans/');
    const plans = renderScreen(<PlansScreen />);
    expect(within(await screen.findByRole('alert')).getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
    plans.unmount();
    failRead('/api/v1/token-packages/');
    renderScreen(<BuyTokensScreen />);
    expect(within(await screen.findByRole('alert')).getByRole('button', { name: 'Reintentar' })).toBeInTheDocument();
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
  });
});
