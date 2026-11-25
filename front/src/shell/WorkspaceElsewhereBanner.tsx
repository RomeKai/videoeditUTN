'use client';

import { useEffect, useRef } from 'react';
import { useDeferredWorkspaceId } from '@/api/hooks';
import { FixedBanner } from '@/ui/Banner';
import { useToast } from '@/ui/Toast';
import { useHasUnsavedChanges } from './leaveGuard';
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
  const unsaved = useHasUnsavedChanges();
  const toast = useToast();

  // The workspace this tab was staying on may be gone meanwhile (deleted, or the person was taken out
  // of it). The session then moves the tab to another one; what was unsaved cannot be saved any more,
  // and that is said, not left to a 404.
  const stayedOn = useRef<{ id: string; name: string } | null>(null);
  const workspaces = me?.workspaces;
  useEffect(() => {
    if (!workspace || !workspaces) return;
    const stayed = stayedOn.current;
    if (stayed && stayed.id !== workspace.id && !workspaces.some((item) => item.id === stayed.id)) {
      toast.show(`Ya no tenés acceso a ${stayed.name}: esta pestaña pasó a ${workspace.name}. Lo que no habías guardado no se puede guardar.`, { tone: 'warn' });
    }
    stayedOn.current = deferredId ? { id: workspace.id, name: workspace.name } : null;
  }, [deferredId, toast, workspace, workspaces]);

  if (!deferredId || !workspace || deferredId === workspace.id) return null;
  const other = workspaces?.find((item) => item.id === deferredId);
  // Not one of this user's (another account signed in over there): nothing to offer.
  if (!other) return null;
  return (
    <div className={styles.offline}>
      <FixedBanner icon="info" tone="info">
        Cambiaste a {other.name} en otra pestaña. Esta sigue en {workspace.name}
        {/* Once there is nothing unsaved the reason is gone: only where each tab is, and the way over. */}
        {unsaved ? ' para no perder tus cambios' : ''}.{' '}
        <button type="button" className="lnk" onClick={() => void switchWorkspace(other.id)}>
          Cambiar a {other.name}
        </button>
      </FixedBanner>
    </div>
  );
}
