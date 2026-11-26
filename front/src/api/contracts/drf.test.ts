import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { approveErrorKind, errorMessage, fieldError, pipelineErrorMessage, planLimitKind, uploadErrorCode } from '@/lib/error-messages';
import { describeProjectStatus } from '@/lib/project-status';
import { ApiError } from '../errors';
import { TokenPair, refreshToken } from './auth';
import { returnedPart } from '@/features/uso/movements';
import {
  Payment,
  Subscription,
  TokenPackages,
  changePlan,
  createTokenPurchase,
  estimateCost,
  exportTransactions,
  getPricing,
  getTokenPurchase,
  getUsage,
  getWallet,
  listTransactions,
  simulatePurchase,
} from './billing';
import { BrandKit, listBrandKits } from './brandkits';
import { ClipPreview } from './clips';
import real from './drf/responses.json';
import { ApprovePaperEditResponse, CreateFromPromptResponse, CreateProjectResponse, PaperEditData, Project, ProjectsResponse } from './projects';
import { type MembersResponse, type Workspace, createWorkspace, getWorkspace, listMembers, listWorkspaces, transferWorkspace, updateMember, updateWorkspace } from './workspaces';

/**
 * Bodies as the backend of `dev` builds them (see drf/responses.json), parsed
 * with the contract. When a serializer changes, update the JSON: the test says
 * which schema stopped accepting it.
 */
function parse(schema: ZodTypeAny, value: unknown) {
  const result = schema.safeParse(value);
  if (!result.success) throw new Error(result.error.issues.map((issue) => `${issue.path.join('.')}: ${issue.message}`).join('; '));
  return result.data;
}

const error = (key: keyof typeof real.errors, endpoint = 'x') => new ApiError(real.errors[key].status, endpoint, real.errors[key].body);

describe('respuestas reales del backend', () => {
  it('sesión', () => {
    parse(TokenPair, real.token_pair);
    parse(refreshToken.response, real.token_refresh);
  });

  it.each([
    'project_uploaded',
    'project_in_review_auto_render_skipped',
    'project_completed_auto_render',
    'project_failed_auto_render_not_paid',
    'project_from_link',
  ] as const)('proyecto: %s', (key) => {
    parse(Project, real[key]);
  });

  it('lista sin paginar (hoy) y la misma lista paginada (propuesto)', () => {
    const today = parse(ProjectsResponse, real.project_list) as ProjectsResponse;
    expect(today.count).toBe(5);
    const paged = parse(ProjectsResponse, { count: 5, next: null, previous: null, results: real.project_list }) as ProjectsResponse;
    expect(paged.results).toHaveLength(5);
  });

  it('crear, crear con prompt, revisión, aprobar, vista previa y brand kits', () => {
    const created = parse(CreateProjectResponse, real.project_create_response) as CreateProjectResponse;
    // A Decimal the view puts in the Response arrives as a JSON number.
    expect(created.cost_details.tokens_reserved).toBe(555.3);
    parse(CreateFromPromptResponse, real.project_from_prompt_response);
    parse(PaperEditData, real.paper_edit_data);
    parse(ApprovePaperEditResponse, real.approve_response);
    parse(ClipPreview, real.clip_preview_pending);
    parse(ClipPreview, real.clip_preview_ready);
    parse(ClipPreview, real.clip_preview_failed);
    // Colors are free text in the backend: "#000" and lowercase are valid answers.
    parse(BrandKit, real.brand_kit);
    parse(listBrandKits.response, real.brand_kit_list);
  });

  it('el aviso de «renderizar sin revisar» y el error de auto-render se pueden leer', () => {
    const review = Project.parse(real.project_in_review_auto_render_skipped);
    expect(review.metadata.auto_render_skipped).toBe('insufficient_funds');
    expect(describeProjectStatus(review).label).toBe('Listo para revisar');
    const done = Project.parse(real.project_completed_auto_render);
    expect(done.metadata.auto_render_clip_ids).toHaveLength(2);
    const failed = Project.parse(real.project_failed_auto_render_not_paid);
    expect(failed.metadata.no_speech).toBe(true);
    expect(pipelineErrorMessage(failed.pipeline_error_code)).toMatch(/No alcanzó el saldo/);
  });
});

