import type { Platform } from '../contracts/common';
import { loadDb, saveDb } from './state';

/**
 * Mock-only actions used by the dev routes that stand in for third parties:
 * the payment provider's checkout and the social network's authorization window.
 * They change the mock database directly; nothing here exists in the real API.
 */

/** What the fake checkout decided. The purchase settles a few seconds after the app polls it. */
export function resolveMockPurchase(purchaseId: string, approved: boolean): boolean {
  const db = loadDb();
  const purchase = db.purchases.find((item) => item.id === purchaseId);
  if (!purchase || purchase.status !== 'pending') return false;
  purchase._reject = !approved || db.flags.rejectPayments;
  saveDb(db);
  return true;
}

export function getMockPurchase(purchaseId: string): { tokens: number; amount_usd: number; plan_name: string | null } | null {
  const db = loadDb();
  const purchase = db.purchases.find((item) => item.id === purchaseId);
  if (!purchase) return null;
  const plan = purchase.plan_id ? db.plans.find((item) => item.id === purchase.plan_id) : null;
  return { tokens: purchase.tokens, amount_usd: purchase.amount_usd, plan_name: plan?.name ?? null };
}

/** The user authorized the network: the active workspace's account becomes connected. */
export function completeMockSocialConnect(platform: Platform, workspaceId: string | null): boolean {
  const db = loadDb();
  const target = workspaceId ?? db.users.find((user) => user.id === db.sessionUserId)?.active_workspace_id ?? null;
  const account = db.socialAccounts.find((item) => item.workspace === target && item.platform === platform);
  if (!account) return false;
  const workspace = db.workspaces.find((item) => item.id === target);
  account.status = 'connected';
  account.username = `@${(workspace?.name ?? 'cuenta').toLowerCase().replace(/[^a-z0-9]+/g, '')}`;
  account.connected_at = new Date().toISOString();
  account.tiktok =
    platform === 'TIKTOK'
      ? { privacy_options: ['PUBLIC_TO_EVERYONE', 'MUTUAL_FOLLOW_FRIENDS', 'SELF_ONLY'], comment_disabled: false, duet_disabled: true, stitch_disabled: false }
      : null;
  saveDb(db);
  return true;
}
