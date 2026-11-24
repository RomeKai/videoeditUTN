'use client';

import { useQuery } from '@tanstack/react-query';
import { apiCall } from '../client';
import * as contract from '../contracts/system';
import { keys } from '../query';

/** K5: whether the platform is in maintenance and until when. */
export function useSystemStatus() {
  return useQuery({
    queryKey: keys.system,
    queryFn: ({ signal }) => apiCall(contract.getSystemStatus, { signal }),
    staleTime: 60_000,
    refetchInterval: 60_000,
    retry: false,
  });
}
