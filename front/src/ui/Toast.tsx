'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { cx } from '@/lib/cx';
import { Button, IconButton } from './Button';
import { Icon, type IconName } from './Icon';
import styles from './Toast.module.css';

export type ToastTone = 'ok' | 'err' | 'info' | 'warn';

export interface ToastOptions {
  tone?: ToastTone;
  /** One action at the right: "Ver", "Reintentar". */
  action?: { label: string; onClick?: () => void; href?: string };
  /** Milliseconds on screen. Default 5000 (map 1.8). 0 keeps it until closed. */
  duration?: number;
}

interface ToastItem extends ToastOptions {
  id: number;
  message: ReactNode;
}

export interface ToastApi {
  show: (message: ReactNode, options?: ToastOptions) => number;
  /** Confirms an action: "Clip descargado". */
  success: (message: ReactNode, options?: Omit<ToastOptions, 'tone'>) => number;
  /** One-off error: "No pudimos descargar el clip". */
  error: (message: ReactNode, options?: Omit<ToastOptions, 'tone'>) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

/** Two toasts at most (artboard 03 · J4); older ones leave first. */
export const MAX_TOASTS = 2;
export const TOAST_DURATION_MS = 5000;

const TONE_ICON: Record<ToastTone, IconName> = { ok: 'check-circle', err: 'error', info: 'info', warn: 'warning' };

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, number>());
  const durations = useRef(new Map<number, number>());

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.current.delete(id);
    durations.current.delete(id);
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  /** A toast with an action waits while the pointer or the focus is on it, so there is time to use it. */
  const pause = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) window.clearTimeout(timer);
    timers.current.delete(id);
  }, []);
  const resume = useCallback(
    (id: number) => {
      const duration = durations.current.get(id);
      if (!duration || timers.current.has(id)) return;
      timers.current.set(id, window.setTimeout(() => dismiss(id), duration));
    },
    [dismiss],
  );

  const show = useCallback(
    (message: ReactNode, options: ToastOptions = {}) => {
      const id = nextId.current;
      nextId.current += 1;
      setToasts((current) => {
        const next = [...current, { id, message, ...options }];
        for (const dropped of next.slice(0, Math.max(0, next.length - MAX_TOASTS))) {
          const timer = timers.current.get(dropped.id);
          if (timer !== undefined) window.clearTimeout(timer);
          timers.current.delete(dropped.id);
        }
        return next.slice(-MAX_TOASTS);
      });
      const duration = options.duration ?? TOAST_DURATION_MS;
      if (duration > 0) {
        durations.current.set(id, duration);
        timers.current.set(id, window.setTimeout(() => dismiss(id), duration));
      }
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending.values()) window.clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const api = useMemo<ToastApi>(
    () => ({
      show,
      success: (message, options) => show(message, { ...options, tone: 'ok' }),
      error: (message, options) => show(message, { ...options, tone: 'err' }),
      dismiss,
    }),
    [show, dismiss],
  );

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/* Always in the DOM so screen readers announce what is added to it. */}
      <div className={styles.region} role="region" aria-label="Avisos" aria-live="polite" aria-relevant="additions">
        {toasts.map((toast) => {
          const tone = toast.tone ?? 'ok';
          return (
            <div
              key={toast.id}
              className={styles.toast}
              role={tone === 'err' ? 'alert' : 'status'}
              {...(toast.action
                ? {
                    onMouseEnter: () => pause(toast.id),
                    onMouseLeave: () => resume(toast.id),
                    onFocus: () => pause(toast.id),
                    onBlur: () => resume(toast.id),
                  }
                : {})}
            >
              <span className={cx(styles.icon, styles[tone])}>
                <Icon name={TONE_ICON[tone]} />
              </span>
              <span className={styles.msg}>{toast.message}</span>
              {toast.action ? (
                <Button
                  size="sm"
                  variant={tone === 'err' ? 'secondary' : 'ghost'}
                  href={toast.action.href}
                  onClick={() => {
                    toast.action?.onClick?.();
                    dismiss(toast.id);
                  }}
                >
                  {toast.action.label}
                </Button>
              ) : null}
              <IconButton icon="close" size="sm" label="Cerrar aviso" onClick={() => dismiss(toast.id)} />
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

/** Toasts: bottom right, 5 s, two at most. Only for confirmations and one-off errors. */
export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error('useToast necesita <ToastProvider> (ya está en el layout raíz).');
  return api;
}
