// @vitest-environment node
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';
import { apiCall } from '@/api/client';
import * as billing from '@/api/contracts/billing';
import * as users from '@/api/contracts/users';
import { ApiError } from '@/api/errors';
import { workspaceDay } from '@/api/mocks/db';
import { USER_IDS, WORKSPACE_IDS } from '@/api/mocks/seed';
import { server } from '@/api/mocks/server';
import { reserveFunds, settleReservationPartially, walletOf } from '@/api/mocks/sim';
import { applyScenario, loadDb, mockTokens, saveDb } from '@/api/mocks/state';
import { setActiveWorkspaceId, setTokens } from '@/api/session';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

beforeEach(() => {
  applyScenario('estudio-norte');
  setTokens(mockTokens(USER_IDS.lucia));
  setActiveWorkspaceId(WORKSPACE_IDS.estudio);
});

const failure = async (promise: Promise<unknown>): Promise<ApiError> => {
  const error = await promise.catch((reason: unknown) => reason);
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
};

describe('mocks de Uso alineados con el backend', () => {
  it('un cobro parcial baja el SPEND a lo cobrado, lo deja COMPLETED y devuelve el resto sin otra fila', () => {
    const db = loadDb();
    const wallet = walletOf(db, WORKSPACE_IDS.estudio);
    const before = { available: wallet.available_balance, reserved: wallet.reserved_balance, rows: db.transactions.length };
    const reservation = reserveFunds(db, { workspace: WORKSPACE_IDS.estudio, amount: 100, userId: USER_IDS.lucia, description: 'Render', project: null, category: 'render' }, Date.now());
    expect(wallet.available_balance).toBe(before.available - 100);
    const returned = settleReservationPartially(db, reservation.id, 75, Date.now());
    expect(returned).toBe(25);
    expect(reservation).toMatchObject({ amount: 75, status: 'COMPLETED', kind: 'charged', transaction_type: 'SPEND' });
    expect(wallet.available_balance).toBe(before.available - 75);
    expect(wallet.reserved_balance).toBe(before.reserved);
    expect(db.transactions).toHaveLength(before.rows + 1);
    expect(db.transactions.some((item) => item.transaction_type === 'REFUND')).toBe(false);
  });

  it('los movimientos siempre traen los totales por tipo y el CSV usa el signo de la pantalla', async () => {
    const page = await apiCall(billing.listTransactions, { query: { kind: 'charged', page_size: 1 } });
    expect(page.totals.reserved?.count).toBeGreaterThan(0);
    expect(page.totals.released?.count).toBeGreaterThan(0);
    const csv = await apiCall(billing.exportTransactions, { query: {} });
    expect(csv.split('\n')[0]).toBe('fecha,concepto,proyecto,tipo,monto');
    // The kind as the screen names it, the amount last.
    const sign = (label: string) => csv.split('\n').filter((line) => line.includes(`,${label},`)).map((line) => line.split(',').pop()?.startsWith('-'));
    expect(new Set(sign('Devuelto'))).toEqual(new Set([false]));
    expect(new Set(sign('Cobrado'))).toEqual(new Set([true]));
    expect(new Set(sign('Compra'))).toEqual(new Set([false]));
  });

  it('como el backend: movimientos solo para Dueño y Admin, el CSV filtra por proyecto y el estimado valida el pedido', async () => {
    const released = loadDb().transactions.find((item) => item.kind === 'released' && item.project);
    if (!released?.project) throw new Error('sin reservas devueltas');
    const csv = await apiCall(billing.exportTransactions, { query: { project: released.project.id } });
    const rows = csv.split('\n').slice(1);
    expect(rows.length).toBeGreaterThan(0);
    expect(rows.every((line) => line.includes(released.project?.title ?? '?'))).toBe(true);
    expect(rows.some((line) => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}-03:00,/.test(line) && line.includes(',Devuelto,'))).toBe(true);

    const link = await failure(apiCall(billing.estimateCost, { body: { source: 'link', render_resolution: '1080p' } }));
    expect(link.status).toBe(400);
    expect(link.fieldErrors.render_resolution?.[0]).toMatch(/720p/);
    expect((await failure(apiCall(billing.estimateCost, { body: { source: 'file' } }))).fieldErrors.duration_seconds).toBeDefined();

    applyScenario('rol-editor');
    setTokens(mockTokens(USER_IDS.carla));
    expect((await failure(apiCall(billing.listTransactions, { query: { project: released.project.id } }))).status).toBe(403);
    expect((await apiCall(billing.estimateCost, { body: { source: 'file', duration_seconds: 60 } })).total).toBeGreaterThan(0);
  });

  it('un cobro cuenta en el consumo el día que se cobró, no el de la reserva', async () => {
    const db = loadDb();
    const reservation = reserveFunds(db, { workspace: WORKSPACE_IDS.estudio, amount: 40, userId: USER_IDS.lucia, description: 'Render', project: null, category: 'render' }, Date.now());
    reservation.created_at = '2026-11-30T12:00:00.000Z';
    settleReservationPartially(db, reservation.id, 40, Date.parse('2026-12-01T12:00:00.000Z'));
    saveDb(db);
    const november = await apiCall(billing.getUsage, { query: { month: '2026-11' } });
    const december = await apiCall(billing.getUsage, { query: { month: '2026-12' } });
    expect(december.find((day) => day.date === '2026-12-01')?.renders).toBeGreaterThanOrEqual(40);
    expect(november.find((day) => day.date === '2026-11-30')?.renders ?? 0).toBeLessThan(40);
  });

  it('el mes y los días se cuentan en la zona horaria del workspace', async () => {
    expect(workspaceDay('2026-12-01T02:30:00Z')).toBe('2026-11-30');
    expect(workspaceDay('2026-12-01T03:00:00Z')).toBe('2026-12-01');
    const db = loadDb();
    const charged = db.transactions.find((item) => item.kind === 'charged');
    if (!charged) throw new Error('sin cobros');
    charged.created_at = '2026-12-01T02:30:00.000Z';
    saveDb(db);
    const november = await apiCall(billing.listTransactions, { query: { month: '2026-11', kind: 'charged', page_size: 100 } });
    const december = await apiCall(billing.listTransactions, { query: { month: '2026-12', kind: 'charged', page_size: 100 } });
    expect(november.results.some((item) => item.id === charged.id)).toBe(true);
    expect(december.results.some((item) => item.id === charged.id)).toBe(false);
    const usage = await apiCall(billing.getUsage, { query: { month: '2026-11' } });
    expect(usage.find((day) => day.date === '2026-11-30')?.projects).toBeGreaterThanOrEqual(charged.amount);
  });

  it('elegir el plan actual cancela el cambio programado; sin cambio programado es un 400', async () => {
    const db = loadDb();
    const free = db.plans.find((plan) => plan.name === 'Gratis');
    const pro = db.plans.find((plan) => plan.name === 'Pro');
    const creator = db.plans.find((plan) => plan.name === 'Creador');
    if (!free || !pro || !creator) throw new Error('faltan planes');
    expect((await failure(apiCall(billing.changePlan, { body: { plan_id: pro.id } }))).status).toBe(400);
    // 4 seats fit in Creador's 5: the downgrade is scheduled, and choosing Pro again cancels it.
    const scheduled = await apiCall(billing.changePlan, { body: { plan_id: creator.id } });
    expect(scheduled.applied).toBe('period_end');
    expect(scheduled.subscription.pending_plan?.name).toBe('Creador');
    const cancelled = await apiCall(billing.changePlan, { body: { plan_id: pro.id } });
    expect(cancelled.applied).toBe('now');
    expect(cancelled.subscription.pending_plan).toBeNull();
    // 4 seats in use and Gratis has 2: the downgrade is rejected, nothing is scheduled.
    const rejected = await failure(apiCall(billing.changePlan, { body: { plan_id: free.id } }));
    expect(rejected.status).toBe(409);
    expect(rejected.code).toBe('seats_exceeded');
    expect((await apiCall(billing.getSubscription)).pending_plan).toBeNull();
  });

  it('cambiar el email sin la contraseña es un 400 en current_password', async () => {
    const error = await failure(apiCall(users.updateMe, { body: { email: 'lucia@nuevo.com' } }));
    expect(error.status).toBe(400);
    expect(error.fieldErrors.current_password?.[0]).toMatch(/contraseña/);
    const me = await apiCall(users.updateMe, { body: { email: 'lucia@nuevo.com', current_password: 'secreta' } });
    expect(me.pending_email).toBe('lucia@nuevo.com');
  });
});
