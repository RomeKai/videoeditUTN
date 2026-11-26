import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { HYBRID_MAINTENANCE_REASON, HYBRID_ONLY_MOCK_REASON, SCENARIOS } from '@/api/mocks/scenarios';
import { CONTRACT } from '@/api/contracts';
import { clearRouteOverrides, forceRouting, ROUTE_OVERRIDES_KEY } from '@/api/routing';
import { AUTH_LOCKED_REASON } from '@/api/routingTable';
import { ApiRoutingPanel } from './ApiRoutingPanel';
import { MockPanel } from './MockPanel';
import { ScenarioPicker } from './ScenarioPicker';

const row = (title: string) => screen.getByText(title).closest('li') as HTMLElement;
const stored = () => JSON.parse(window.localStorage.getItem(ROUTE_OVERRIDES_KEY) ?? '{}') as Record<string, string>;

// An explicit list: these tests do not depend on which endpoints the contract marks as existing today.
beforeEach(() => forceRouting({ mode: 'hybrid', real: 'projects.list,projects.detail,brandkits' }));

const existing = (domain: string) => CONTRACT.find((group) => group.domain === domain)?.endpoints.filter((endpoint) => endpoint.backend.status !== 'proposed').length ?? 0;
afterEach(() => {
  forceRouting(null);
  clearRouteOverrides();
  vi.restoreAllMocks();
});

