import { setupWorker } from 'msw/browser';
import { API_URL } from '../config';
import { handlers } from './handlers';
import { dropStaleBridgeWorkspace } from '../bridge';
import { API_REAL_LIST } from '../config';
import { isHybrid } from '../routing';
import { unknownRealNames } from '../routingTable';
import { watchRealAnswers } from './hybrid';
import { dropForeignSession, loadDb, syncSession } from './state';

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
 * In hybrid mode each handler decides, per request, whether it answers or lets the
 * request through to the real backend (`route()` in ./http, `src/api/routing.ts`).
 */
export function startMocks(): Promise<void> {
  if (started) return started;
  keepWorkerRegistered();
  const worker = setupWorker(...handlers);
  dropForeignSession();
  // `/me` became real (or the build is no longer hybrid): the placeholder workspace of the bridge is forgotten.
  dropStaleBridgeWorkspace();
  if (isHybrid()) {
    const unknown = unknownRealNames(API_REAL_LIST);
    // A typo would silently leave that domain mocked.
    if (unknown.length > 0) console.warn(`[híbrido] NEXT_PUBLIC_API_REAL nombra algo que no está en el contrato: ${unknown.join(', ')}. Se ignora.`);
  }
  // Hybrid: the session comes from a real login, never from the scenario.
  if (!isHybrid()) syncSession(loadDb());
  // …and the answers of the backend tell the mocks what its rows look like (the worker hands each one over).
  else watchRealAnswers(worker.events);
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
