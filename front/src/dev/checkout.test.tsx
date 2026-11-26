import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import MockCheckoutPage from '@/app/dev/checkout/page';
import { API_URL } from '@/api/config';
import real from '@/api/contracts/drf/responses.json';
import { server } from '@/api/mocks/server';
import { forceRouting } from '@/api/routing';
import { leaveTo } from '@/features/uso/leave';
import { db, editDb, renderScreen, signIn, startMockApi } from '@/testing';

const navigation = vi.hoisted(() => ({ search: '' }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => '/dev/checkout',
  useSearchParams: () => new URLSearchParams(navigation.search),
  useParams: () => ({}),
}));
vi.mock('@/features/uso/leave', () => ({ leaveTo: vi.fn() }));

startMockApi();

const PURCHASE_ID = '33333333-3333-4333-8333-333333333333';
const at = (id: string, volver: string) => `compra=${id}&volver=${encodeURIComponent(`${window.location.origin}${volver}`)}`;

beforeEach(() => signIn('estudio-norte'));
afterEach(() => {
  forceRouting(null);
  vi.mocked(leaveTo).mockReset();
});

describe('checkout de prueba (/dev/checkout)', () => {
  it('con mocks, la decisión queda en la base simulada y vuelve con la compra', async () => {
    forceRouting({ mode: 'mock' });
    editDb((current) => {
      const workspace = current.workspaces[0];
      if (!workspace) throw new Error('falta el workspace');
      current.purchases.push({
        id: PURCHASE_ID,
        workspace: workspace.id,
        status: 'pending',
        tokens: 1000,
        amount_usd: 10,
        plan_id: null,
        checkout_url: null,
        failure_reason: null,
        created_at: new Date().toISOString(),
        balance_after: null,
        _settleAt: null,
        _reject: false,
      });
    });
    navigation.search = at(PURCHASE_ID, '/uso/comprar/resultado');
    renderScreen(<MockCheckoutPage />);

    await userEvent.click(await screen.findByRole('button', { name: 'Simular pago rechazado' }));

    expect(db().purchases.find((item) => item.id === PURCHASE_ID)?._reject).toBe(true);
    expect(leaveTo).toHaveBeenCalledWith(`${window.location.origin}/uso/comprar/resultado?compra=${PURCHASE_ID}`);
  });

  it('con billing en el backend, lee el pago de ahí y lo decide con billing.simulatePurchase', async () => {
    forceRouting({ mode: 'hybrid', real: 'billing' });
    const payment = real.billing_plan_payment_pending;
    const sent: unknown[] = [];
    server.use(
      http.get(`${API_URL}/api/v1/token-purchases/:id/`, () => HttpResponse.json(payment)),
      http.get(`${API_URL}/api/v1/plans/`, () => HttpResponse.json([real.billing_change_plan_period_end.subscription.plan])),
      http.post(`${API_URL}/api/v1/token-purchases/:id/simulate/`, async ({ request, params }) => {
        sent.push({ id: params.id, body: await request.json() });
        return HttpResponse.json(real.billing_plan_payment_approved);
      }),
    );
    navigation.search = at(payment.id, '/uso/planes');
    renderScreen(<MockCheckoutPage />);

    expect(await screen.findByText(/Plan Pro/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Pagar' }));

    await waitFor(() => expect(leaveTo).toHaveBeenCalledWith(`${window.location.origin}/uso/planes?compra=${payment.id}`));
    expect(sent).toEqual([{ id: payment.id, body: { approved: true } }]);
  });

  it('un pago del backend ya resuelto no se decide otra vez', async () => {
    forceRouting({ mode: 'hybrid', real: 'billing' });
    const payment = real.billing_token_purchase_approved;
    server.use(http.get(`${API_URL}/api/v1/token-purchases/:id/`, () => HttpResponse.json(payment)));
    navigation.search = at(payment.id, '/uso/comprar/resultado');
    renderScreen(<MockCheckoutPage />);

    expect(await screen.findByText('Este pago ya está resuelto')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Pagar' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Ver el resultado' }));
    expect(leaveTo).toHaveBeenCalledWith(`${window.location.origin}/uso/comprar/resultado?compra=${payment.id}`);
  });
});
