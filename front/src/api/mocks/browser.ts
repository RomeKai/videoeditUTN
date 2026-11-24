import { setupWorker } from 'msw/browser';
import { API_URL } from '../config';
import { handlers } from './handlers';
import { loadDb, syncSession } from './state';

let started: Promise<void> | null = null;

/**
 * Starts the MSW service worker (once) and the scenario's mocked session.
 * Requests to anything that is not the API pass through untouched; an API path
 * without a handler is reported, because it means the contract and the mocks drifted.
 */
export function startMocks(): Promise<void> {
  if (started) return started;
  const worker = setupWorker(...handlers);
  syncSession(loadDb());
  started = worker
    .start({
      serviceWorker: { url: '/mockServiceWorker.js' },
      quiet: true,
      onUnhandledRequest(request, print) {
        if (request.url.startsWith(`${API_URL}/api/`)) print.error();
      },
    })
    .then(() => undefined);
  return started;
}
