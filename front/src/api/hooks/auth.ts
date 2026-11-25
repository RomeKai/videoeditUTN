'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '../client';
import * as contract from '../contracts/auth';
import { clearSession, getTokens, setTokens } from '../session';

/** A1 and D9: email + password → JWT pair, stored for the HTTP client. */
export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: contract.LoginRequest) => apiCall(contract.login, { body }),
    onSuccess: async (tokens) => {
      setTokens(tokens);
      await queryClient.invalidateQueries();
    },
  });
}

/** Closes the session here (and on every device with `allDevices`). Local tokens are dropped even if the request fails. */
export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ allDevices = false }: { allDevices?: boolean } = {}) => {
      const refresh = getTokens()?.refresh;
      if (!refresh) return;
      await apiCall(contract.logout, { body: { refresh, all_devices: allDevices } });
    },
    onSettled: () => {
      clearSession();
      queryClient.clear();
    },
  });
}

export function useRegister() {
  return useMutation({ mutationFn: (body: contract.RegisterRequest) => apiCall(contract.register, { body }) });
}

/** A3: the link's token verifies the account and starts the session. */
export function useVerifyEmail() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (token: string) => apiCall(contract.verifyEmail, { body: { token } }),
    onSuccess: async (tokens) => {
      setTokens(tokens);
      await queryClient.invalidateQueries();
    },
  });
}

export function useResendVerification() {
  return useMutation({ mutationFn: (email: string) => apiCall(contract.resendVerification, { body: { email } }) });
}

export function useRequestPasswordReset() {
  return useMutation({ mutationFn: (email: string) => apiCall(contract.requestPasswordReset, { body: { email } }) });
}

export function useConfirmPasswordReset() {
  return useMutation({
    mutationFn: (body: { token: string; password: string }) => apiCall(contract.confirmPasswordReset, { body }),
  });
}
