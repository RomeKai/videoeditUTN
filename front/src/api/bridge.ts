import { hasBackendDefaultPrefix } from './config';
import { getMe } from './contracts/users';
import { isHybrid, routeOf } from './routing';
import { getActiveWorkspaceId, setActiveWorkspaceId } from './session';

/**
 * DEV BRIDGE (hybrid mode only): `/me` is mocked, so the session shows the placeholder workspace instead
 * of the user's real ones (`src/api/mocks/hybrid.ts`). False in mock and real mode, and in hybrid once
 * `/me` is routed to the backend.
 */
export function isMeBridged(): boolean {
  return isHybrid() && routeOf(getMe) === 'mock';
}

/**
 * A placeholder workspace left in the browser by an earlier hybrid session, when the bridge is no longer
 * in use (the build is not hybrid, or `/me` became real): forgotten before anything is asked, so the
 * session picks a real workspace from `/me` and no scoped request goes out with an id nobody knows.
 */
export function dropStaleBridgeWorkspace(): void {
  if (!hasBackendDefaultPrefix(getActiveWorkspaceId()) || isMeBridged()) return;
  setActiveWorkspaceId(null);
}
