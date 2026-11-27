'use client';

import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import type { Platform } from '@/api/contracts/common';
import { useConnectSocialAccount, usePlans, useSocialAccounts } from '@/api/hooks';
import { ApiError } from '@/api/errors';
import { keys } from '@/api/query';
import { errorMessage } from '@/lib/error-messages';
import { firstPlanWith } from '@/lib/pricing';
import { usePlan, useSession } from '@/shell/session';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { PlanLock } from '@/ui/DataDisplay';
import { Dialog } from '@/ui/Dialog';
import { Icon } from '@/ui/Icon';
import { NETWORKS, NetworkChip } from '@/ui/NetworkChip';
import styles from './Dialogs.module.css';

/** How often the accounts are asked again after the network's window closed. */
const RECHECK_MS = 5000;

/** The backend's own sentence for the refusals it explains (plan, email, the profile cap), else the screen's. */
function connectError(error: unknown): string {
  if (error instanceof ApiError && error.status < 500 && ['social_accounts', 'email_not_verified', 'social_profiles_limit', 'profile_title_taken'].includes(error.code ?? '')) {
    return error.message;
  }
  return errorMessage(error, 'No pudimos abrir la conexión.');
}

export interface ConnectNetworkDialogProps {
  /** Network to connect; null keeps the dialog closed. */
  platform: Platform | null;
  onClose: () => void;
}

/**
 * D6 · Conectar red social. Step 1 explains what the network will ask for and
 * opens its authorization window; step 2, on return, shows the connected account.
 * Used from Publicar (E1) and Ajustes › Redes sociales (H3).
 */
export function ConnectNetworkDialog({ platform, onClose }: ConnectNetworkDialogProps) {
  const { workspace } = useSession();
  const connect = useConnectSocialAccount();
  const accounts = useSocialAccounts();
  const planInfo = usePlan();
  const plans = usePlans();
  const queryClient = useQueryClient();
  const [waiting, setWaiting] = useState(false);
  const popup = useRef<Window | null>(null);
  const account = accounts.data?.find((item) => item.platform === platform);
  const connected = waiting && account?.status === 'connected';

  // While the network's window is open, check the accounts when it closes or the tab regains focus. Once it is
  // closed, ask again every few seconds until the account shows up (then stop): the backend reads the provider at
  // most every 15 s, and the provider may take a moment to list a new account.
  useEffect(() => {
    if (!waiting || connected) return;
    let asked = 0;
    const refresh = () => {
      asked = Date.now();
      void queryClient.invalidateQueries({ queryKey: keys.social.accounts });
    };
    const timer = window.setInterval(() => {
      if (popup.current?.closed && Date.now() - asked >= RECHECK_MS) refresh();
    }, 1000);
    window.addEventListener('focus', refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('focus', refresh);
    };
  }, [queryClient, waiting, connected]);

  const close = () => {
    // Cancelling also closes the network's window: nothing is left half way.
    if (!connected && popup.current && !popup.current.closed) popup.current.close();
    popup.current = null;
    setWaiting(false);
    connect.reset();
    onClose();
  };

  if (!platform) return null;
  const network = NETWORKS[platform];

  // Connecting is for the paid plans (`allow_social_accounts`): the lock and the plan that includes it, as elsewhere.
  // Only once the plan is known; without it, the backend decides (its 403 says why).
  if (planInfo.plan && !planInfo.allows('social')) {
    return (
      <Dialog
        open
        onClose={close}
        title={`Conectar ${network.shortName}`}
        description={workspace ? `Para el workspace ${workspace.name}` : undefined}
        footer={
          <Button variant="ghost" onClick={close} data-autofocus>
            Cerrar
          </Button>
        }
      >
        <p className="t-small">Tu plan no incluye conectar redes. Lo que ya está conectado sigue publicando.</p>
        <PlanLock plan={firstPlanWith(plans.data ?? [], (plan) => plan.allow_social_accounts === true)?.name ?? 'Creador'} />
      </Dialog>
    );
  }

  if (connected) {
    return (
      <Dialog
        open
        onClose={close}
        title={`${network.shortName} quedó conectada`}
        description="Al volver de la red"
        footer={
          <Button variant="primary" onClick={close} data-autofocus>
            Listo
          </Button>
        }
      >
        <div className={styles.who}>
          <NetworkChip platform={platform} hideName />
          <div className={styles.whoMain}>
            <p className={styles.strong}>{account?.username}</p>
            <p className="t-small muted">{network.name}</p>
          </div>
          <Badge tone="ok" dot>
            Conectada
          </Badge>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog
      open
      onClose={close}
      title={`Conectar ${network.shortName}`}
      description={workspace ? `Para el workspace ${workspace.name}` : undefined}
      footer={
        <>
          <Button variant="ghost" onClick={close}>
            Cancelar
          </Button>
          <Button
            variant="primary"
            iconRight="external"
            loading={connect.isPending}
            data-autofocus
            onClick={() =>
              // No `return_url`: the provider's «Close» then closes this window, and the check above sees it.
              connect.mutate(
                { platform },
                {
                  onSuccess: ({ connect_url }) => {
                    popup.current = window.open(connect_url, 'onecreator-conectar', 'width=520,height=680');
                    setWaiting(true);
                  },
                },
              )
            }
          >
            {waiting ? `Abrir ${network.shortName} de nuevo` : `Continuar en ${network.shortName}`}
          </Button>
        </>
      }
    >
      <div>
        <p className="t-label muted" style={{ marginBottom: 8 }}>
          {network.shortName} te va a pedir permiso para
        </p>
        <ul className={styles.perm}>
          <li>
            <Icon name="check" />
            Ver el nombre y la foto de tu cuenta
          </li>
          <li>
            <Icon name="check" />
            Publicar videos en tu nombre
          </li>
          <li>
            <Icon name="check" />
            Leer el estado de lo que publicamos
          </li>
        </ul>
      </div>
      <p className="t-small subtle">
        {waiting
          ? `Terminá de autorizar en la ventana de ${network.shortName}. Esta pantalla se actualiza sola.`
          : `Se abre una ventana de ${network.shortName} para que autorices. No vemos ni guardamos tu contraseña.`}
      </p>
      {connect.isError ? (
        <p className="t-small" style={{ color: 'var(--err)' }} role="alert">
          {connectError(connect.error)}
        </p>
      ) : null}
    </Dialog>
  );
}
