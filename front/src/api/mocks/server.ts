import { setupServer } from 'msw/node';
import { handlers } from './handlers';
import { watchRealAnswers } from './hybrid';

/** MSW for Vitest: the same handlers as the browser, answering in Node. */
export const server = setupServer(...handlers);
// Hybrid mode: what the backend answers is what the stand-ins of its rows show (a no-op outside hybrid).
watchRealAnswers(server.events);
