import { apiFetch } from './api';

/**
 * Response from `GET /api/sessions/current/`.
 *
 * The backend resolves an omitted `register` to the caller's lowest-pk
 * accessible register and an omitted `day` to the business-local date of the
 * request instant. `register`/`day` echo back the scope the session actually
 * resolved to, so a caller can confirm which one it operated on.
 */
export interface CurrentSession {
  open: boolean;
  sessionId?: string;
  startedAt?: string;
  startingCash?: string;
  register?: number;
  day?: string;
}

/**
 * Resolve the current register/day open session.
 *
 * v1 ships a single default register: there is no register/day selector in
 * the UI, so every frontend session entry point must resolve the SAME
 * register/day for a given context. This one function is that shared resolver
 * — the route guard (`RequireOpenSession`), the start-shift flow, and the
 * Close page all call it (directly or via `GET /api/sessions/current/`) and
 * never scan the session list for "an open one", which could land on a
 * sibling register's shift. If a register picker is added later, extend this
 * to forward explicit `register`/`day` instead of introducing a second
 * implicit default.
 */
export async function currentSession(): Promise<CurrentSession> {
  return apiFetch<CurrentSession>('/api/sessions/current/');
}