describe('workspaces y miembros: respuestas reales del backend', () => {
  it('lista, crear, detalle, renombrar y transferir', () => {
    parse(listWorkspaces.response, real.workspace_list);
    const created = parse(createWorkspace.response, real.workspace_create) as Workspace;
    expect(created.role).toBe('owner');
    const detail = parse(getWorkspace.response, real.workspace_detail) as Workspace;
    // The owner first; an account without a name shows its email.
    expect(detail.admins?.map((admin) => admin.role)).toEqual(['owner', 'admin']);
    parse(updateWorkspace.response, real.workspace_update);
    // Who transferred stays as Admin.
    expect((parse(transferWorkspace.response, real.workspace_transfer) as Workspace).role).toBe('admin');
  });

  it('miembros: la fila del dueño sale como owner y las pendientes ocupan lugar', () => {
    const members = parse(listMembers.response, real.members) as MembersResponse;
    expect(members.members.map((member) => member.role)).toEqual(['owner', 'admin', 'editor', 'viewer', 'editor']);
    expect(members.members.at(-1)?.status).toBe('pending');
    expect(members.seats_used).toBe(members.members.length);
    // A user without a name arrives as '' and nobody is recorded as the inviter.
    expect(members.members[0]?.user?.name).toBe('');
    expect(members.members[0]?.invited_by).toBeNull();
    parse(updateMember.response, real.member_update);
  });

  it('errores: el 403 de rol dice quién puede, sin `code`; los 400 van por campo; el 409 trae `code`', () => {
    for (const key of ['workspace_403_admins', 'workspace_403_owner'] as const) {
      expect(error(key).code).toBeUndefined();
      expect(error(key).isPlanLimit).toBe(false);
      expect(errorMessage(error(key))).toBe(real.errors[key].body.detail);
    }
    expect(error('workspace_404').isNotFound).toBe(true);
    expect(fieldError(error('workspace_400_name'), 'name')).toBe('Escribí un nombre para el espacio.');
    expect(fieldError(error('workspace_delete_400_confirm'), 'confirm_name')).toBe('El nombre no coincide.');
    expect(fieldError(error('member_400_owner_role'), 'role')).toBe('No se puede cambiar el rol del dueño.');
    expect(fieldError(error('transfer_400'), 'new_owner_id')).toBe('Tiene que ser un miembro activo del workspace.');
    expect(errorMessage(error('member_400_owner_remove'))).toBe('No se puede quitar al dueño del workspace.');
    expect(error('workspace_delete_409_processing').code).toBe('project_processing');
    expect(errorMessage(error('workspace_delete_409_processing'))).toMatch(/esperá a que terminen/);
    expect(error('workspace_delete_409_reserved').code).toBe('reservation_open');
    // Every refusal of the delete says why in Spanish, and that sentence is what the dialog shows.
    for (const [key, code] of [['workspace_delete_409_posts', 'posts_in_flight'], ['workspace_delete_409_balance', 'balance_left'], ['workspace_delete_409_last', 'last_workspace']] as const) {
      expect(error(key).isConflict).toBe(true);
      expect(error(key).code).toBe(code);
      expect(errorMessage(error(key))).toBe(real.errors[key].body.error);
    }
    expect(errorMessage(error('workspace_delete_409_balance'))).toMatch(/^Quedan 120\.5 tokens en este espacio y no se pueden recuperar/);
    expect(errorMessage(error('workspace_delete_409_processing'))).toMatch(/Si alguno quedó trabado, borrá ese proyecto primero/);
    expect(error('workspace_create_409_limit').code).toBe('workspace_limit');
    expect(errorMessage(error('workspace_create_409_limit'))).toMatch(/máximo de 10 espacios/);
    expect(errorMessage(error('transfer_400_seats'))).toMatch(/^No quedan lugares en el plan/);
  });
});

