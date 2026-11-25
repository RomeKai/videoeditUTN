import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse, delay } from 'msw';
import { API_URL } from '@/api/config';
import { server } from '@/api/mocks/server';
import { loadDb } from '@/api/mocks/state';
import { failRead, renderScreen, startMockApi } from '../uso/test-utils';
import { GeneralSettings } from './GeneralSettings';
import { parseEmails } from './InviteMemberDialog';
import { MembersScreen } from './MembersScreen';
import { NetworksScreen } from './NetworksScreen';
import { NotificationsScreen } from './NotificationsScreen';
import { ProfileScreen } from './ProfileScreen';
import { SettingsShell } from './SectionShell';
import { membersSummary, networksSummary } from './summaries';

const navigation = vi.hoisted(() => ({ pathname: '/ajustes', replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: navigation.replace, back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

startMockApi();

beforeEach(() => {
  navigation.pathname = '/ajustes';
  navigation.replace.mockClear();
});

describe('utilidades de Ajustes', () => {
  it('separa los emails por coma, espacio o punto y coma, sin repetidos', () => {
    expect(parseEmails(' Juan@Ejemplo.com, ana@ejemplo.com;juan@ejemplo.com\nleo@ejemplo.com ')).toEqual(['juan@ejemplo.com', 'ana@ejemplo.com', 'leo@ejemplo.com']);
    expect(parseEmails('  ')).toEqual([]);
  });

  it('resume miembros y redes para la lista del teléfono', () => {
    const member = (status: 'active' | 'pending') => ({ id: 'a', user: null, email: 'a@b.co', role: 'editor' as const, status, joined_at: '2026-11-01T00:00:00Z' });
    expect(membersSummary({ members: [member('active'), member('active'), member('pending')], max_members: 5, seats_used: 3 })).toBe('2 de 5 miembros · 1 invitación pendiente');
    expect(membersSummary({ members: [member('active')], max_members: 1, seats_used: 1 })).toBe('1 de 1 miembro');
    expect(networksSummary([{ status: 'connected' }, { status: 'connected' }, { status: 'expired' }])).toBe('2 conectadas · 1 vencida');
    expect(networksSummary([{ status: 'disconnected' }])).toBe('Ninguna conectada');
  });
});

describe('H1 · General', () => {
  it('el dueño renombra el workspace: Guardar se activa solo con cambios', async () => {
    const user = userEvent.setup();
    renderScreen(<GeneralSettings />);
    const input = await screen.findByLabelText('Nombre del workspace');
    expect(input).toHaveValue('Estudio Norte');
    const save = screen.getByRole('button', { name: 'Guardar' });
    expect(save).toBeDisabled();
    expect(screen.getByText('No hay cambios para guardar.')).toBeInTheDocument();

    await user.clear(input);
    expect(await screen.findByText('El workspace necesita un nombre.')).toBeInTheDocument();
    expect(save).toBeDisabled();
    await user.type(input, 'Estudio Sur');
    expect(save).toBeEnabled();
    await user.click(save);
    expect(await screen.findByText(/El workspace ahora se llama Estudio Sur/)).toBeInTheDocument();
    await waitFor(() => expect(loadDb().workspaces[0]?.name).toBe('Estudio Sur'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled());
  });

  it('borrar pide escribir el nombre del workspace', async () => {
    const user = userEvent.setup();
    renderScreen(<GeneralSettings />);
    await user.click(await screen.findByRole('button', { name: 'Borrar el workspace…' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Borrar Estudio Norte?' });
    const confirm = within(dialog).getByRole('button', { name: 'Borrar el workspace' });
    expect(confirm).toBeDisabled();
    await user.type(within(dialog).getByRole('textbox'), 'Estudio Norte');
    expect(confirm).toBeEnabled();
    expect(within(dialog).queryByText('Es tu único workspace')).not.toBeInTheDocument();
    const deletedId = loadDb().workspaces.find((workspace) => workspace.name === 'Estudio Norte')?.id ?? '';
    // A handler that answers nothing sees every request and lets the real mock respond.
    const requests: Array<{ method: string; url: string; workspace: string | null }> = [];
    server.use(
      http.all('*', ({ request }) => {
        requests.push({ method: request.method, url: request.url, workspace: request.headers.get('X-Workspace-Id') });
      }),
    );
    await user.click(confirm);
    await waitFor(() => expect(loadDb().workspaces.some((workspace) => workspace.name === 'Estudio Norte')).toBe(false));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/proyectos'));
    // Pasa al otro workspace de Lucía y no vuelve a pedir nada del borrado.
    expect(await screen.findByLabelText('Nombre del workspace')).toHaveValue('Canal de Martín');
    const afterDelete = requests.slice(requests.findIndex((request) => request.method === 'DELETE') + 1);
    expect(requests.some((request) => request.method === 'DELETE')).toBe(true);
    expect(afterDelete.length).toBeGreaterThan(0);
    expect(afterDelete.filter((request) => request.url.includes(deletedId) || request.workspace === deletedId)).toEqual([]);
  });

  it('borrar el único workspace lo avisa antes', async () => {
    const user = userEvent.setup();
    renderScreen(<GeneralSettings />, { scenario: 'usuario-nuevo' });
    await user.click(await screen.findByRole('button', { name: 'Borrar el workspace…' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Borrar Mi Espacio?' });
    expect(within(dialog).getByText('Es tu único workspace')).toBeInTheDocument();
  });

  it('transferir ofrece a los demás miembros activos', async () => {
    const user = userEvent.setup();
    renderScreen(<GeneralSettings />);
    await user.click(await screen.findByRole('button', { name: 'Transferir…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Transferir Estudio Norte' });
    const select = await within(dialog).findByLabelText('Nuevo dueño');
    expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual(['Elegí a alguien', 'Martín Sosa (Admin)', 'Carla Ruiz (Editor)', 'Diego Paz (Lector)']);
    // Nadie viene elegido: hasta elegir no se puede seguir.
    expect(select).toHaveValue('');
    expect(within(dialog).getByRole('button', { name: 'Transferir el workspace' })).toBeDisabled();
    await user.selectOptions(select, 'Carla Ruiz (Editor)');
    await user.click(within(dialog).getByRole('button', { name: 'Transferir el workspace' }));
    const confirm = await screen.findByRole('alertdialog', { name: '¿Transferir Estudio Norte a Carla Ruiz?' });
    expect(loadDb().workspaces[0]?.owner).not.toBe(loadDb().users.find((item) => item.name === 'Carla Ruiz')?.id);
    await user.click(within(confirm).getByRole('button', { name: 'Sí, transferir' }));
    expect(await screen.findByText(/Carla Ruiz es ahora la persona dueña de Estudio Norte/)).toBeInTheDocument();
    // Ya no es la dueña: la zona de peligro deja de mostrarse.
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'Zona de peligro' })).not.toBeInTheDocument());
  });

  it('un Admin renombra pero no ve la zona de peligro', async () => {
    renderScreen(<GeneralSettings />, { scenario: 'rol-admin' });
    expect(await screen.findByLabelText('Nombre del workspace')).toBeEnabled();
    expect(screen.queryByRole('heading', { name: 'Zona de peligro' })).not.toBeInTheDocument();
  });

  it('un Editor ve el nombre sin poder cambiarlo, y Ajustes no le muestra las secciones de admin', async () => {
    renderScreen(
      <SettingsShell>
        <GeneralSettings />
      </SettingsShell>,
      { scenario: 'rol-editor' },
    );
    expect(await screen.findByLabelText('Nombre del workspace')).toBeDisabled();
    expect(screen.getByText('Pedile a un admin que lo cambie.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Zona de peligro' })).not.toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Secciones de ajustes' });
    expect(within(nav).getAllByRole('link').map((link) => link.getAttribute('href'))).toEqual(['/ajustes']);
  });
});

describe('H2 · Miembros', () => {
  it('invita por email con rol (D7) y la invitación ocupa un lugar', async () => {
    const user = userEvent.setup();
    renderScreen(<MembersScreen />);
    expect(await screen.findByText('4 de 5 miembros')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Invitar' }));
    const dialog = await screen.findByRole('dialog', { name: 'Invitar a Estudio Norte' });
    expect(within(dialog).getByText(/Queda/)).toHaveTextContent('Queda 1 lugar en el plan Pro');

    const emails = within(dialog).getByLabelText('Emails');
    await user.type(emails, 'juan');
    await user.click(within(dialog).getByRole('button', { name: 'Enviar invitación' }));
    expect(await within(dialog).findByText('«juan» no es un email válido.')).toBeInTheDocument();
    await user.clear(emails);
    await user.type(emails, 'juan@ejemplo.com, ana@ejemplo.com');
    expect(await within(dialog).findByText(/Escribiste 2 emails y queda 1 lugar/)).toBeInTheDocument();
    await user.clear(emails);
    await user.type(emails, 'carla@estudionorte.com');
    expect(await within(dialog).findByText(/ya es miembro o tiene una invitación pendiente/)).toBeInTheDocument();

    await user.clear(emails);
    await user.type(emails, 'juan@ejemplo.com');
    await user.selectOptions(within(dialog).getByLabelText('Rol'), 'Lector');
    expect(within(dialog).getByText(/^Lector: ve proyectos/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Esta invitación ocupa el último lugar/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Enviar invitación' }));

    expect(await screen.findByText('Invitación enviada a juan@ejemplo.com')).toBeInTheDocument();
    const row = (await screen.findByText('juan@ejemplo.com')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Invitación pendiente')).toBeInTheDocument();
    expect(within(row).getByRole('button', { name: /Rol de juan@ejemplo.com: Lector/ })).toBeInTheDocument();
    expect(await screen.findByText('4 de 5 miembros · 1 invitación pendiente')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Invitar' })).toHaveAttribute('aria-disabled', 'true');
    // A long flow (dialog, four validations, the invitation): on a busy machine it needs more than the default 5 s.
  }, 30_000);

  it('con el plan lleno «Invitar» queda deshabilitado y explica por qué', async () => {
    const user = userEvent.setup();
    renderScreen(<MembersScreen />, { scenario: 'miembros-al-limite' });
    expect(await screen.findByText('4 de 5 miembros · 1 invitación pendiente')).toBeInTheDocument();
    const invite = screen.getByRole('button', { name: 'Invitar' });
    expect(invite).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText(/La invitación pendiente ocupa el último lugar del plan Pro/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'cambiá de plan' })).toHaveAttribute('href', '/uso/planes');
    await user.click(invite);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    // Reenviar y después quitar la invitación libera el lugar.
    await user.click(screen.getByRole('button', { name: 'Reenviar la invitación a juan@estudionorte.com' }));
    expect(await screen.findByText('Reenviamos la invitación a juan@estudionorte.com')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /Más acciones para juan@estudionorte.com/ }));
    await user.click(await screen.findByRole('menuitem', { name: 'Quitar la invitación' }));
    const confirm = await screen.findByRole('alertdialog', { name: '¿Quitar la invitación a juan@estudionorte.com?' });
    await user.click(within(confirm).getByRole('button', { name: 'Quitar la invitación' }));
    expect(await screen.findByText('4 de 5 miembros')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Invitar' })).not.toHaveAttribute('aria-disabled'));
  });

  it('cambia el rol de un miembro y no deja tocar el del dueño ni el propio', async () => {
    const user = userEvent.setup();
    renderScreen(<MembersScreen />, { scenario: 'rol-admin' });
    const owner = (await screen.findByText('Lucía Fernández')).closest('tr') as HTMLElement;
    expect(within(owner).getByText('Dueño')).toBeInTheDocument();
    expect(within(owner).queryByRole('button')).not.toBeInTheDocument();
    const self = screen.getByText('Martín Sosa').closest('tr') as HTMLElement;
    expect(within(self).getByText('Vos')).toBeInTheDocument();
    expect(within(self).queryByRole('button')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Rol de Carla Ruiz: Editor. Cambiar' }));
    await user.click(await screen.findByRole('menuitemradio', { name: 'Admin' }));
    expect(await screen.findByText('Carla Ruiz ahora es Admin')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Rol de Carla Ruiz: Admin. Cambiar' })).toBeInTheDocument();
  });

  it('quita a un miembro después de confirmar', async () => {
    const user = userEvent.setup();
    renderScreen(<MembersScreen />);
    await user.click(await screen.findByRole('button', { name: /Más acciones para Diego Paz/ }));
    await user.click(await screen.findByRole('menuitem', { name: 'Quitar del workspace' }));
    const confirm = await screen.findByRole('alertdialog', { name: '¿Quitar a Diego Paz de Estudio Norte?' });
    await user.click(within(confirm).getByRole('button', { name: 'Quitar del workspace' }));
    expect(await screen.findByText('3 de 5 miembros')).toBeInTheDocument();
    expect(screen.queryByText('diego@clientes.com')).not.toBeInTheDocument();
  });

  it('no se muestra a un Editor', async () => {
    renderScreen(<MembersScreen />, { scenario: 'rol-editor' });
    expect(await screen.findByRole('heading', { name: /No tenés acceso a esto/ })).toBeInTheDocument();
  });
});

describe('H3 · Redes sociales', () => {
  it('muestra el estado de cada red y desconecta después de confirmar', async () => {
    const user = userEvent.setup();
    renderScreen(<NetworksScreen />);
    expect(await screen.findByRole('button', { name: 'Desconectar TikTok' })).toBeInTheDocument();
    expect(screen.getByText('@estudionorte · publica en Reels')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Conectar YouTube' })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Desconectar TikTok' }));
    const confirm = await screen.findByRole('alertdialog', { name: '¿Desconectar TikTok?' });
    expect(within(confirm).getByText(/Las publicaciones programadas en TikTok no van a salir/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole('button', { name: 'Desconectar' }));
    expect(await screen.findByRole('button', { name: 'Conectar TikTok' })).toBeInTheDocument();
    expect(await screen.findByText('Desconectaste TikTok')).toBeInTheDocument();
  });

  it('una conexión vencida pide reconectar y abre el diálogo de la red (D6)', async () => {
    const user = userEvent.setup();
    renderScreen(<NetworksScreen />, { scenario: 'redes-vencidas' });
    expect(await screen.findByText('Vencida')).toBeInTheDocument();
    expect(screen.getByText('@estudionorte · no se puede publicar en TikTok hasta reconectar.')).toBeInTheDocument();
    expect(screen.getAllByText('Sin conectar')).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Reconectar TikTok' }));
    expect(await screen.findByRole('dialog', { name: 'Conectar TikTok' })).toBeInTheDocument();
  });

  it('no se muestra a un Lector', async () => {
    renderScreen(<NetworksScreen />, { scenario: 'rol-lector' });
    expect(await screen.findByRole('heading', { name: /No tenés acceso a esto/ })).toBeInTheDocument();
  });
});

describe('I1 · Perfil', () => {
  it('guarda el nombre', async () => {
    const user = userEvent.setup();
    renderScreen(<ProfileScreen />);
    const name = await screen.findByLabelText('Nombre');
    expect(name).toHaveValue('Lucía Fernández');
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled();
    await user.clear(name);
    await user.type(name, 'Lucía F.');
    await user.click(screen.getByRole('button', { name: 'Guardar' }));
    expect(await screen.findByText('Guardado')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled());
  });

  it('cambiar el email pide la contraseña y deja el nuevo pendiente de verificar', async () => {
    const user = userEvent.setup();
    renderScreen(<ProfileScreen />);
    await user.click(await screen.findByRole('button', { name: 'Cambiar email' }));
    await user.click(screen.getByRole('button', { name: 'Enviar link de verificación' }));
    expect(await screen.findByText('Escribí un email válido.')).toBeInTheDocument();
    expect(screen.getByText('Escribí tu contraseña para confirmar.')).toBeInTheDocument();
    await user.type(screen.getByLabelText('Email nuevo'), 'lucia@nuevo.com');
    await user.type(screen.getByLabelText('Tu contraseña'), 'incorrecta');
    await user.click(screen.getByRole('button', { name: 'Enviar link de verificación' }));
    expect(await screen.findByText('La contraseña no coincide.')).toBeInTheDocument();
    await user.clear(screen.getByLabelText('Tu contraseña'));
    await user.type(screen.getByLabelText('Tu contraseña'), 'secreta123');
    await user.click(screen.getByRole('button', { name: 'Enviar link de verificación' }));
    expect(await screen.findByText('Falta verificar lucia@nuevo.com')).toBeInTheDocument();
    expect(screen.queryByLabelText('Email nuevo')).not.toBeInTheDocument();
  });

  it('cambiar la contraseña valida el largo, la repetición y la actual', async () => {
    const user = userEvent.setup();
    renderScreen(<ProfileScreen />);
    await user.click(await screen.findByRole('button', { name: 'Cambiar contraseña' }));
    const submit = screen.getAllByRole('button', { name: 'Cambiar contraseña' }).find((button) => button.getAttribute('type') === 'submit') as HTMLElement;
    await user.type(screen.getByLabelText('Contraseña actual'), 'incorrecta');
    await user.type(screen.getByLabelText('Contraseña nueva'), 'corta');
    await user.type(screen.getByLabelText('Repetí la contraseña nueva'), 'otra');
    await user.click(submit);
    expect(await screen.findByText('Tiene que tener 8 caracteres como mínimo.', { selector: '[role="alert"]' })).toBeInTheDocument();
    expect(screen.getByText('Las contraseñas no coinciden.')).toBeInTheDocument();

    await user.clear(screen.getByLabelText('Contraseña nueva'));
    await user.type(screen.getByLabelText('Contraseña nueva'), 'unaclavelarga');
    await user.clear(screen.getByLabelText('Repetí la contraseña nueva'));
    await user.type(screen.getByLabelText('Repetí la contraseña nueva'), 'unaclavelarga');
    await user.click(submit);
    expect(await screen.findByText('La contraseña actual no coincide.')).toBeInTheDocument();

    await user.clear(screen.getByLabelText('Contraseña actual'));
    await user.type(screen.getByLabelText('Contraseña actual'), 'laDeSiempre');
    await user.click(submit);
    expect(await screen.findByText('Contraseña cambiada')).toBeInTheDocument();
    expect(screen.queryByLabelText('Contraseña actual')).not.toBeInTheDocument();
  });

  it('cierra la sesión y lleva a Iniciar sesión', async () => {
    const user = userEvent.setup();
    renderScreen(<ProfileScreen />);
    await user.click(await screen.findByRole('button', { name: 'Cerrar sesión' }));
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith('/login'));
  });
});

describe('I2 · Notificaciones', () => {
  it('cada interruptor se guarda solo y avisa qué cambió', async () => {
    const user = userEvent.setup();
    renderScreen(<NotificationsScreen />);
    const render = await screen.findByRole('switch', { name: 'Email: El render terminó' });
    expect(screen.getAllByRole('switch')).toHaveLength(5);
    expect(render).toHaveAttribute('aria-checked', 'true');
    await user.click(render);
    expect(await screen.findByText('Guardado. Ya no te avisamos por email cuando termina un render.')).toBeInTheDocument();
    expect(render).toHaveAttribute('aria-checked', 'false');
    await waitFor(() => expect(loadDb().users.find((item) => item.name === 'Lucía Fernández')?.notification_preferences.render_finished).toBe(false));
    await user.click(render);
    expect(await screen.findByText('Guardado. Te avisamos por email cuando termina un render.')).toBeInTheDocument();
    expect(render).toHaveAttribute('aria-checked', 'true');
  });
});

describe('errores de carga: banner con Reintentar', () => {
  it('Miembros', async () => {
    const user = userEvent.setup();
    const restore = failRead(/\/api\/v1\/workspaces\/[^/]+\/members\/$/);
    renderScreen(<MembersScreen />);
    const alert = await screen.findByRole('alert');
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Invitar' })).toBeDisabled();
    restore();
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByText('Carla Ruiz')).toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('Redes sociales', async () => {
    const user = userEvent.setup();
    const restore = failRead('/api/v1/social-accounts/');
    renderScreen(<NetworksScreen />);
    const alert = await screen.findByRole('alert');
    restore();
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findByRole('button', { name: 'Desconectar TikTok' })).toBeInTheDocument();
  });

  it('Notificaciones', async () => {
    const user = userEvent.setup();
    const restore = failRead('/api/v1/me/notification-preferences/');
    renderScreen(<NotificationsScreen />);
    const alert = await screen.findByRole('alert');
    expect(screen.queryByRole('switch')).not.toBeInTheDocument();
    restore();
    await user.click(within(alert).getByRole('button', { name: 'Reintentar' }));
    expect(await screen.findAllByRole('switch')).toHaveLength(5);
  });
});

describe('I2 · respuestas fuera de orden', () => {
  it('el interruptor queda como el último clic aunque la primera respuesta llegue después', async () => {
    const user = userEvent.setup();
    const all = { project_ready: true, render_finished: true, project_failed: true, post_failed: true, low_balance: true };
    let stored = { ...all };
    let calls = 0;
    server.use(
      http.get(`${API_URL}/api/v1/me/notification-preferences/`, () => HttpResponse.json(stored)),
      http.patch(`${API_URL}/api/v1/me/notification-preferences/`, async ({ request }) => {
        calls += 1;
        const turn = calls;
        const body = (await request.json()) as Partial<typeof all>;
        // The server applies them in order; only the first answer travels slowly.
        stored = { ...stored, ...body };
        const snapshot = { ...stored };
        if (turn === 1) await delay(250);
        return HttpResponse.json(snapshot);
      }),
    );
    renderScreen(<NotificationsScreen />);
    const toggle = await screen.findByRole('switch', { name: 'Email: El render terminó' });
    await user.click(toggle); // off (slow answer)
    await user.click(toggle); // on (fast answer)
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    await waitFor(() => expect(calls).toBe(2));
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(screen.queryByText(/Ya no te avisamos por email cuando termina un render/)).not.toBeInTheDocument();
    expect(await screen.findByText('Guardado. Te avisamos por email cuando termina un render.')).toBeInTheDocument();
  });
});

describe('M10 · orden en el teléfono', () => {
  it('el contenido de la sección abierta va en el documento justo después de su fila', async () => {
    const original = window.matchMedia;
    window.matchMedia = ((query: string) => ({ matches: query.includes('max-width: 639px'), media: query, onchange: null, addEventListener: () => undefined, removeEventListener: () => undefined, addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false })) as typeof window.matchMedia;
    try {
      navigation.pathname = '/ajustes/miembros';
      const view = renderScreen(
        <SettingsShell>
          <button type="button">contenido</button>
        </SettingsShell>,
      );
      await screen.findByRole('link', { name: /Redes sociales/ });
      const order = [...view.container.querySelectorAll('a, button')].map((node) => node.textContent ?? '');
      const at = (text: string) => order.findIndex((item) => item.includes(text));
      expect(at('General')).toBeLessThan(at('Miembros'));
      expect(at('Miembros')).toBeLessThan(at('contenido'));
      expect(at('contenido')).toBeLessThan(at('Redes sociales'));
      expect(screen.getByRole('navigation', { name: 'Secciones de ajustes, continuación' })).toBeInTheDocument();
    } finally {
      window.matchMedia = original;
    }
  });
});
