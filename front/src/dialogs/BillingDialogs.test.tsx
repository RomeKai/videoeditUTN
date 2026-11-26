import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { allowSlowScreens, editDb, renderScreen, startMockApi, startScenario } from '@/testing';
import { useBillingDialogs } from './BillingDialogs';

startMockApi();
allowSlowScreens();

/** What a screen does with a 403 `seats_exceeded` (Miembros › Invitar): opens "Límite del plan" for the seats. */
function Opener() {
  const billing = useBillingDialogs();
  return (
    <button type="button" onClick={() => billing.showPlanLimit({ kind: 'seats' })}>
      Invitar
    </button>
  );
}

/** The seats of each plan, set in the test: Gratis 2, Creador 5, Pro 5, Estudio 15. */
function seats(byName: Record<string, number>) {
  editDb((db) => {
    for (const plan of db.plans) if (byName[plan.name] !== undefined) plan.max_members = byName[plan.name] as number;
  });
}

describe('D3 · Límite del plan: lugares', () => {
  it('un workspace Gratis sin lugares: propone Creador, el primer plan con más lugares', async () => {
    startScenario('plan-gratis');
    seats({ Gratis: 2, Creador: 5, Pro: 5, Estudio: 15 });
    const user = userEvent.setup();
    renderScreen(<Opener />);
    await user.click(screen.getByRole('button', { name: 'Invitar' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'No quedan lugares en tu plan' });
    // The current plan, with its seats.
    expect(await within(dialog).findByText('Tu plan')).toBeInTheDocument();
    expect(within(dialog).getByText('2 miembros')).toBeInTheDocument();
    // The suggestion: the cheapest plan that has more, with what it gives and what it costs.
    const suggestion = await within(dialog).findByRole('link', { name: /Lo permite/ });
    expect(suggestion).toHaveTextContent('Creador');
    expect(suggestion).toHaveTextContent('5 miembros');
    expect(suggestion).toHaveAttribute('href', '/uso/planes');
    expect(within(dialog).queryByText('Pro')).toBeNull();
    expect(within(dialog).queryByText('Estudio')).toBeNull();
  });

  it('si Creador tiene los mismos lugares que Gratis, no lo propone: salta al primero que tiene más', async () => {
    startScenario('plan-gratis');
    seats({ Gratis: 2, Creador: 2, Pro: 5, Estudio: 15 });
    const user = userEvent.setup();
    renderScreen(<Opener />);
    await user.click(screen.getByRole('button', { name: 'Invitar' }));
    const dialog = await screen.findByRole('alertdialog', { name: 'No quedan lugares en tu plan' });
    const suggestion = await within(dialog).findByRole('link', { name: /Lo permite/ });
    expect(suggestion).toHaveTextContent('Pro');
    expect(suggestion).toHaveTextContent('5 miembros');
  });
});