describe('billetera, movimientos y precios: respuestas reales del backend', () => {
  it('precios y estimado', () => {
    const pricing = parse(getPricing.response, real.pricing) as typeof getPricing.response._output;
    // Every Decimal arrives as text and is read as a number.
    expect(pricing.base_cost_per_second_720p).toBe(0.5);
    expect(pricing.resolution_multipliers['1080p']).toBe(1.5);
    expect(pricing.upload_extensions).toEqual(['.mp4', '.mov', '.avi']);
    const file = parse(estimateCost.response, real.estimate_file) as typeof estimateCost.response._output;
    expect(file.total).toBe(133.71);
    // Subtracted by the backend in Decimal: in floats 496.5 − 133.71 is 362.78999999999996.
    expect(file.balance_after).toBe(362.79);
    const link = parse(estimateCost.response, real.estimate_link) as typeof estimateCost.response._output;
    expect(link.eta_seconds).toBeNull();
  });

  it('saldo, consumo y movimientos', () => {
    const wallet = parse(getWallet.response, real.wallet) as typeof getWallet.response._output;
    expect(wallet.reservations[0]).toMatchObject({ project_title: 'Entrevista con Ana', amount: 3.5 });
    const usage = parse(getUsage.response, real.wallet_usage) as typeof getUsage.response._output;
    expect(usage).toHaveLength(31);
    expect(usage.find((day) => day.date === '2026-10-11')?.projects).toBe(12.5);
    const page = parse(listTransactions.response, real.transactions_page) as typeof listTransactions.response._output;
    const released = page.results.find((item) => item.kind === 'released');
    // "Devuelto" keeps the link to its project.
    expect(released?.project?.title).toBe('Podcast 12');
    // A partial charge: 5 charged of 20 reserved, settled after it was made.
    const partial = page.results.find((item) => item.kind === 'charged' && item.reserved_amount !== null && item.reserved_amount > item.amount);
    expect(partial).toMatchObject({ amount: 5, reserved_amount: 20, settled_at: '2026-10-11T16:30:00Z' });
    expect(returnedPart(partial!)).toBe(15);
    expect(page.totals.charged).toEqual({ count: 2, amount: 17.5 });
    // What came back: the released reservation (7.25) and the part the partial charge did not use (15).
    expect(page.totals.released).toEqual({ count: 1, amount: 22.25, partial_count: 1 });
    const csv = parse(exportTransactions.response, real.transactions_export) as string;
    expect(csv.split('\n')[0]).toBe('fecha,concepto,proyecto,tipo,monto,estado,transaccion');
    // The ledger of October adds up to what the balance did: −20 +15, −12.50, −7.25 +7.25 and the purchase.
    const sum = csv.split('\n').slice(1).filter(Boolean).reduce((total, line) => total + Number(line.split(',')[4]) * 100, 0) / 100;
    expect(sum).toBe(4982.5);
    expect(csv).toContain(',Podcast 12,Devuelto,7.25,Devuelto,');
  });

  it('sus errores: el límite del plan abre D3, el rol no, y los 400 van por campo', () => {
    const plan = error('estimate_403_plan', 'billing.estimate');
    expect(plan.isPlanLimit).toBe(true);
    expect(planLimitKind(plan)).toBe('duration');
    const role = error('transactions_403_role', 'billing.transactions');
    expect(role.isPlanLimit).toBe(false);
    expect(errorMessage(role)).toMatch(/dueño o un admin/);
    // The creation's own 400 for a link in another resolution.
    expect(errorMessage(error('estimate_400_link'))).toMatch(/720p/);
    expect(fieldError(error('usage_400_month'), 'month')).toMatch(/AAAA-MM/);
  });
});

describe('errores reales del backend', () => {
  it('subida: se reconoce el código por el texto', () => {
    expect(uploadErrorCode(error('create_400_upload'))).toBe('too_long');
    expect(error('create_400_required').message).toBe("Debes proporcionar un 'source_file' o una 'video_url'.");
    expect(fieldError(error('create_400_single_edit'), 'project_type')).toMatch(/subí el archivo/);
  });

  it('los mensajes en inglés de DRF no llegan a la pantalla', () => {
    const english = error('create_400_drf_english');
    expect(fieldError(english, 'title')).toBe('Máximo 255 caracteres.');
    expect(fieldError(english, 'aspect_ratio')).toBe('Elegí una de las opciones.');
    expect(errorMessage(error('permission_403'))).toBe('No tenés acceso a esto. Pedile a un admin del workspace.');
    expect(errorMessage(error('not_found_404'))).toBe('No encontramos lo que buscabas.');
    expect(errorMessage(error('login_401'))).not.toMatch(/account/);
  });

  it('402 y 403: saldo, plan y permisos no se confunden', () => {
    expect(error('create_402').isInsufficientFunds).toBe(true);
    expect(error('create_402').requiredTokens).toBe(555.3);
    expect(error('create_403_plan').isPlanLimit).toBe(true);
    expect(planLimitKind(error('create_403_plan'))).toBe('duration');
    // DRF's PermissionDenied has no code: it is a role 403, never a plan limit.
    expect(error('permission_403').isPlanLimit).toBe(false);
  });

  it('aprobar: cada 400 lleva a su lugar', () => {
    expect(approveErrorKind(error('approve_400_not_awaiting'))).toMatchObject({ kind: 'not_awaiting_approval' });
    // The backend's source has that sentence with a broken encoding today; it is recognised broken and fixed.
    expect(real.errors.approve_400_not_awaiting.body.error).toContain('no estÃ¡ en espera de aprobaciÃ³n');
    const fixed = new ApiError(400, 'x', { error: 'El proyecto no está en espera de aprobación. Estado actual: rendering' });
    expect(approveErrorKind(fixed)).toMatchObject({ kind: 'not_awaiting_approval' });
    expect(errorMessage(error('approve_400_not_awaiting'))).toBe('El proyecto no está en espera de aprobación. Estado actual: rendering');
    expect(approveErrorKind(error('approve_409_race'))).toMatchObject({ kind: 'not_awaiting_approval' });
    expect(approveErrorKind(error('approve_400_pricing'))).toMatchObject({ kind: 'plan_limit', limit: 'resolution' });
    expect(approveErrorKind(error('approve_402'))).toMatchObject({ kind: 'insufficient_funds' });
    expect(approveErrorKind(error('approve_400_segments'))).toMatchObject({ kind: 'invalid_segments' });
  });

  it('409 al cambiar un proyecto que se renderiza: llega solo `detail`', () => {
    const rendering = error('patch_409_rendering', 'projects.update');
    expect(rendering.isConflict).toBe(true);
    expect(rendering.code).toBeUndefined();
    expect(errorMessage(rendering)).toBe('El proyecto se está renderizando: esperá a que termine para cambiarlo.');
  });

  it('vista previa', () => {
    expect(error('preview_404').isNotFound).toBe(true);
    expect(errorMessage(error('preview_429'))).toBe('Ya hay vistas previas en proceso en este espacio. Probá en unos segundos.');
    expect(errorMessage(error('preview_409'))).toMatch(/no tiene vista previa/);
  });
});

