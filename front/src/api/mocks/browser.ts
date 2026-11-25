import { setupWorker } from 'msw/browser';
import { API_URL } from '../config';
import { handlers } from './handlers';
import { loadDb, syncSession } from './state';

let started: Promise<void> | null = null;

/**
 * MSW tells its worker "CLIENT_CLOSED" when the page unloads, and the worker
 * unregisters itself when no page is left. On a reload that races with the page
 * that is loading: it finds a registration that is on its way out, MSW's
 * `registration.update()` rejects ("Failed to update a ServiceWorker… Not
 * found") and the requests made meanwhile go to the network unmocked. The
 * worker is kept instead: it only answers pages that turned the mocks on.
 */
function keepWorkerRegistered(): void {
  const prototype = window.ServiceWorker?.prototype as (ServiceWorker & { __ocKeep?: boolean }) | undefined;
  if (!prototype || prototype.__ocKeep) return;
  const original = prototype.postMessage;
  prototype.postMessage = function postMessage(this: ServiceWorker, message: unknown, ...rest: unknown[]) {
    if (message === 'CLIENT_CLOSED') return;
    return (original as (...args: unknown[]) => void).call(this, message, ...rest);
  } as ServiceWorker['postMessage'];
  prototype.__ocKeep = true;
}

const START_ATTEMPTS = 3;

/**
 * Starts the MSW service worker (once) and the scenario's mocked session.
 * Requests to anything that is not the API pass through untouched; an API path
 * without a handler is reported, because it means the contract and the mocks drifted.
 */
export function startMocks(): Promise<void> {
  if (started) return started;
  keepWorkerRegistered();
  const worker = setupWorker(...handlers);
  syncSession(loadDb());
  const start = async (): Promise<void> => {
    for (let attempt = 1; ; attempt += 1) {
      try {
        await worker.start({
          serviceWorker: { url: '/mockServiceWorker.js' },
          quiet: true,
          onUnhandledRequest(request, print) {
            if (request.url.startsWith(`${API_URL}/api/`)) print.error();
          },
        });
        return;
      } catch (error) {
        // A registration that failed (the browser was busy replacing the worker) is tried again.
        if (attempt >= START_ATTEMPTS) throw error;
        await new Promise((resolve) => window.setTimeout(resolve, 300 * attempt));
      }
    }
  };
  started = start().catch((error: unknown) => {
    started = null;
    throw error;
  });
  return started;
}
