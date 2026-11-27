'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '../client';
import type { MemberRole } from '../contracts/common';
import * as contract from '../contracts/workspaces';
import { keys } from '../query';
import { setTokens } from '../session';

export function useWorkspaces() {
  return useQuery({ queryKey: keys.workspaces.all, queryFn: ({ signal }) => apiCall(contract.listWorkspaces, { signal }) });
}

export function useWorkspace(id: string | null | undefined) {
  return useQuery({
    queryKey: keys.workspaces.detail(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getWorkspace, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
  });
}

export function useCreateWorkspace() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => apiCall(contract.createWorkspace, { body: { name } }),
    onSuccess: () => Promise.all([queryClient.invalidateQueries({ queryKey: keys.me }), queryClient.invalidateQueries({ queryKey: keys.workspaces.all })]),
  });
}

export function useUpdateWorkspace(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => apiCall(contract.updateWorkspace, { params: { id }, body: { name } }),
    onSuccess: (workspace) => {
      queryClient.setQueryData(keys.workspaces.detail(id), workspace);
      return Promise.all([queryClient.invalidateQueries({ queryKey: keys.me }), queryClient.invalidateQueries({ queryKey: keys.workspaces.all })]);
    },
  });
}

export function useDeleteWorkspace(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (confirmName: string) => apiCall(contract.deleteWorkspace, { params: { id }, body: { confirm_name: confirmName } }),
    // Nothing of the deleted workspace may be asked for again: its queries (detail, members and every
    // workspace-scoped one, whose key ends with its id) are dropped, not refetched. The caller then
    // switches to another workspace, which reloads the rest.
    onSuccess: () => queryClient.removeQueries({ predicate: (query) => query.queryKey.includes(id) }),
  });
}

export function useTransferWorkspace(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (newOwnerId: string) => apiCall(contract.transferWorkspace, { params: { id }, body: { new_owner_id: newOwnerId } }),
    onSuccess: () => queryClient.invalidateQueries(),
  });
}

export function useMembers(workspaceId: string | null | undefined, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: keys.workspaces.members(workspaceId ?? ''),
    queryFn: ({ signal }) => apiCall(contract.listMembers, { params: { id: workspaceId ?? '' }, signal }),
    enabled: Boolean(workspaceId) && enabled,
  });
}

export function useUpdateMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ memberId, role }: { memberId: string; role: MemberRole }) =>
      apiCall(contract.updateMember, { params: { id: workspaceId, memberId }, body: { role } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.workspaces.members(workspaceId) }),
  });
}

export function useRemoveMember(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (memberId: string) => apiCall(contract.removeMember, { params: { id: workspaceId, memberId } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.workspaces.members(workspaceId) }),
  });
}

export function useInviteMembers(workspaceId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: contract.InviteRequest) => apiCall(contract.inviteMembers, { params: { id: workspaceId }, body }),
    onSuccess: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.workspaces.members(workspaceId) }),
        queryClient.invalidateQueries({ queryKey: keys.me }),
      ]),
  });
}

export function useResendInvitation(workspaceId: string) {
  return useMutation({
    mutationFn: (memberId: string) => apiCall(contract.resendInvitation, { params: { id: workspaceId, memberId } }),
  });
}

/**
 * A5: public data of an invitation. `enabled: false` stops reading it: once accepted the link is spent,
 * and the refetch that follows every mutation would be a 404 by design.
 */
export function useInvitation(token: string, { enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: keys.workspaces.invitation(token),
    // A POST that only reads: the token goes in the body, never in a URL.
    queryFn: ({ signal }) => apiCall(contract.getInvitation, { body: { token }, signal }),
    enabled: enabled && Boolean(token),
    retry: false,
  });
}

export function useAcceptInvitation(token: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string; password?: string; terms_accepted?: boolean } = {}) =>
      apiCall(contract.acceptInvitation, { body: { ...body, token } }),
    onSuccess: async (result) => {
      if (result.tokens) setTokens(result.tokens);
      await queryClient.invalidateQueries();
    },
  });
}