describe('suscripción y compras: respuestas reales del backend (billing_*)', () => {
  it('suscripción y cambio de plan', () => {
    const free = parse(Subscription, real.billing_subscription_free) as Subscription;
    expect(free.plan.name).toBe('Gratis');
    expect(free.renews_at).toBeNull();
    const checkout = parse(changePlan.response, real.billing_change_plan_checkout) as ReturnType<typeof changePlan.response.parse>;
    expect(checkout.checkout_url).toMatch(/\/dev\/checkout\?compra=.+&volver=/);
    const scheduled = parse(changePlan.response, real.billing_change_plan_period_end) as ReturnType<typeof changePlan.response.parse>;
    expect(scheduled.applied).toBe('period_end');
    expect(scheduled.subscription.pending_plan?.name).toBe('Gratis');
    expect(scheduled.subscription.pending_plan?.effective_at).toBe(scheduled.subscription.renews_at);
    const canceled = parse(changePlan.response, real.billing_change_plan_cancel) as ReturnType<typeof changePlan.response.parse>;
    expect(canceled.subscription.pending_plan).toBeNull();
  });

  it('paquetes, compras y pagos de plan', () => {
    const packages = parse(TokenPackages, real.billing_token_packages) as TokenPackages;
    expect(packages.packages.map((item) => item.price_usd)).toEqual([10, 45, 120]);
    expect(packages.usd_to_local_rate).toBe(1450);
    const pending = parse(createTokenPurchase.response, real.billing_token_purchase_pending) as ReturnType<typeof createTokenPurchase.response.parse>;
    expect(pending.status).toBe('pending');
    const approved = parse(simulatePurchase.response, real.billing_token_purchase_approved) as ReturnType<typeof Payment.parse>;
    expect(approved).toMatchObject({ status: 'approved', checkout_url: null, balance_after: 1000 });
    const rejected = parse(getTokenPurchase.response, real.billing_token_purchase_rejected) as ReturnType<typeof Payment.parse>;
    expect(rejected.failure_reason).toMatch(/rechazó/);
    const planPayment = parse(Payment, real.billing_plan_payment_pending) as ReturnType<typeof Payment.parse>;
    expect(planPayment.tokens).toBe(0);
    expect(planPayment.plan_id).toBeTruthy();
    parse(Payment, real.billing_plan_payment_approved);
  });

  it('errores: lugares, plan actual, rol y pagos sin proveedor', () => {
    const seats = error('billing_change_plan_409_seats', 'billing.changePlan');
    expect(seats.isConflict).toBe(true);
    expect(seats.code).toBe('seats_exceeded');
    expect(errorMessage(seats)).toBe('Ocupás 4 lugares y el plan Gratis tiene 2. Quitá miembros o invitaciones antes de bajar.');
    expect(fieldError(error('billing_change_plan_400_current'), 'plan_id')).toBe('Ya es tu plan actual.');
    expect(errorMessage(error('billing_change_plan_403_role'))).toBe('Solo el dueño o un admin del espacio pueden hacer esto. Pedile acceso a un admin.');
    expect(fieldError(error('billing_purchase_400_min'), 'custom_tokens')).toBe('El mínimo es 100 tokens.');
    const unavailable = error('billing_purchase_501_no_provider', 'billing.createPurchase');
    expect(unavailable.code).toBe('payments_unavailable');
    expect(errorMessage(unavailable, 'No pudimos iniciar la compra')).toBe('Los pagos todavía no están habilitados.');
  });
});
