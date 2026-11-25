'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '../client';
import * as contract from '../contracts/brandkits';
import { keys } from '../query';
import { getActiveWorkspaceId } from '../session';
import { scopedKey, useScopedQuery } from './scope';

export function useBrandKits() {
  return useScopedQuery({ queryKey: keys.brandKits.all, queryFn: ({ signal }) => apiCall(contract.listBrandKits, { signal }) });
}

export function useBrandKit(id: string | null | undefined) {
  return useScopedQuery({
    queryKey: keys.brandKits.detail(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getBrandKit, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
  });
}

/**
 * Only one default kit per workspace (map F2). The backend does not enforce it yet, so after
 * marking one the others that still say default are unmarked from here. With a backend that
 * already does it (or the mocks) the fresh list has no other default and nothing is sent.
 *
 * Only kits of the active workspace. The backend scopes the list by `X-Workspace-Id` since PR #125,
 * so the comparison below is defensive: each workspace keeps its own default, and a kit of another
 * one must never be demoted, whatever the list brings.
 */
export async function demoteOtherDefaults(keepId: string): Promise<void> {
  const workspaceId = getActiveWorkspaceId();
  if (!workspaceId) return;
  const kits = await apiCall(contract.listBrandKits, {});
  for (const kit of kits) {
    if (kit.workspace !== workspaceId || !kit.is_default || kit.id === keepId) continue;
    await apiCall(contract.updateBrandKit, { params: { id: kit.id }, body: { is_default: false } });
  }
}

function useInvalidateBrandKits() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([queryClient.invalidateQueries({ queryKey: keys.brandKits.all }), queryClient.invalidateQueries({ queryKey: keys.me })]);
}

export function useCreateBrandKit() {
  const invalidate = useInvalidateBrandKits();
  return useMutation({
    mutationFn: async (body: contract.BrandKitInput) => {
      const created = await apiCall(contract.createBrandKit, { body });
      if (body.is_default) await demoteOtherDefaults(created.id);
      return created;
    },
    onSuccess: invalidate,
  });
}

/** Also used for "marcar como default": `{ is_default: true }`. */
export function useUpdateBrandKit(id: string) {
  const invalidate = useInvalidateBrandKits();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: Partial<contract.BrandKitInput>) => {
      const saved = await apiCall(contract.updateBrandKit, { params: { id }, body });
      if (body.is_default) await demoteOtherDefaults(id);
      return saved;
    },
    onSuccess: (saved) => {
      // The editor compares its draft with this copy: it must be the saved one right away, not after the refetch.
      queryClient.setQueryData(scopedKey(keys.brandKits.detail(id)), saved);
      return invalidate();
    },
  });
}

/** For lists, where the id is only known when the action runs. */
export function useSetDefaultBrandKit() {
  const invalidate = useInvalidateBrandKits();
  return useMutation({
    mutationFn: async (id: string) => {
      const saved = await apiCall(contract.updateBrandKit, { params: { id }, body: { is_default: true } });
      await demoteOtherDefaults(id);
      return saved;
    },
    onSuccess: invalidate,
  });
}

export function useDeleteBrandKit() {
  const invalidate = useInvalidateBrandKits();
  return useMutation({
    mutationFn: (id: string) => apiCall(contract.deleteBrandKit, { params: { id } }),
    onSuccess: invalidate,
  });
}

export function useDuplicateBrandKit() {
  const invalidate = useInvalidateBrandKits();
  return useMutation({
    mutationFn: (id: string) => apiCall(contract.duplicateBrandKit, { params: { id } }),
    onSuccess: invalidate,
  });
}
