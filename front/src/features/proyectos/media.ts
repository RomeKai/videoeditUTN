import { API_MOCKS } from '@/api/config';
import type { Plan, Pricing } from '@/api/contracts/billing';
import type { UploadErrorCode } from '@/api/contracts/projects';

export interface MediaInfo {
  /** Seconds, or null when the browser cannot read this container (the backend measures it after the upload). */
  duration: number | null;
  width: number | null;
  height: number | null;
}

/** Limits of services/media_validation.py that the browser can check before uploading. */
export const MAX_SIDE_PX = 4096;
export const MIN_SIDE_PX = 240;
const PROBE_TIMEOUT_MS = 8000;

/**
 * Reads duration and size from the file's metadata with a detached <video>.
 * Nothing is uploaded. Resolves with nulls when the browser cannot decode it.
 */
export function probeVideoFile(file: File): Promise<MediaInfo> {
  const unknown: MediaInfo = { duration: null, width: null, height: null };
  if (typeof document === 'undefined' || typeof URL === 'undefined' || typeof URL.createObjectURL !== 'function') return Promise.resolve(unknown);
  return new Promise((resolve) => {
    let url = '';
    try {
      url = URL.createObjectURL(file);
    } catch {
      resolve(unknown);
      return;
    }
    const video = document.createElement('video');
    let settled = false;
    const finish = (info: MediaInfo) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      video.removeAttribute('src');
      URL.revokeObjectURL(url);
      resolve(info);
    };
    const timer = window.setTimeout(() => finish(unknown), PROBE_TIMEOUT_MS);
    video.preload = 'metadata';
    video.muted = true;
    video.onloadedmetadata = () =>
      finish({
        duration: Number.isFinite(video.duration) && video.duration > 0 ? video.duration : null,
        width: video.videoWidth || null,
        height: video.videoHeight || null,
      });
    video.onerror = () => finish(unknown);
    video.src = url;
  });
}

/**
 * Mock mode only: test files are not real videos, so the duration follows the
 * same rule as the mocked backend ("NNmin" in the name, else by size).
 */
export function mockDuration(name: string, size: number): number {
  const minutes = /(\d+)\s*min/i.exec(name);
  if (minutes) return Number(minutes[1]) * 60;
  return Math.max(180, Math.round((size / (1024 * 1024)) * 8));
}

export async function readMedia(file: File): Promise<MediaInfo> {
  const info = await probeVideoFile(file);
  if (info.duration === null && API_MOCKS) return { ...info, duration: mockDuration(file.name, file.size) };
  return info;
}

export type FileProblem =
  | { code: UploadErrorCode; limitSeconds?: number; byPlan?: boolean }
  /** The serializer rejects files over its size ceiling (no code in map §4). */
  | { code: 'too_heavy'; limitBytes: number };

function extensionOf(name: string): string {
  const match = /\.[a-z0-9]+$/i.exec(name);
  return match ? match[0].toLowerCase() : '';
}

/** Type and size: checked as soon as the file is picked. */
export function checkFile(file: Pick<File, 'name' | 'type' | 'size'>, pricing: Pick<Pricing, 'upload_extensions' | 'upload_max_size_bytes'>): FileProblem | null {
  const allowed = pricing.upload_extensions.map((item) => item.toLowerCase());
  if (!allowed.includes(extensionOf(file.name)) || (file.type !== '' && !file.type.startsWith('video/') && file.type !== 'application/octet-stream')) {
    return { code: 'not_a_video' };
  }
  if (file.size > pricing.upload_max_size_bytes) return { code: 'too_heavy', limitBytes: pricing.upload_max_size_bytes };
  return null;
}

/** Duration against the plan and the system ceiling, and resolution, once the metadata is read. */
export function checkMedia(
  info: MediaInfo,
  pricing: Pick<Pricing, 'upload_max_duration_seconds'>,
  plan: Pick<Plan, 'max_video_duration_seconds'> | null,
): FileProblem | null {
  if (info.width !== null && info.height !== null) {
    if (Math.max(info.width, info.height) > MAX_SIDE_PX) return { code: 'too_large' };
    if (Math.min(info.width, info.height) < MIN_SIDE_PX) return { code: 'too_small' };
  }
  if (info.duration !== null) {
    if (plan && info.duration > plan.max_video_duration_seconds) return { code: 'too_long', limitSeconds: plan.max_video_duration_seconds, byPlan: true };
    if (info.duration > pricing.upload_max_duration_seconds) return { code: 'too_long', limitSeconds: pricing.upload_max_duration_seconds };
  }
  return null;
}

/** ".mp4, .mov, .avi" → "MP4, MOV o AVI" */
export function extensionsLabel(extensions: readonly string[]): string {
  const names = extensions.map((item) => item.replace(/^\./, '').toUpperCase());
  if (names.length <= 1) return names.join('');
  return `${names.slice(0, -1).join(', ')} o ${names[names.length - 1]}`;
}
