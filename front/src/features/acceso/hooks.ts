'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '@/api/client';
import { acceptInvitation } from '@/api/contracts/workspaces';
import { setActiveWorkspaceId, setTokens } from '@/api/session';

export interface JoinInput {
  name?: string;
  password?: string;
  terms_accepted?: boolean;
}

/**
 * A5: accepts the invitation and leaves the app on the workspace the user has just joined.
 *
 * Same endpoint as `useAcceptInvitation`, with one difference in the order: the
 * active workspace is stored *before* the profile is fetched again. The session
 * picks the workspace when `/me` changes, so storing it afterwards left the bar
 * on the previous workspace while the requests already went to the new one.
 */
export function useJoinWorkspace(token: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: JoinInput = {}) => apiCall(acceptInvitation, { params: { token }, body }),
    onSuccess: async (result) => {
      if (result.tokens) setTokens(result.tokens);
      setActiveWorkspaceId(result.workspace_id);
      await queryClient.invalidateQueries();
    },
  });
}
