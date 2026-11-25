'use client';

import { useEffect } from 'react';
import { Button } from '@/ui/Button';
import { SystemScreen } from '@/shell/SystemPage';

/** K3 · Error del servidor: an unexpected error while rendering a route. */
export default function RouteError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);
  return (
    <SystemScreen
      title="Algo falló de nuestro lado"
      code="500"
      description="Ya nos enteramos. Probá de nuevo en un momento."
      actions={
        <>
          <Button variant="primary" iconLeft="retry" onClick={reset}>
            Reintentar
          </Button>
          <Button variant="ghost" href="/proyectos">
            Ir a Proyectos
          </Button>
        </>
      }
    />
  );
}
