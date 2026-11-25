import { setupServer } from 'msw/node';
import { handlers } from './handlers';

/** MSW for Vitest: the same handlers as the browser, answering in Node. */
export const server = setupServer(...handlers);
