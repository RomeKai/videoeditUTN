'use client';

import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiCall } from '../client';
import * as contract from '../contracts/billing';
import { keys, pollWhile } from '../query';
import { useScopedQuery } from './scope';

/** Public: works without a session (A6). */
export function usePlans({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({ queryKey: keys.billing.plans, queryFn: ({ signal }) => apiCall(contract.listPlans, { signal }), staleTime: 5 * 60_000, enabled });
}

/** Formula values for the in-browser estimate (lib/pricing.ts · estimateCost). Public. */
export function usePricing({ enabled = true }: { enabled?: boolean } = {}) {
  return useQuery({ queryKey: keys.billing.pricing, queryFn: ({ signal }) => apiCall(contract.getPricing, { signal }), staleTime: 5 * 60_000, enabled });
}

/** Backend estimate with ETA. Pass null to keep it idle (no file chosen yet). */
export function useEstimate(input: contract.EstimateRequest | null) {
  return useScopedQuery({
    queryKey: keys.billing.estimate(input),
    queryFn: ({ signal }) => apiCall(contract.estimateCost, { body: input as contract.EstimateRequest, signal }),
    enabled: input !== null,
    placeholderData: keepPreviousData,
    retry: false,
  });
}

export function useSubscription({ enabled = true }: { enabled?: boolean } = {}) {
  return useScopedQuery({
    queryKey: keys.billing.subscription,
    queryFn: ({ signal }) => apiCall(contract.getSubscription, { signal }),
    enabled,
  });
}

export function useChangePlan() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (planId: string) => apiCall(contract.changePlan, { body: { plan_id: planId } }),
    onSuccess: () =>
      Promise.all([queryClient.invalidateQueries({ queryKey: keys.billing.all }), queryClient.invalidateQueries({ queryKey: keys.me })]),
  });
}

/** Available and reserved balance: always visible in the topbar (map 1.5). Polls while something is reserved. */
export function useWallet({ enabled = true }: { enabled?: boolean } = {}) {
  return useScopedQuery({
    queryKey: keys.billing.wallet,
    queryFn: ({ signal }) => apiCall(contract.getWallet, { signal }),
    enabled,
    ...pollWhile<contract.Wallet>((wallet) => Boolean(wallet && wallet.reserved_balance > 0)),
  });
}

export function useUsage(month?: string) {
  return useScopedQuery({
    queryKey: keys.billing.usage(month),
    queryFn: ({ signal }) => apiCall(contract.getUsage, { query: { month }, signal }),
  });
}

export function useTransactions(query: contract.TransactionsQuery = {}, { enabled = true }: { enabled?: boolean } = {}) {
  return useScopedQuery({
    queryKey: keys.billing.transactions(query),
    queryFn: ({ signal }) => apiCall(contract.listTransactions, { query, signal }),
    placeholderData: keepPreviousData,
    enabled,
  });
}

/** CSV text of the filtered movements; the screen turns it into a download. */
export function useExportTransactions() {
  return useMutation({
    mutationFn: (query: Omit<contract.TransactionsQuery, 'page' | 'page_size'> = {}) => apiCall(contract.exportTransactions, { query }),
  });
}

export function useTokenPackages({ enabled = true }: { enabled?: boolean } = {}) {
  return useScopedQuery({ queryKey: keys.billing.packages, queryFn: ({ signal }) => apiCall(contract.listTokenPackages, { signal }), staleTime: 5 * 60_000, enabled });
}

/** G4: returns the purchase with `checkout_url`; the screen sends the browser there. */
export function useCreateTokenPurchase() {
  return useMutation({
    mutationFn: (body: { package_id?: string; custom_tokens?: number; return_url: string }) => apiCall(contract.createTokenPurchase, { body }),
  });
}

/** G5: polls every 5 s while the payment is pending; refreshes the balance when it settles. */
export function useTokenPurchase(id: string | null | undefined) {
  return useScopedQuery({
    queryKey: keys.billing.purchase(id ?? ''),
    queryFn: ({ signal }) => apiCall(contract.getTokenPurchase, { params: { id: id ?? '' }, signal }),
    enabled: Boolean(id),
    ...pollWhile<contract.TokenPurchase>((purchase) => purchase?.status === 'pending'),
  });
}
