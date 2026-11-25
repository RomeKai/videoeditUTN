'use client';

import { useSearchParams } from 'next/navigation';
import { Suspense } from 'react';
import type { Platform } from '@/api/contracts/common';
import { completeMockSocialConnect } from '@/api/mocks/control';
import { getActiveWorkspaceId } from '@/api/session';
import { AuthCard } from '@/shell/PublicShell';
import { Banner, Button, NETWORKS, NetworkChip } from '@/ui';

function Connect() {
  const search = useSearchParams();
  const raw = search.get('red');
  const platform = raw && raw in NETWORKS ? (raw as Platform) : null;

  const finish = (authorize: boolean) => {
    if (authorize && platform) completeMockSocialConnect(platform, getActiveWorkspaceId());
    // Opened as a popup by the connect dialog; as a normal tab, go back to Ajustes › Redes.
    if (window.opener) window.close();
    else window.location.assign('/ajustes/redes');
  };

  return (
    <div style={{ minHeight: '100dvh', display: 'grid', placeItems: 'center', padding: 16 }}>
      <AuthCard title="Autorización de prueba" description="Esta ventana reemplaza a la de la red social mientras la API está simulada.">
        {platform ? (
          <>
            <NetworkChip platform={platform} />
            <p className="t-small muted">OneCreator quiere publicar videos en tu nombre y leer el estado de lo que publica.</p>
            <Button variant="primary" block onClick={() => finish(true)}>
              Autorizar
            </Button>
            <Button variant="ghost" block onClick={() => finish(false)}>
              Cancelar
            </Button>
          </>
        ) : (
          <Banner tone="warn" title="Falta indicar la red" />
        )}
      </AuthCard>
    </div>
  );
}

/** Mock-only: stands in for the network's authorization window (see CONTRACT.md · social.connect). */
export default function MockConnectPage() {
  return (
    <Suspense>
      <Connect />
    </Suspense>
  );
}
