'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import { apiCall } from '../client';
import * as contract from '../contracts/users';
import { keys } from '../query';
import { getAccessSnapshot, subscribeSession } from '../session';

/** True while there is a stored session (does not say it is still valid). */
export function useHasSession(): boolean {
  return useSyncExternalStore(subscribeSession, () => getAccessSnapshot() !== null, () => false);
}

/** Profile, workspaces and role in each. Only runs with a session. */
export function useMe() {
  const hasSession = useHasSession();
  return useQuery({
    queryKey: keys.me,
    queryFn: ({ signal }) => apiCall(contract.getMe, { signal }),
    enabled: hasSession,
    staleTime: 60_000,
  });
}

export function useUpdateMe() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: contract.UpdateMeRequest) => apiCall(contract.updateMe, { body }),
    onSuccess: (me) => queryClient.setQueryData(keys.me, me),
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) => apiCall(contract.changePassword, { body }),
  });
}

export function useNotificationPreferences() {
  return useQuery({
    queryKey: keys.notificationPreferences,
    queryFn: ({ signal }) => apiCall(contract.getNotificationPreferences, { signal }),
  });
}

export function useUpdateNotificationPreferences() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<contract.NotificationPreferences>) => apiCall(contract.updateNotificationPreferences, { body }),
    onSuccess: (preferences) => queryClient.setQueryData(keys.notificationPreferences, preferences),
  });
}
