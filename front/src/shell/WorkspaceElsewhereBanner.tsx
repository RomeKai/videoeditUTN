'use client';

import { useDeferredWorkspaceId } from '@/api/hooks';
import { FixedBanner } from '@/ui/Banner';
import { useSession } from './session';
import styles from './System.module.css';

/**
 * The person switched workspace in another tab while this one had unsaved changes. This tab stayed
 * on its workspace (following would have reloaded the screen in the other one and lost the changes)
 * and offers to go there. The switch asks before leaving, like any other, if the changes are still unsaved.
 */
export function WorkspaceElsewhereBanner() {
  const deferredId = useDeferredWorkspaceId();
  const { me, workspace, switchWorkspace } = useSession();
  if (!deferredId || !workspace || deferredId === workspace.id) return null;
  const other = me?.workspaces.find((item) => item.id === deferredId);
  // Not one of this user's (another account signed in over there): nothing to offer.
  if (!other) return null;
  return (
    <div className={styles.offline}>
      <FixedBanner icon="info" tone="info">
        Cambiaste a {other.name} en otra pestaña. Esta sigue en {workspace.name} para no perder tus cambios.{' '}
        <button type="button" className="lnk" onClick={() => void switchWorkspace(other.id)}>
          Cambiar a {other.name}
        </button>
      </FixedBanner>
    </div>
  );
}
