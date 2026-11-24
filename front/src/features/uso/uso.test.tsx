import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Plan } from '@/api/contracts/billing';
import { loadDb, saveDb } from '@/api/mocks/state';
import { BuyTokensScreen, parseTokenAmount } from './BuyTokensScreen';
import { leaveTo } from './leave';
import { movementConcept, signedAmount } from './movements';
import { MovementsScreen } from './MovementsScreen';
import { monthKey, monthLong, monthTitle, recentMonths, shiftMonth } from './period';
import { planFeatures, planLosses } from './PlanCard';
import { PlansScreen } from './PlansScreen';
import { PricingScreen } from './PricingScreen';
import { PurchaseResultScreen } from './PurchaseResultScreen';
import { renderScreen, startMockApi } from './test-utils';
import { gridStep, usageTotals } from './UsageChart';
import { UsageHeader } from './UsageHeader';
import { UsageOverview } from './UsageOverview';

const navigation = vi.hoisted(() => ({ pathname: '/uso', search: '' }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(navigation.search),
  useParams: () => ({}),
}));
vi.mock('./leave', () => ({ leaveTo: vi.fn() }));
const download = vi.hoisted(() => ({ downloadText: vi.fn() }));
vi.mock('@/lib/download', () => download);

startMockApi();

beforeEach(() => {
  navigation.pathname = '/uso';
  navigation.search = '';
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
  max_members: 5,
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
    expect(recentMonths(3, november)).toEqual(['2026-11', '2026-10', '2026-09']);
    expect(monthTitle('2026-11')).toBe('Noviembre 2026');
    expect(monthLong('2026-11')).toBe('noviembre de 2026');
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
    expect(parseTokenAmount('12,5')).toBeNull();
    expect(parseTokenAmount('0')).toBeNull();
    expect(parseTokenAmount('abc')).toBeNull();
  });

  it('dice qué se pierde al bajar de plan y qué plan habilita cada función', () => {
    const pro = plan({});
    const creator = plan({ name: 'Creador', sort_order: 2, max_video_duration_seconds: 3600, max_resolution: '1080p', allow_crossposting: false, base_discount_rate: 0.05, max_members: 2 });
    const free = plan({ name: 'Gratis', sort_order: 1, has_watermark: true, allow_scheduling: false, allow_crossposting: false, has_seo_optimization: false, base_discount_rate: 0, max_members: 1 });
    expect(planLosses(pro, creator)).toEqual([
      'Videos de hasta 60 min (hoy 180 min)',
      'Resolución hasta 1080p (hoy 4K)',
      'Publicar en varias redes a la vez',
      'Miembros: 2 (hoy 5)',
      'Descuento en tokens: 5 % (hoy 10 %)',
    ]);
    expect(planLosses(creator, pro)).toEqual([]);
    const features = planFeatures(free, [free, creator, pro]);
    expect(features.find((item) => item.key === 'crossposting')).toMatchObject({ included: false, availableIn: 'Pro' });
    expect(features.find((item) => item.key === 'watermark')).toMatchObject({ included: false, availableIn: 'Creador' });
    expect(features.find((item) => item.key === 'members')).toMatchObject({ included: true, value: '1' });
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
    expect(await screen.findByRole('img', { name: /Consumo por día en/ })).toBeInTheDocument();
    expect(screen.queryByText('Te queda poco saldo')).not.toBeInTheDocument();
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
    expect(filename).toMatch(/^movimientos-\d{4}-\d{2}\.csv$/);
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
  it('marca el plan actual y, al bajar, muestra qué se pierde antes de programar el cambio', async () => {
    const user = userEvent.setup();
    renderScreen(<PlansScreen />);
    const pro = await screen.findByRole('article', { name: 'Plan Pro, tu plan' });
    expect(within(pro).getAllByText('Tu plan').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Subir a Estudio' })).toBeEnabled();

    await user.click(screen.getByRole('button', { name: 'Bajar a Creador' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Bajar a Creador?' });
    expect(within(dialog).getByText('Publicar en varias redes a la vez')).toBeInTheDocument();
    expect(within(dialog).getByText('Miembros: 2 (hoy 5)')).toBeInTheDocument();
    expect(await within(dialog).findByText(/Hoy ocupás 4 lugares y Creador tiene 2/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Bajar a Creador' }));

    expect(await screen.findByText(/^Tu plan pasa a Creador el /, { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByRole('article', { name: 'Plan Pro, tu plan' })).toBeInTheDocument();
    expect(within(screen.getByRole('article', { name: 'Plan Creador' })).getByText('Próximo plan')).toBeInTheDocument();
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

  it('un Editor ve los planes pero no puede cambiarlos', async () => {
    renderScreen(<PlansScreen />, { scenario: 'rol-editor' });
    await screen.findByRole('article', { name: 'Plan Pro, tu plan' });
    expect(screen.getByRole('button', { name: 'Subir a Estudio' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: 'Bajar a Gratis' })).toHaveAttribute('aria-disabled', 'true');
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
    renderScreen(<PurchaseResultScreen />, { prepare: () => seedPurchase('approved') });
    expect((await screen.findAllByText(/Compra acreditada:/)).length).toBeGreaterThan(0);
    expect(await screen.findByRole('link', { name: 'Ver movimiento' })).toHaveAttribute('href', '/uso/movimientos');
  });

  it('pago rechazado: el aviso queda arriba y el saldo no cambia', async () => {
    navigation.search = `compra=${PURCHASE_ID}`;
    renderScreen(<PurchaseResultScreen />, { scenario: 'pago-rechazado', prepare: () => seedPurchase('rejected') });
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText('El pago no se completó. No se te cobró nada.')).toBeInTheDocument();
    expect(await within(alert).findByText(/Tu saldo sigue en/)).toHaveTextContent('4.860');
    expect(within(alert).getByRole('link', { name: 'Reintentar' })).toHaveAttribute('href', '/uso/comprar');
  });

  it('sin el id de la compra ofrece volver a comprar', async () => {
    renderScreen(<PurchaseResultScreen />);
    expect(await screen.findByText('No encontramos esa compra')).toBeInTheDocument();
  });
});
