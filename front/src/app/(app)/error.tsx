'use client';

import { useEffect } from 'react';
import { ServerErrorMessage } from '@/shell/SystemPage';

/** K3 inside the app shell: the top bar stays, the page offers to retry. */
export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return <ServerErrorMessage inline onRetry={reset} />;
}