describe('panel de origen por dominio (modo híbrido)', () => {
  it('muestra cada dominio como Real, Mock o Mixto', () => {
    render(<ApiRoutingPanel onChange={() => undefined} />);
    expect(screen.getAllByRole('listitem')).toHaveLength(10);
    expect(row('Acceso')).toHaveAttribute('data-route', 'real');
    expect(within(row('Acceso')).getByText('Real', { selector: 'span' })).toBeInTheDocument();
    expect(row('Planes, billetera, movimientos, compras y precios')).toHaveAttribute('data-route', 'mock');
    expect(within(row('Planes, billetera, movimientos, compras y precios')).getByText('0 de 12 endpoints van al backend.')).toBeInTheDocument();
    expect(row('Proyectos')).toHaveAttribute('data-route', 'mixed');
    expect(within(row('Proyectos')).getByText('Mixto')).toBeInTheDocument();
    expect(within(row('Proyectos')).getByText(/2 de 13 endpoints van al backend/)).toBeInTheDocument();
    expect(row('Brand kits')).toHaveAttribute('data-route', 'real');
  });

  it('sin lista, cada dominio manda al backend lo que el contrato marca como existente', () => {
    forceRouting({ mode: 'hybrid', real: '' });
    render(<ApiRoutingPanel onChange={() => undefined} />);
    const count = existing('projects');
    expect(count).toBeGreaterThan(0);
    expect(within(row('Proyectos')).getByText(new RegExp(`${count} de 13 endpoints van al backend: los que el contrato marca como existentes\\.`))).toBeInTheDocument();
    expect(within(row('Notificaciones')).getByText(`${existing('notifications')} de 3 endpoints van al backend.`)).toBeInTheDocument();
  });

  it('con NEXT_PUBLIC_API_REAL los valores por defecto salen de la lista', () => {
    forceRouting({ mode: 'hybrid', real: 'billing' });
    render(<ApiRoutingPanel onChange={() => undefined} />);
    expect(row('Planes, billetera, movimientos, compras y precios')).toHaveAttribute('data-route', 'real');
    expect(row('Proyectos')).toHaveAttribute('data-route', 'mock');
  });

  it('Acceso no se puede cambiar, y dice por qué', () => {
    render(<ApiRoutingPanel onChange={() => undefined} />);
    const access = within(row('Acceso'));
    expect(access.getByText(AUTH_LOCKED_REASON)).toBeInTheDocument();
    for (const button of access.getAllByRole('button')) expect(button).toBeDisabled();
  });

  it('cambiar un dominio lo guarda en localStorage y avisa para recargar', async () => {
    const onChange = vi.fn();
    render(<ApiRoutingPanel onChange={onChange} />);
    const billing = () => within(row('Planes, billetera, movimientos, compras y precios'));
    expect(billing().getByRole('button', { name: 'Mock' })).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(billing().getByRole('button', { name: 'Real' }));
    expect(stored()).toEqual({ billing: 'real' });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(row('Planes, billetera, movimientos, compras y precios')).toHaveAttribute('data-route', 'real');
    expect(billing().getByRole('button', { name: 'Real' })).toHaveAttribute('aria-pressed', 'true');
    expect(billing().getByText(/Cambiado a mano en este navegador \(el env dice mock\)/)).toBeInTheDocument();
    // Back to what the env says: the override is removed, not pinned.
    await userEvent.click(billing().getByRole('button', { name: 'Mock' }));
    expect(window.localStorage.getItem(ROUTE_OVERRIDES_KEY)).toBeNull();
  });

  it('un dominio mixto se puede mandar entero a un lado, y «Volver a lo que dice el env» lo deshace', async () => {
    render(<ApiRoutingPanel onChange={() => undefined} />);
    expect(screen.queryByRole('button', { name: 'Volver a lo que dice el env' })).not.toBeInTheDocument();
    await userEvent.click(within(row('Proyectos')).getByRole('button', { name: 'Mock' }));
    expect(stored()).toEqual({ projects: 'mock' });
    expect(row('Proyectos')).toHaveAttribute('data-route', 'mock');
    await userEvent.click(screen.getByRole('button', { name: 'Volver a lo que dice el env' }));
    expect(window.localStorage.getItem(ROUTE_OVERRIDES_KEY)).toBeNull();
    expect(row('Proyectos')).toHaveAttribute('data-route', 'mixed');
  });

  it('el cambio de un dominio se puede quitar sin tocar los demás', async () => {
    window.localStorage.setItem(ROUTE_OVERRIDES_KEY, JSON.stringify({ social: 'real', billing: 'real' }));
    const onChange = vi.fn();
    render(<ApiRoutingPanel onChange={onChange} />);
    await userEvent.click(screen.getByRole('button', { name: 'Quitar el cambio de Redes sociales y publicaciones' }));
    expect(stored()).toEqual({ billing: 'real' });
    expect(row('Redes sociales y publicaciones')).toHaveAttribute('data-route', 'mock');
    expect(screen.queryByRole('button', { name: 'Quitar el cambio de Redes sociales y publicaciones' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Quitar el cambio de Planes/ })).toBeInTheDocument();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('avisa si NEXT_PUBLIC_API_REAL nombra algo que no está en el contrato', () => {
    forceRouting({ mode: 'hybrid', real: 'proyects,brandkits' });
    render(<ApiRoutingPanel onChange={() => undefined} />);
    expect(screen.getByRole('alert')).toHaveTextContent('nombra algo que no está en el contrato y se ignora: proyects.');
  });

  it('lee el cambio guardado al abrir', () => {
    window.localStorage.setItem(ROUTE_OVERRIDES_KEY, JSON.stringify({ social: 'real' }));
    render(<ApiRoutingPanel onChange={() => undefined} />);
    expect(row('Redes sociales y publicaciones')).toHaveAttribute('data-route', 'real');
  });

  it('si localStorage falla, el panel usa lo del env y no se rompe al cambiar', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    const onChange = vi.fn();
    render(<ApiRoutingPanel onChange={onChange} />);
    expect(row('Proyectos')).toHaveAttribute('data-route', 'mixed');
    await userEvent.click(within(row('Proyectos')).getByRole('button', { name: 'Mock' }));
    expect(onChange).toHaveBeenCalled();
  });
});

describe('escenarios en modo híbrido', () => {
  it('deshabilita, con el motivo, los que solo tienen sentido con todo simulado', () => {
    render(<ScenarioPicker hybrid />);
    const button = (label: string) => screen.getByText(label, { selector: 'span' }).closest('button') as HTMLButtonElement;
    for (const label of ['Usuario nuevo', 'Workspace vacío', 'Todos los estados', 'Rol Admin', 'Rol Lector', 'Sin sesión', 'Sesión vencida']) {
      expect(button(label), label).toBeDisabled();
      expect(within(button(label)).getByText(HYBRID_ONLY_MOCK_REASON)).toBeInTheDocument();
    }
    expect(button('Mantenimiento')).toBeDisabled();
    expect(within(button('Mantenimiento')).getByText(HYBRID_MAINTENANCE_REASON)).toBeInTheDocument();
    for (const label of ['Estudio Norte', 'Saldo bajo', 'Plan Gratis', 'Pago rechazado', 'Servidor caído']) expect(button(label), label).toBeEnabled();
    expect(SCENARIOS.filter((scenario) => scenario.hybrid).map((scenario) => scenario.id)).toEqual([
      'estudio-norte',
      'saldo-bajo',
      'plan-gratis',
      'plan-creador',
      'plan-estudio',
      'pago-rechazado',
      'plan-con-checkout',
      'servidor-caido',
    ]);
  });

  it('en modo mock están todos habilitados', () => {
    forceRouting({ mode: 'mock' });
    render(<ScenarioPicker hybrid={false} />);
    for (const button of screen.getAllByRole('button')) expect(button).toBeEnabled();
    expect(screen.queryByText(HYBRID_ONLY_MOCK_REASON)).not.toBeInTheDocument();
  });
});

describe('panel flotante', () => {
  it('en híbrido dice cuántos dominios tienen backend real y muestra el origen por dominio', async () => {
    render(<MockPanel hybrid />);
    const fab = screen.getByRole('button', { name: /Híbrido: 3 de 10 dominios con backend real/ });
    await userEvent.click(fab);
    expect(screen.getByRole('group', { name: 'Origen de cada dominio de la API' })).toBeInTheDocument();
    expect(screen.getByText(/una parte de la API es el backend real/)).toBeInTheDocument();
  });

  it('en mock sigue como antes: sin origen por dominio', async () => {
    forceRouting({ mode: 'mock' });
    render(<MockPanel hybrid={false} />);
    await userEvent.click(screen.getByRole('button', { name: /Mocks: Estudio Norte/ }));
    expect(screen.queryByRole('group', { name: 'Origen de cada dominio de la API' })).not.toBeInTheDocument();
    expect(screen.getByText('La API está simulada en el navegador.')).toBeInTheDocument();
  });
});
