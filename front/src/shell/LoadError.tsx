'use client';

import { ApiError } from '@/api/errors';
import { errorMessage } from '@/lib/error-messages';
import { Banner } from '@/ui/Banner';
import { Button } from '@/ui/Button';
import { NoAccessMessage, NotFoundMessage } from './SystemPage';

export interface LoadErrorProps {
  error: unknown;
  onRetry?: () => void;
  /** 404 and 403 take the whole content area (K1, K2) instead of a banner. Default true. */
  fullPageForMissing?: boolean;
  className?: string;
}

/**
 * Error of a query, the way map 1.8 asks: a banner above the content that says
 * "No pudimos conectar" with Reintentar; what was already on screen stays.
 * A 404 shows K1 and a role 403 shows K2.
 */
export function LoadError({ error, onRetry, fullPageForMissing = true, className }: LoadErrorProps) {
  if (!error) return null;
  if (fullPageForMissing && error instanceof ApiError) {
    if (error.status === 404) return <NotFoundMessage inline />;
    if (error.status === 403 && !error.isPlanLimit) return <NoAccessMessage inline />;
  }
  return (
    <Banner
      tone="err"
      className={className}
      title={errorMessage(error, 'No pudimos cargar esto')}
      action={
        onRetry ? (
          <Button size="sm" variant="secondary" iconLeft="retry" onClick={onRetry}>
            Reintentar
          </Button>
        ) : undefined
      }
    />
  );
}
