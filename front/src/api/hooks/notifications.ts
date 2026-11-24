'use client';

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '../client';
import * as contract from '../contracts/notifications';
import { keys } from '../query';

/** J2: the last 20 and the unread count for the bell. Refreshed every minute. */
export function useNotifications({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: keys.notifications,
    queryFn: ({ signal }) => apiCall(contract.listNotifications, { signal }),
    enabled,
    refetchInterval: 60_000,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiCall(contract.markNotificationRead, { params: { id } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.notifications }),
  });
}

export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiCall(contract.markAllNotificationsRead),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.notifications }),
  });
}
