'use client';

import { keepPreviousData, useMutation, useQueryClient } from '@tanstack/react-query';
import { describeProjectStatus } from '@/lib/project-status';
import { apiCall } from '../client';
import * as contract from '../contracts/projects';
import { keys, pollWhile } from '../query';
import { scopedKey, useScopedQuery } from './scope';

/** B2: paginated list (25 per page). Polls every 5 s while any project of the page is in progress. */
export function useProjects(query: contract.ProjectsQuery = {}, { enabled = true }: { enabled?: boolean } = {}) {
  return useScopedQuery({
    queryKey: keys.projects.list(query),
    queryFn: ({ signal }) => apiCall(contract.listProjects, { query, signal }),
    placeholderData: keepPreviousData,
    enabled,
    ...pollWhile<contract.ProjectsResponse>((page) => Boolean(page?.results.some((project) => describeProjectStatus(project).inProgress))),
  });
}

export interface UseProjectOptions {
  /** False stops fetching and polling (the project was just deleted). Default true. */
  enabled?: boolean;
  /**
   * Extra reason to keep polling after the project's own state is final: a clip
   * retried or re-rendered on a finished project leaves the project `completed`
   * or `partial` while that clip is `rendering`.
   */
  pollWhile?: (project: contract.Project) => boolean;
}

/** D1 and its tabs: polls every 5 s until the project reaches a final state (or while `options.pollWhile` says so). */
export function useProject(id: string | null | undefined, options: UseProjectOptions = {}) {
  const { enabled = true, pollWhile: alsoWhile } = options;
  return useScopedQuery({
    queryKey: keys.projects.detail(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getProject, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id) && enabled,
    ...pollWhile<contract.Project>((project) => Boolean(project && (describeProjectStatus(project).inProgress || alsoWhile?.(project)))),
  });
}

/**
 * C1 › `?desde=<id>`: the configuration of a project to start from. Read once:
 * it is copied when the screen opens, so it neither polls nor refetches.
 */
export function useProjectSnapshot(id: string | null | undefined) {
  return useScopedQuery({
    queryKey: [...keys.projects.detail(id ?? ''), 'snapshot'] as const,
    queryFn: ({ signal }) => apiCall(contract.getProject, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}

export interface CreateProjectVariables {
  body: contract.CreateProjectRequest;
  /** With a prompt the request goes to `from-prompt`. */
  prompt?: string;
  onUploadProgress?: (loaded: number, total: number) => void;
  /** Lets the screen cancel the upload. */
  signal?: AbortSignal;
}

/**
 * C1: creates the project (multipart). Errors to handle in the screen:
 * 400 on the file → `uploadErrorCode(error)`; 402 → dialog D2; 403 → dialog D3
 * (`useBillingDialogs().handleError(error)` does both).
 */
export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ body, prompt, onUploadProgress, signal }: CreateProjectVariables) =>
      prompt !== undefined
        ? apiCall(contract.createProjectFromPrompt, { body: { ...body, prompt }, onUploadProgress, signal })
        : apiCall(contract.createProject, { body, onUploadProgress, signal }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.projects.all }),
        queryClient.invalidateQueries({ queryKey: keys.billing.all }),
        queryClient.invalidateQueries({ queryKey: keys.me }),
      ]),
  });
}

export function useRenameProject(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (title: string) => apiCall(contract.updateProject, { params: { id }, body: { title } }),
    onSuccess: (project) => {
      queryClient.setQueryData(scopedKey(keys.projects.detail(id)), project);
      return queryClient.invalidateQueries({ queryKey: keys.projects.all });
    },
  });
}

export function useDeleteProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiCall(contract.deleteProject, { params: { id } }),
    onSuccess: (_result, id) => {
      queryClient.removeQueries({ queryKey: keys.projects.detail(id) });
      return Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.projects.all }),
        queryClient.invalidateQueries({ queryKey: keys.social.posts }),
      ]);
    },
  });
}

/** D2: proxy video, transcript and suggested clips. */
export function usePaperEditData(id: string | null | undefined) {
  return useScopedQuery({
    queryKey: keys.projects.paperEdit(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getPaperEditData, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
    staleTime: 60_000,
  });
}

/** D2 › Renderizar: 402 opens D2 (insufficient balance), 409 means it was already approved (go to Clips). */
export function useApprovePaperEdit(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: contract.ApprovePaperEditRequest) => apiCall(contract.approvePaperEdit, { params: { id }, body }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.projects.all }),
        queryClient.invalidateQueries({ queryKey: keys.billing.all }),
      ]),
  });
}

/**
 * Resumes a failed project (owner and admin). Paid when what is left had been returned: the answer
 * brings `cost_details` with what it reserved again, and a 402 opens D2. No screen calls it yet.
 */
export function useRedispatchProject(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiCall(contract.redispatchProject, { params: { id } }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.projects.all }),
        queryClient.invalidateQueries({ queryKey: keys.billing.all }),
      ]),
  });
}

/** "Renderizar todo de nuevo" / "Volver a renderizar con otro estilo" (paid: confirm the cost first). */
export function useRerenderProject(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: contract.RerenderRequest = {}) => apiCall(contract.rerenderProject, { params: { id }, body }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.projects.all }),
        queryClient.invalidateQueries({ queryKey: keys.billing.all }),
      ]),
  });
}

export function useProjectActivity(id: string | null | undefined) {
  return useScopedQuery({
    queryKey: keys.projects.activity(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getProjectActivity, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
  });
}

/** D3 › Descargar todo: returns the temporary URL of the .zip. */
export function useDownloadAllClips(id: string) {
  return useMutation({
    mutationFn: (clipIds?: string[]) => apiCall(contract.downloadAllClips, { params: { id }, body: { clip_ids: clipIds } }),
  });
}

/** C1 › Pegar link: recognises the link (provider, title, duration). 400 → cannot be downloaded; 403 → longer than the plan allows. */
export function useProbeLink() {
  return useMutation({ mutationFn: (videoUrl: string) => apiCall(contract.probeLink, { body: { video_url: videoUrl } }) });
}
