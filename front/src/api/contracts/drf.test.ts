import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { approveErrorKind, errorMessage, fieldError, pipelineErrorMessage, planLimitKind, uploadErrorCode } from '@/lib/error-messages';
import { describeProjectStatus } from '@/lib/project-status';
import { ApiError } from '../errors';
import { TokenPair, refreshToken } from './auth';
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
