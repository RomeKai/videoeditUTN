import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { http, HttpResponse, delay } from 'msw';
import { API_URL } from '@/api/config';
import real from '@/api/contracts/drf/responses.json';
import { getTokens } from '@/api/session';
import { server } from '@/api/mocks/server';
import { loadDb, saveDb } from '@/api/mocks/state';
import { failRead, renderScreen, startMockApi } from '../uso/test-utils';
import { GeneralSettings } from './GeneralSettings';
import { parseEmails } from './InviteMemberDialog';
import { MembersScreen } from './MembersScreen';
import { NetworksScreen } from './NetworksScreen';
import { NotificationsScreen } from './NotificationsScreen';
import { ProfileScreen } from './ProfileScreen';
import { SettingsShell } from './SectionShell';
import { membersSummary, networksSummary, seatLimitReason } from './summaries';

const navigation = vi.hoisted(() => ({ pathname: '/ajustes', replace: vi.fn() }));
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn(), replace: navigation.replace, back: vi.fn(), refresh: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(),
  useParams: () => ({}),
}));

startMockApi();

const newMemberId = '7c1d2e3f-4a5b-4c6d-8e7f-9a0b1c2d3e4f';

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
    expect(membersSummary({ members: [member('active'), member('active'), member('pending')], max_members: 5, seats_used: 3, limited_by: null })).toBe('2 de 5 miembros · 1 invitación pendiente');
    expect(membersSummary({ members: [member('active')], max_members: 1, seats_used: 1, limited_by: null })).toBe('1 de 1 miembro');
    expect(networksSummary([{ status: 'connected' }, { status: 'connected' }, { status: 'expired' }])).toBe('2 conectadas · 1 vencida');
    expect(networksSummary([{ status: 'disconnected' }])).toBe('Ninguna conectada');
  });

  it('dice de qué plan son los lugares cuando un cambio de plan los achica', () => {
    expect(seatLimitReason({ reason: 'pending_payment', plan_name: 'Creador', effective_at: null })).toBe(
      'Cuentan los lugares del plan Creador: estás pasando a ese plan y el pago está en curso.',
    );
    expect(seatLimitReason({ reason: 'pending_plan', plan_name: 'Gratis', effective_at: '2026-12-12T15:00:00Z' })).toBe(
      'Cuentan los lugares del plan Gratis: tu plan pasa a ese el 12 dic 2026.',
    );
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

  it('borrar con proyectos en proceso: el servidor lo rechaza y el diálogo dice por qué', async () => {
    const user = userEvent.setup();
    renderScreen(<GeneralSettings />);
    await user.click(await screen.findByRole('button', { name: 'Borrar el workspace…' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Borrar Estudio Norte?' });
    await user.type(within(dialog).getByRole('textbox'), 'Estudio Norte');
    await user.click(within(dialog).getByRole('button', { name: 'Borrar el workspace' }));
    expect(await within(dialog).findByText(/Hay proyectos procesándose o renderizándose/)).toBeInTheDocument();
    expect(loadDb().workspaces.some((workspace) => workspace.name === 'Estudio Norte')).toBe(true);
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it('borrar con un pago en curso: el servidor lo rechaza y el diálogo dice qué hacer', async () => {
    const user = userEvent.setup();
    const prepare = () => {
      const db = loadDb();
      const estudio = db.workspaces.find((workspace) => workspace.name === 'Estudio Norte')?.id ?? '';
      db.projects = db.projects.filter((project) => project.workspace !== estudio || !['uploaded', 'ingesting', 'rendering'].includes(project.status));
      for (const post of db.posts) if (post.workspace === estudio && ['SCHEDULED', 'QUEUED', 'PROCESSING'].includes(post.status)) post.status = 'DRAFT';
      for (const wallet of db.wallets) if (wallet.workspace === estudio) Object.assign(wallet, { available_balance: 0, reserved_balance: 0 });
      const id = '5b2e8c1d-3f4a-4e6b-8c9d-0a1b2c3d4e5f';
      db.purchases.push({
        id,
        workspace: estudio,
        status: 'pending',
        tokens: 0,
        plan_id: db.plans.find((plan) => plan.price_usd_monthly > 0)?.id ?? null,
        amount_usd: 49,
        checkout_url: `http://localhost:3000/dev/checkout?compra=${id}`,
        failure_reason: null,
        created_at: new Date().toISOString(),
        balance_after: null,
        _settleAt: null,
        _reject: false,
      });
      saveDb(db);
    };
    renderScreen(<GeneralSettings />, { prepare });
    await user.click(await screen.findByRole('button', { name: 'Borrar el workspace…' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Borrar Estudio Norte?' });
    await user.type(within(dialog).getByRole('textbox'), 'Estudio Norte');
    await user.click(within(dialog).getByRole('button', { name: 'Borrar el workspace' }));
    expect(await within(dialog).findByText('Hay un pago en curso: terminalo, o esperá a que venza (24 h después de abrirlo).')).toBeInTheDocument();
    expect(loadDb().workspaces.some((workspace) => workspace.name === 'Estudio Norte')).toBe(true);
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it('borrar pide escribir el nombre del workspace', async () => {
    const user = userEvent.setup();
    // Nothing in progress: a workspace with projects processing or rendering is not deleted (409).
    const prepare = () => {
      const db = loadDb();
      const estudio = db.workspaces.find((workspace) => workspace.name === 'Estudio Norte')?.id;
      db.projects = db.projects.filter((project) => project.workspace !== estudio || !['uploaded', 'ingesting', 'rendering'].includes(project.status));
      // Drafts: the simulation would send a SCHEDULED post that is due, and one going out stops the delete.
      for (const post of db.posts) if (post.workspace === estudio && ['SCHEDULED', 'QUEUED', 'PROCESSING'].includes(post.status)) post.status = 'DRAFT';
      for (const wallet of db.wallets) if (wallet.workspace === estudio) Object.assign(wallet, { available_balance: 0, reserved_balance: 0 });
      saveDb(db);
    };
    renderScreen(<GeneralSettings />, { prepare });
    await user.click(await screen.findByRole('button', { name: 'Borrar el workspace…' }));
    const dialog = await screen.findByRole('alertdialog', { name: '¿Borrar Estudio Norte?' });
    expect(within(dialog).getByText(/Las publicaciones programadas que todavía no salieron se borran y no se van a publicar\./)).toBeInTheDocument();
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
    expect(within(dialog).getByText(/No se puede borrar el único workspace que tenés/)).toBeInTheDocument();
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
    renderScreen(<MembersScreen />, { scenario: 'miembros-queda-un-lugar' });
    expect(await screen.findByText('9 de 10 miembros')).toBeInTheDocument();
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
    expect(await screen.findByText('9 de 10 miembros · 1 invitación pendiente')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Invitar' })).toHaveAttribute('aria-disabled', 'true');
    // A long flow (dialog, four validations, the invitation): on a busy machine it needs more than the default 5 s.
  }, 30_000);

  it('con las respuestas reales del backend: la fila pendiente sin cuenta, los errores de invitar y reenviar', async () => {
    const user = userEvent.setup();
    const pending = real.invitation_invited.invited[0];
    if (!pending) throw new Error('falta la fila invitada');
    const members = { members: [...real.members.members.slice(0, 2), pending], max_members: 10, seats_used: 3, limited_by: null };
    let answer: { status: number; body: unknown } = real.errors.invite_403_seats;
    const sent: unknown[] = [];
    server.use(
      http.get(`${API_URL}/api/v1/workspaces/:id/members/`, () => HttpResponse.json(members)),
      http.post(`${API_URL}/api/v1/workspaces/:id/invitations/`, async ({ request }) => {
        sent.push(await request.json());
        return HttpResponse.json(answer.body as Record<string, unknown>, { status: answer.status });
      }),
      http.post(`${API_URL}/api/v1/workspaces/:id/invitations/:memberId/resend/`, ({ params }) =>
        params.memberId === pending.id ? new HttpResponse(null, { status: 204 }) : HttpResponse.json(real.errors.invitation_resend_404.body, { status: 404 }),
      ),
    );
    renderScreen(<MembersScreen />, { scenario: 'estudio-norte' });
    // A pending row of the backend: no `user`, the invited email, and who invited without their email.
    const row = (await screen.findByText('juana@example.com')).closest('tr') as HTMLElement;
    expect(within(row).getByText('Invitación pendiente')).toBeInTheDocument();
    expect(within(row).getByText(/Invitado por Dana/)).toBeInTheDocument();
    await user.click(within(row).getByRole('button', { name: 'Reenviar la invitación a juana@example.com' }));
    expect(await screen.findByText('Reenviamos la invitación a juana@example.com')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Invitar' }));
    const dialog = await screen.findByRole('dialog', { name: 'Invitar a Estudio Norte' });
    await user.type(within(dialog).getByLabelText('Emails'), 'nueva@example.com');
    await user.click(within(dialog).getByRole('button', { name: 'Enviar invitación' }));
    expect(await within(dialog).findByText(real.errors.invite_403_seats.body.error)).toBeInTheDocument();
    expect(sent[0]).toEqual({ emails: ['nueva@example.com'], role: 'editor' });

    answer = real.errors.invite_400_already_member;
    await user.type(within(dialog).getByLabelText('Emails'), ' ');
    await user.click(within(dialog).getByRole('button', { name: 'Enviar invitación' }));
    expect(await within(dialog).findByText(real.errors.invite_400_already_member.body.emails[0] as string)).toBeInTheDocument();

    answer = { status: 201, body: real.invitation_invited };
    await user.type(within(dialog).getByLabelText('Emails'), ' ');
    await user.click(within(dialog).getByRole('button', { name: 'Enviar invitación' }));
    expect(await screen.findByText('Invitación enviada a juana@example.com')).toBeInTheDocument();
  }, 30_000);

  it('con el plan lleno «Invitar» queda deshabilitado y explica por qué', async () => {
    const user = userEvent.setup();
    renderScreen(<MembersScreen />, { scenario: 'miembros-al-limite' });
    expect(await screen.findByText('9 de 10 miembros · 1 invitación pendiente')).toBeInTheDocument();
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
    expect(await screen.findByText('9 de 10 miembros')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Invitar' })).not.toHaveAttribute('aria-disabled'));
  });

  it('con un pago de plan en curso cuentan los lugares de ese plan, los mismos que controla invitar', async () => {
    const user = userEvent.setup();
    // The rehearsal: Pro (10 seats) with 4 taken while a payment to Creador (5 seats) waits for its checkout.
    const prepare = () => {
      const db = loadDb();
      const estudio = db.workspaces.find((workspace) => workspace.name === 'Estudio Norte')?.id ?? '';
      const id = '5b2e8c1d-3f4a-4e6b-8c9d-0a1b2c3d4e5f';
      db.purchases.push({
        id,
        workspace: estudio,
        status: 'pending',
        tokens: 0,
        plan_id: db.plans.find((plan) => plan.name === 'Creador')?.id ?? null,
        amount_usd: 19,
        checkout_url: `http://localhost:3000/dev/checkout?compra=${id}`,
        failure_reason: null,
        created_at: new Date().toISOString(),
        balance_after: null,
        _settleAt: null,
        _reject: false,
      });
      saveDb(db);
    };
    renderScreen(<MembersScreen />, { prepare });
    expect(await screen.findByText('4 de 5 miembros')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Invitar' }));
    const dialog = await screen.findByRole('dialog', { name: 'Invitar a Estudio Norte' });
    expect(within(dialog).getByText(/en el plan Creador$/)).toHaveTextContent('Queda 1 lugar en el plan Creador');
    expect(within(dialog).getByText(/Cuentan los lugares del plan Creador: estás pasando a ese plan/)).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText('Emails'), 'juan@ejemplo.com, ana@ejemplo.com');
    await user.click(within(dialog).getByRole('button', { name: 'Enviar invitaciones' }));
    expect(await within(dialog).findByText('Escribiste 2 emails y queda 1 lugar en el plan Creador.')).toBeInTheDocument();
    await user.clear(within(dialog).getByLabelText('Emails'));
    await user.type(within(dialog).getByLabelText('Emails'), 'juan@ejemplo.com');
    await user.click(within(dialog).getByRole('button', { name: 'Enviar invitación' }));
    expect(await screen.findByText('Invitación enviada a juan@ejemplo.com')).toBeInTheDocument();

    // Full: «Invitar» is off and the note says why, with what to do in Planes.
    expect(await screen.findByText('4 de 5 miembros · 1 invitación pendiente')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Invitar' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText(/^Hay un pago en curso para pasar al plan Creador, que tiene 5 lugares, y ya están ocupados\. Para invitar a alguien más,/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'elegí «Seguir con Pro» en Planes' })).toHaveAttribute('href', '/uso/planes');
  }, 30_000);

  it('con una bajada programada el plan lleno dice cuándo cambia y cómo cancelarlo', async () => {
    const prepare = () => {
      const db = loadDb();
      const workspace = db.workspaces.find((item) => item.name === 'Estudio Norte');
      const creator = db.plans.find((plan) => plan.name === 'Creador');
      if (!workspace || !creator) throw new Error('faltan datos');
      // Creador has 5 seats; Estudio Norte has 4 members and one invitation.
      workspace.pending_plan = { id: creator.id, effective_at: '2026-12-12T15:00:00Z' };
      db.members.push({ id: newMemberId, workspace: workspace.id, user: null, email: 'juan@ejemplo.com', role: 'editor', status: 'pending', joined_at: new Date().toISOString(), invited_by: null, token: null });
      saveDb(db);
    };
    renderScreen(<MembersScreen />, { prepare });
    expect(await screen.findByText('4 de 5 miembros · 1 invitación pendiente')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Invitar' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByText(/^Tu plan pasa a Creador el 12 dic 2026 y ese plan tiene 5 lugares, y ya están ocupados\./)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'cancelá el cambio de plan' })).toHaveAttribute('href', '/uso/planes');
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
    expect(await screen.findByText('3 de 10 miembros')).toBeInTheDocument();
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

  it('en el plan Gratis no conecta: candado con el plan que lo incluye, y lo conectado se puede desconectar', async () => {
    renderScreen(<NetworksScreen />, { scenario: 'plan-gratis' });
    expect(await screen.findByRole('button', { name: 'Desconectar TikTok' })).toBeInTheDocument();
    expect(await screen.findAllByText(/Disponible en el plan Creador/)).not.toHaveLength(0);
    expect(screen.queryByRole('button', { name: 'Conectar YouTube' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Reconectar/ })).not.toBeInTheDocument();
  });

  it('un backend que todavía no manda `allow_social_accounts` no muestra el candado: decide el backend', async () => {
    renderScreen(<NetworksScreen />, {
      scenario: 'plan-gratis',
      prepare: () => {
        const db = loadDb();
        for (const plan of db.plans) delete plan.allow_social_accounts;
        saveDb(db);
      },
    });
    expect(await screen.findByRole('button', { name: 'Conectar YouTube' })).toBeInTheDocument();
    expect(screen.queryByText(/Disponible en el plan/)).not.toBeInTheDocument();
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
    const before = getTokens();
    await user.click(submit);
    expect(await screen.findByText('Contraseña cambiada')).toBeInTheDocument();
    expect(screen.queryByLabelText('Contraseña actual')).not.toBeInTheDocument();
    // The change ends the other sessions: this browser keeps going with the pair of the answer.
    expect(getTokens()).not.toEqual(before);
    expect(getTokens()?.access).toMatch(/^mock-access\./);
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

  it('«Una publicación falló» se prende y se apaga como los demás, y la campana no depende del email', async () => {
    const user = userEvent.setup();
    renderScreen(<NotificationsScreen />);
    const post = await screen.findByRole('switch', { name: 'Email: Una publicación falló' });
    expect(post).toBeEnabled();
    expect(post).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('Un clip no salió en una de tus redes, o no pudimos confirmar si salió. Te contamos qué pasó para que la reintentes.')).toBeInTheDocument();
    expect(screen.queryByText('Todavía no avisamos de las publicaciones fallidas.')).not.toBeInTheDocument();

    await user.click(post);
    expect(await screen.findByText('Guardado. Ya no te avisamos por email cuando una publicación falla.')).toBeInTheDocument();
    expect(post).toHaveAttribute('aria-checked', 'false');
    await waitFor(() => expect(loadDb().users.find((item) => item.name === 'Lucía Fernández')?.notification_preferences.post_failed).toBe(false));
    await user.click(post);
    expect(await screen.findByText('Guardado. Te avisamos por email cuando una publicación falla.')).toBeInTheDocument();
    await waitFor(() => expect(loadDb().users.find((item) => item.name === 'Lucía Fernández')?.notification_preferences.post_failed).toBe(true));

    // Every switch works, and the note about the bell speaks of all of them.
    expect(screen.getAllByRole('switch').filter((item) => !(item as HTMLButtonElement).disabled)).toHaveLength(5);
    expect(screen.getByText('En la app, estos avisos aparecen siempre en la campana, aunque apagues el email.')).toBeInTheDocument();
  });

  it('dice a qué email llegan, o que no llegan hasta verificarlo', async () => {
    const first = renderScreen(<NotificationsScreen />);
    expect(await screen.findByText(/^Llegan a lucia@/)).toBeInTheDocument();
    expect(screen.queryByText(/Todavía no verificaste/)).not.toBeInTheDocument();
    first.unmount();

    const unverified = () => {
      const data = loadDb();
      const lucia = data.users.find((item) => item.name === 'Lucía Fernández');
      if (lucia) lucia.is_email_verified = false;
      saveDb(data);
    };
    renderScreen(<NotificationsScreen />, { prepare: unverified });
    expect(await screen.findByText(/^Todavía no verificaste lucia@.*: hasta que lo hagas no te mandamos estos emails\./)).toBeInTheDocument();
    expect(screen.queryByText(/^Llegan a/)).not.toBeInTheDocument();
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
