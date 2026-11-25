import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { setActiveWorkspaceId, WORKSPACE_STORAGE_KEY } from '@/api/session';
import { ToastProvider } from '@/ui/Toast';
import { LeaveGuardProvider, useLeaveGuard } from './leaveGuard';
import { WorkspaceElsewhereBanner } from './WorkspaceElsewhereBanner';

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }), usePathname: () => '/brand-kits/x' }));

const ALL = [
  { id: 'ws-1', name: 'Estudio Norte', role: 'owner', plan: null, available_balance: '1.00' },
  { id: 'ws-2', name: 'Canal de Martín', role: 'editor', plan: null, available_balance: '1.00' },
];
/** What the session says: the workspaces of the person and the one this tab is on. */
const session = vi.hoisted(() => ({ workspaces: [] as Array<{ id: string; name: string }>, current: 'ws-1', switchWorkspace: vi.fn() }));
vi.mock('./session', () => ({
  useSession: () => ({
    me: { workspaces: session.workspaces },
    workspace: session.workspaces.find((item) => item.id === session.current) ?? session.workspaces[0] ?? null,
    switchWorkspace: session.switchWorkspace,
  }),
}));

function Editor({ dirty }: { dirty: boolean }) {
  useLeaveGuard(dirty);
  return null;
}
const app = (dirty: boolean) => (
  <ToastProvider>
    <LeaveGuardProvider>
      <Editor dirty={dirty} />
      <WorkspaceElsewhereBanner />
    </LeaveGuardProvider>
  </ToastProvider>
);
const otherTabSwitches = (from: string, to: string) => {
  window.localStorage.setItem(WORKSPACE_STORAGE_KEY, to);
  act(() => void window.dispatchEvent(new StorageEvent('storage', { key: WORKSPACE_STORAGE_KEY, oldValue: from, newValue: to })));
};

afterEach(() => {
  setActiveWorkspaceId(null);
  session.switchWorkspace.mockReset();
});

describe('aviso de «cambiaste de workspace en otra pestaña»', () => {
  it('con cambios sin guardar dice por qué se queda; una vez guardado, solo dónde está cada pestaña y cómo pasar', async () => {
    session.workspaces = ALL;
    session.current = 'ws-1';
    setActiveWorkspaceId('ws-1');
    window.history.pushState(null, '', '/brand-kits');
    window.history.pushState(null, '', '/brand-kits/x');
    const view = render(app(true));
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    otherTabSwitches('ws-1', 'ws-2');
    expect(screen.getByRole('status')).toHaveTextContent('Cambiaste a Canal de Martín en otra pestaña. Esta sigue en Estudio Norte para no perder tus cambios.');

    // Saved: nothing is at risk any more, and the banner stops saying so.
    view.rerender(app(false));
    await act(() => new Promise((resolve) => setTimeout(resolve, 30)));
    const banner = screen.getByRole('status');
    expect(banner).toHaveTextContent('Cambiaste a Canal de Martín en otra pestaña. Esta sigue en Estudio Norte.');
    expect(banner).not.toHaveTextContent('para no perder tus cambios');
    act(() => screen.getByRole('button', { name: 'Cambiar a Canal de Martín' }).click());
    expect(session.switchWorkspace).toHaveBeenCalledWith('ws-2');
  });

  it('si el workspace en el que se había quedado ya no es de la persona, avisa que pasó a otro y que lo sin guardar se perdió', async () => {
    session.workspaces = ALL;
    session.current = 'ws-1';
    setActiveWorkspaceId('ws-1');
    const view = render(app(true));
    otherTabSwitches('ws-1', 'ws-2');
    expect(screen.getByRole('status')).toHaveTextContent('Esta sigue en Estudio Norte');

    // `/me` comes back without Estudio Norte (deleted, or the person was taken out): the session falls back to the other one.
    session.workspaces = ALL.filter((item) => item.id !== 'ws-1');
    session.current = 'ws-2';
    view.rerender(app(true));
    act(() => setActiveWorkspaceId('ws-2'));
    expect(await screen.findByText('Ya no tenés acceso a Estudio Norte: esta pestaña pasó a Canal de Martín. Lo que no habías guardado no se puede guardar.')).toBeInTheDocument();
    // Nothing left to offer: both tabs are on the same workspace.
    expect(screen.queryByText(/en otra pestaña/)).not.toBeInTheDocument();
  });
});
