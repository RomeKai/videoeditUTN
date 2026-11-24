'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '../client';
import * as contract from '../contracts/brandkits';
import { keys } from '../query';

export function useBrandKits() {
  return useQuery({ queryKey: keys.brandKits.all, queryFn: ({ signal }) => apiCall(contract.listBrandKits, { signal }) });
}

export function useBrandKit(id: string | null | undefined) {
  return useQuery({
    queryKey: keys.brandKits.detail(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getBrandKit, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
  });
}

function useInvalidateBrandKits() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([queryClient.invalidateQueries({ queryKey: keys.brandKits.all }), queryClient.invalidateQueries({ queryKey: keys.me })]);
}

export function useCreateBrandKit() {
  const invalidate = useInvalidateBrandKits();
  return useMutation({
    mutationFn: (body: contract.BrandKitInput) => apiCall(contract.createBrandKit, { body }),
    onSuccess: invalidate,
  });
}

/** Also used for "marcar como default": `{ is_default: true }`. */
export function useUpdateBrandKit(id: string) {
  const invalidate = useInvalidateBrandKits();
  return useMutation({
    mutationFn: (body: Partial<contract.BrandKitInput>) => apiCall(contract.updateBrandKit, { params: { id }, body }),
    onSuccess: invalidate,
  });
}

/** For lists, where the id is only known when the action runs. */
export function useSetDefaultBrandKit() {
  const invalidate = useInvalidateBrandKits();
  return useMutation({
    mutationFn: (id: string) => apiCall(contract.updateBrandKit, { params: { id }, body: { is_default: true } }),
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
