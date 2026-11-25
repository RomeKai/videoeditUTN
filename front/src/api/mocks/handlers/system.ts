import * as contract from '../../contracts/system';
import { route } from '../http';

export const systemHandlers = [
  route(contract.getSystemStatus, ({ db, now }) => ({
    data: {
      maintenance: db.flags.maintenance,
      maintenance_window: db.flags.maintenance
        ? { starts_at: new Date(now - 20 * 60_000).toISOString(), ends_at: new Date(now + 70 * 60_000).toISOString() }
        : null,
    },
  })),
];
