import { describe, expect, it } from 'vitest';
import type { ZodTypeAny } from 'zod';
import { approveErrorKind, errorMessage, fieldError, pipelineErrorMessage, planLimitKind, uploadErrorCode } from '@/lib/error-messages';
import { describeProjectStatus } from '@/lib/project-status';
import { ApiError } from '../errors';
import { TokenPair, refreshToken } from './auth';
import { BrandKit, listBrandKits } from './brandkits';
import { ClipPreview } from './clips';
import real from './drf/responses.json';
import { ApprovePaperEditResponse, Clip, CreateFromPromptResponse, CreateProjectResponse, PaperEditData, Project, ProjectsResponse } from './projects';

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

  it('un `draft` en cola de un render pago se distingue de una sugerencia (`render_queued`)', () => {
    const review = Project.parse(real.project_in_review_auto_render_skipped);
    expect(review.clips.map((clip) => [clip.status, clip.render_queued])).toEqual([
      ['draft', false],
      ['draft', false],
    ]);
    const suggestion = real.project_in_review_auto_render_skipped.clips[0];
    expect(Clip.parse({ ...suggestion, render_queued: true }).render_queued).toBe(true);
    // Optional: a body without it (the mocks) is still a clip.
    expect(Clip.parse({ ...suggestion, render_queued: undefined }).render_queued).toBeUndefined();
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
