import type { Plan, TokenPurchase, Transaction } from '../contracts/billing';
import type { BrandKit } from '../contracts/brandkits';
import type { ClipPreview } from '../contracts/clips';
import type { MemberRole, Platform } from '../contracts/common';
import type { Notification } from '../contracts/notifications';
import type { ActivityItem, Clip, Project } from '../contracts/projects';
import type { ScheduledPost, SocialAccount } from '../contracts/social';
import type { NotificationPreferences, OnboardingUseCase } from '../contracts/users';

/** Deterministic UUID-shaped id for seeded rows: readable in the network tab and stable across reloads. */
export function mid(kind: number, n: number): string {
  return `${kind.toString(16).padStart(8, '0')}-0000-4000-8000-${n.toString(16).padStart(12, '0')}`;
}

export function newId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const hex = (length: number) => Array.from({ length }, () => Math.floor(Math.random() * 16).toString(16)).join('');
  return `${hex(8)}-${hex(4)}-4${hex(3)}-8${hex(3)}-${hex(12)}`;
}

export const KIND = {
  user: 1,
  workspace: 2,
  plan: 3,
  member: 4,
  wallet: 5,
  transaction: 6,
  project: 7,
  clip: 8,
  brandKit: 9,
  post: 10,
  notification: 11,
  activity: 12,
  purchase: 13,
  preview: 14,
} as const;

export interface MockUser {
  id: string;
  name: string;
  email: string;
  /** Any password works in the mocks except the literal "incorrecta". */
  is_email_verified: boolean;
  tour_completed: boolean;
  terms_accepted_at: string | null;
  pending_email: string | null;
  onboarding_use_case: OnboardingUseCase | null;
  active_workspace_id: string | null;
  notification_preferences: NotificationPreferences;
}

export interface MockWorkspace {
  id: string;
  name: string;
  owner: string;
  subscription_plan: string;
  created_at: string;
  renews_at: string | null;
  pending_plan: { id: string; effective_at: string } | null;
}

export interface MockMember {
  id: string;
  workspace: string;
  /** Null while the invited email has no account. */
  user: string | null;
  email: string;
  role: MemberRole;
  status: 'pending' | 'active';
  joined_at: string;
  invited_by: string | null;
  /** Token of the invitation link while pending. */
  token: string | null;
}

export interface MockWallet {
  id: string;
  workspace: string;
  available_balance: number;
  reserved_balance: number;
  updated_at: string;
}

export type MockTransaction = Transaction & { workspace: string };

/** How a live (user-created) project moves on. Seeded projects have no simulation: they stay as drawn. */
export interface ProjectSim {
  phase: 'ingest' | 'render';
  /** Epoch ms when the phase started. */
  startedAt: number;
  /** Fails at this stage with this `pipeline_error_code`. */
  failAt?: { stage: 'audio_extracted' | 'transcribed' | 'moderated' | 'clips_selected'; code: string };
  /** Waits for capacity (pending_capacity) during this many ms before the second stage. */
  waitMs?: number;
  /** One clip fails in the render: the project ends `partial`. */
  failOneClip?: boolean;
}

/** `_renderFails`: the next render of the clip fails (tests and the mock panel set it). */
export type MockClip = Clip & { _renderFails?: boolean };

/**
 * A render the user paid for after the project ended (CLIP_RENDER in the backend): a clip rendered
 * again, a failed clip retried or every clip with another style. One per project at a time.
 */
export interface PaidRender {
  /** Its reservation: settled by the seconds delivered when the last clip ends. */
  reservation: string;
  clipIds: string[];
  /** What the user had of each clip: it comes back if the new render of that clip fails. */
  previous: Record<string, { status: 'completed' | 'published'; key: string | null }>;
}

export type MockProject = Omit<Project, 'clips' | 'transcript_data'> & {
  clips: MockClip[];
  /** Transcripts are generated on demand from this seed instead of being stored. */
  _transcriptSeed: number | null;
  _sim?: ProjectSim;
  /** Reservation of the creation and of the render, to settle or refund them. */
  _reservations: string[];
  /** The paid render in progress, if any (see PaidRender). */
  _paidRender?: PaidRender;
};

/** `regen`: how many times the AI text was regenerated (E1 · Regenerar); the new text replaces the current one. */
export type MockPost = ScheduledPost & { workspace: string; _sim?: { startedAt: number; fails?: boolean; regen?: number } };

export type MockNotification = Notification & { workspace: string; user: string };

export type MockActivity = ActivityItem & { project: string };

export type MockPurchase = TokenPurchase & { workspace: string; _settleAt: number | null; _reject: boolean };

/** Time zone of the workspaces in the mocks: months and days of Uso are counted in it (fixed UTC−3). */
export const WORKSPACE_UTC_OFFSET_MS = -3 * 3600 * 1000;

/** `YYYY-MM-DD` of a moment in the workspace's time zone. */
export function workspaceDay(value: string | number): string {
  return new Date(new Date(value).getTime() + WORKSPACE_UTC_OFFSET_MS).toISOString().slice(0, 10);
}

/** ISO date and time of a moment in the workspace's time zone, with its offset: `2026-11-04T12:00:00-03:00`. */
export function workspaceDateTime(value: string | number): string {
  return `${new Date(new Date(value).getTime() + WORKSPACE_UTC_OFFSET_MS).toISOString().slice(0, 19)}-03:00`;
}

export type MockPreview = ClipPreview & { _startedAt: number };

export type MockSocialAccount = SocialAccount & { workspace: string };

export interface MockInvitation {
  token: string;
  member: string | null;
  workspace_id: string;
  workspace_name: string;
  inviter_name: string;
  role: MemberRole;
  email: string;
  status: 'pending' | 'expired' | 'used';
  has_account: boolean;
}

export interface MockFlags {
  /** Every authenticated request answers 401 and the refresh fails: dialog D9. */
  sessionExpired: boolean;
  maintenance: boolean;
  /** Every GET answers 500: error banners and K3. */
  serverDown: boolean;
  /** Token purchases get rejected by the "payment provider". */
  rejectPayments: boolean;
  /** Login answers 403 email_not_verified. */
  unverifiedLogin: boolean;
  /** Plan upgrades need a payment: `changePlan` answers with a `checkout_url` instead of applying. */
  planCheckout: boolean;
}

export interface MockDb {
  /** Answers of the writes that came with an `Idempotency-Key`, by workspace, endpoint and key. */
  idempotency?: Record<string, { status: number; data: unknown }>;
  version: number;
  scenario: string;
  /** Epoch ms of the seed: seeded dates are relative to it. */
  seededAt: number;
  /** The user the auto-login of the scenario signs in. Null: starts logged out. */
  sessionUserId: string | null;
  users: MockUser[];
  workspaces: MockWorkspace[];
  members: MockMember[];
  plans: Plan[];
  wallets: MockWallet[];
  transactions: MockTransaction[];
  projects: MockProject[];
  brandKits: BrandKit[];
  socialAccounts: MockSocialAccount[];
  posts: MockPost[];
  notifications: MockNotification[];
  activity: MockActivity[];
  purchases: MockPurchase[];
  previews: MockPreview[];
  invitations: MockInvitation[];
  flags: MockFlags;
  /** email → epoch ms of the last verification email (60 s cooldown of A3). */
  resendAt: Record<string, number>;
}

export const DB_VERSION = 14;

export const PLATFORMS: Platform[] = ['TIKTOK', 'INSTAGRAM_REELS', 'YOUTUBE_SHORTS'];
