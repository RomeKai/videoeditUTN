'use client';

import { useId, useState } from 'react';
import type { Platform } from '@/api/contracts/common';
import type { SocialAccount } from '@/api/contracts/social';
import { useDisconnectSocialAccount, usePlans, useSocialAccounts } from '@/api/hooks';
import { ConfirmDialog, ConnectNetworkDialog } from '@/dialogs';
import { cx } from '@/lib/cx';
import { errorMessage } from '@/lib/error-messages';
import { LoadError } from '@/shell/LoadError';
import { RequireRole } from '@/shell/RequireSession';
import { firstPlanWith } from '@/lib/pricing';
import { usePlan, useSession } from '@/shell/session';
import { Badge, Button, NETWORKS, NetworkChip, PlanLock, Skeleton, useToast } from '@/ui';
import styles from './ajustes.module.css';

/** Where each network publishes, when its name does not say it. */
const SURFACE: Record<Platform, string | null> = { TIKTOK: null, INSTAGRAM_REELS: 'Reels', YOUTUBE_SHORTS: 'Shorts' };

function describe(account: SocialAccount): string {
  const surface = SURFACE[account.platform];
  const name = NETWORKS[account.platform].shortName;
  if (account.status === 'connected') return [account.username, surface ? `publica en ${surface}` : null].filter(Boolean).join(' · ');
  if (account.status === 'expired') return `${account.username ? `${account.username} · ` : ''}no se puede publicar en ${surface ?? name} hasta reconectar.`;
  return 'Conectá una cuenta para publicar acá.';
}

function Networks() {
  const toast = useToast();
  const titleId = useId();
  const { workspace } = useSession();
  const accounts = useSocialAccounts();
  const disconnect = useDisconnectSocialAccount();
  const planInfo = usePlan();
  const plans = usePlans();
  // Connecting is for the paid plans (`allow_social_accounts`); what is connected stays and can be disconnected.
  // Locked only once the plan is known: without it, the backend decides (and D6 shows its answer).
  const mayConnect = !planInfo.plan || planInfo.allows('social');
  const planWithIt = firstPlanWith(plans.data ?? [], (plan) => plan.allow_social_accounts)?.name ?? 'Creador';
  const [connecting, setConnecting] = useState<Platform | null>(null);
  const [disconnecting, setDisconnecting] = useState<Platform | null>(null);
  const target = disconnecting ? NETWORKS[disconnecting].shortName : '';

  const confirmDisconnect = () => {
    if (!disconnecting) return;
    disconnect.mutate(disconnecting, {
      onSuccess: () => {
        setDisconnecting(null);
        toast.success(`Desconectaste ${target}`);
      },
    });
  };

  return (
    <section className={styles.group} aria-labelledby={titleId}>
      <div className={styles.head}>
        <div className={styles.headText}>
          <h2 className="t-h3" id={titleId}>
            Redes sociales
          </h2>
          <p className="t-small muted">
            Las cuentas donde {workspace?.name ?? 'tu workspace'} publica los clips. Las conectan el dueño y los admins.
          </p>
        </div>
      </div>
      {accounts.isError ? <LoadError error={accounts.error} onRetry={() => void accounts.refetch()} className={styles.mem} /> : null}
      {accounts.data ? (
        accounts.data.map((account) => {
          const name = NETWORKS[account.platform].shortName;
          return (
            <div key={account.platform} className={styles.row}>
              <div className={styles.netId}>
                <NetworkChip platform={account.platform} hideName />
                <div className={styles.label}>
                  <span>{name}</span>
                  <span className={styles.desc}>{describe(account)}</span>
                </div>
              </div>
              <div className={styles.netState}>
                {account.status === 'connected' ? (
                  <Badge tone="ok" dot className={styles.netBadge}>
                    Conectada
                  </Badge>
                ) : account.status === 'expired' ? (
                  <Badge tone="warn" dot className={styles.netBadge}>
                    Vencida
                  </Badge>
                ) : (
                  <Badge dot className={styles.netBadge}>
                    Sin conectar
                  </Badge>
                )}
                {account.status !== 'connected' && !mayConnect ? <PlanLock plan={planWithIt} /> : null}
                {account.status === 'disconnected' && mayConnect ? (
                  <Button variant="secondary" size="sm" aria-label={`Conectar ${name}`} onClick={() => setConnecting(account.platform)}>
                    Conectar
                  </Button>
                ) : null}
                {account.status === 'expired' && mayConnect ? (
                  <Button variant="secondary" size="sm" iconLeft="retry" aria-label={`Reconectar ${name}`} onClick={() => setConnecting(account.platform)}>
                    Reconectar
                  </Button>
                ) : null}
                {account.status !== 'disconnected' ? (
                  <Button variant="ghost" size="sm" aria-label={`Desconectar ${name}`} onClick={() => setDisconnecting(account.platform)}>
                    Desconectar
                  </Button>
                ) : null}
              </div>
            </div>
          );
        })
      ) : accounts.isError ? null : (
        <div aria-busy="true">
          <span className="sr-only">Cargando las redes</span>
          {[0, 1, 2].map((item) => (
            <div key={item} className={styles.row}>
              <div className={styles.netId}>
                <Skeleton width={32} height={32} radius={6} />
                <div className={cx(styles.label)}>
                  <Skeleton width={90} />
                  <Skeleton width={160} height={12} />
                </div>
              </div>
              <Skeleton width={150} height={24} pill />
            </div>
          ))}
        </div>
      )}
      <p className={styles.note}>Si desconectás una red, sus publicaciones programadas no salen.</p>

      <ConnectNetworkDialog platform={connecting} onClose={() => setConnecting(null)} />
      <ConfirmDialog
        open={Boolean(disconnecting)}
        onClose={() => {
          setDisconnecting(null);
          disconnect.reset();
        }}
        onConfirm={confirmDisconnect}
        loading={disconnect.isPending}
        tone="danger"
        title={`¿Desconectar ${target}?`}
        description={`Las publicaciones programadas en ${target} no van a salir. Podés volver a conectarla cuando quieras.`}
        confirmLabel="Desconectar"
        error={disconnect.isError ? errorMessage(disconnect.error, 'No pudimos desconectar la cuenta.') : undefined}
      />
    </section>
  );
}

/** H3 · Ajustes › Redes sociales. Only Dueño and Admin connect and disconnect (map 1.7). */
export function NetworksScreen() {
  return (
    <RequireRole action="social.manage">
      <Networks />
    </RequireRole>
  );
}
