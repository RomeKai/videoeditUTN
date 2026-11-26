import type { Subscription, Transaction, Wallet } from '../contracts/billing';
import type { Role } from '../contracts/common';
import type { Clip, Project } from '../contracts/projects';
import type { ScheduledPost } from '../contracts/social';
import type { Me } from '../contracts/users';
import type { Member } from '../contracts/workspaces';
import { workspaceDay, type MockClip, type MockDb, type MockMember, type MockPost, type MockProject, type MockTransaction, type MockUser, type MockWorkspace } from './db';
import { dec } from './http';
import { generateTranscript, PRICING } from './seed';
import { roleIn, walletOf } from './sim';

/** Wire types: what goes on the network (decimals as strings), i.e. the *input* of the zod schemas. */
type Wire<T> = { [K in keyof T]: T[K] extends number ? number | string : T[K] };

export function serializeClip(db: MockDb, clip: MockClip): Clip {
  const { _renderFails, ...rest } = clip;
  void _renderFails;
  return {
    ...rest,
    posts: db.posts
      .filter((post) => post.video_clip === clip.id)
      .map((post) => ({ id: post.id, platform: post.platform, status: post.status, publish_at: post.publish_at })),
  };
}

/**
 * `full` adds the transcript (detail). The list leaves it out: the real list
 * sends it too, which is heavy; see the note in CONTRACT.md.
 */
export function serializeProject(db: MockDb, project: MockProject, { full }: { full: boolean }): Project {
  const { _sim, _transcriptSeed, _reservations, _paidRender, clips, ...rest } = project;
  void _sim;
  void _paidRender;
  void _reservations;
  const duration = project.duration_seconds ?? 0;
  return {
    ...rest,
    clips: clips.map((clip) => serializeClip(db, clip)),
    transcript_data: full && _transcriptSeed !== null ? generateTranscript(_transcriptSeed, duration) : null,
  };
}

export function serializePost(post: MockPost): ScheduledPost {
  const { workspace, _sim, ...rest } = post;
  void workspace;
  void _sim;
  return rest;
}

export function serializeTransaction(transaction: MockTransaction): Wire<Transaction> {
  const { workspace, _settled_at, ...rest } = transaction;
  void workspace;
  void _settled_at;
  return { ...rest, amount: dec(transaction.amount, 10) };
}

/** When a charge counts as consumption: the day it was charged (`settled_at`), else its reservation (rows from before). */
export function chargedAt(transaction: MockTransaction): string {
  return transaction._settled_at ?? transaction.created_at;
}

export function serializeWallet(db: MockDb, workspaceId: string, now: number): Wire<Omit<Wallet, 'reservations'>> & { reservations: unknown[] } {
  const wallet = walletOf(db, workspaceId);
  // This month in the workspace's time zone, by when each charge was charged (like the backend).
  const month = workspaceDay(now).slice(0, 7);
  const transactions = db.transactions.filter((item) => item.workspace === workspaceId);
  const spent = transactions
    .filter((item) => item.transaction_type === 'SPEND' && item.status === 'COMPLETED' && workspaceDay(chargedAt(item)).startsWith(month))
    .reduce((sum, item) => sum + item.amount, 0);
  const plan = planOf(db, workspaceId);
  // "Below what an average video costs": 20 minutes at 720p with the plan's discount.
  const threshold = 1200 * PRICING.base_cost_per_second_720p * (1 - (plan?.base_discount_rate ?? 0));
  return {
    id: wallet.id,
    workspace: wallet.workspace,
    available_balance: dec(wallet.available_balance, 10),
    reserved_balance: dec(wallet.reserved_balance, 10),
    updated_at: wallet.updated_at,
    spent_this_month: dec(spent),
    low_balance_threshold: dec(threshold),
    // Newest first, like the backend.
    reservations: transactions
      .filter((item) => item.status === 'RESERVED')
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .map((item) => ({
        transaction_id: item.id,
        project_id: item.project?.id ?? null,
        project_title: item.project?.title ?? null,
        amount: dec(item.amount),
        created_at: item.created_at,
      })),
  };
}

export function planOf(db: MockDb, workspaceId: string) {
  const workspace = db.workspaces.find((item) => item.id === workspaceId);
  return db.plans.find((plan) => plan.id === workspace?.subscription_plan) ?? null;
}

export function serializeSubscription(db: MockDb, workspace: MockWorkspace): Subscription {
  const plan = planOf(db, workspace.id);
  if (!plan) throw new Error('Workspace sin plan');
  const pending = workspace.pending_plan ? db.plans.find((item) => item.id === workspace.pending_plan?.id) : null;
  return {
    plan,
    status: 'active',
    renews_at: workspace.renews_at,
    pending_plan: pending && workspace.pending_plan ? { id: pending.id, name: pending.name, effective_at: workspace.pending_plan.effective_at } : null,
  };
}

export function workspacesOf(db: MockDb, userId: string): Array<{ workspace: MockWorkspace; role: Role }> {
  return db.workspaces
    .map((workspace) => ({ workspace, role: roleIn(db, workspace.id, userId) }))
    .filter((item): item is { workspace: MockWorkspace; role: Role } => item.role !== null);
}

export function serializeMe(db: MockDb, user: MockUser): Wire<Me> {
  const mine = workspacesOf(db, user.id);
  const active = mine.find((item) => item.workspace.id === user.active_workspace_id) ?? mine[0] ?? null;
  const activeId = active?.workspace.id ?? null;
  const { notification_preferences, ...profile } = user;
  void notification_preferences;
  return {
    ...profile,
    active_workspace_id: activeId,
    onboarding: {
      has_project: db.projects.some((project) => project.workspace === activeId),
      has_brand_kit: db.brandKits.some((kit) => kit.workspace === activeId),
      has_social_account: db.socialAccounts.some((account) => account.workspace === activeId && account.status === 'connected'),
      has_invited_member: db.members.filter((member) => member.workspace === activeId).length > 1,
    },
    workspaces: mine.map(({ workspace, role }) => {
      const plan = planOf(db, workspace.id);
      return {
        id: workspace.id,
        name: workspace.name,
        role,
        plan: plan ? { id: plan.id, name: plan.name } : null,
        available_balance: walletOf(db, workspace.id).available_balance,
      };
    }),
  };
}

export function serializeMember(db: MockDb, member: MockMember): Member {
  const workspace = db.workspaces.find((item) => item.id === member.workspace);
  const user = member.user ? db.users.find((item) => item.id === member.user) : null;
  const inviter = member.invited_by ? db.users.find((item) => item.id === member.invited_by) : null;
  return {
    id: member.id,
    user: user ? { id: user.id, name: user.name, email: user.email } : null,
    email: member.email,
    role: workspace && member.user && workspace.owner === member.user ? 'owner' : member.role,
    status: member.status,
    joined_at: member.joined_at,
    invited_by: inviter ? { id: inviter.id, name: inviter.name } : null,
  };
}

export function serializeWorkspace(db: MockDb, workspace: MockWorkspace, userId: string) {
  return {
    id: workspace.id,
    name: workspace.name,
    owner: workspace.owner,
    subscription_plan: workspace.subscription_plan,
    created_at: workspace.created_at,
    role: roleIn(db, workspace.id, userId) ?? undefined,
  };
}
