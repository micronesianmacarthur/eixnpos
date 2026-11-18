# Research Report: Guarded Menu Redirect to Dashboard (start-session modal)

**Task:** t_658341d5 — Investigate why the route guard redirects to the dashboard before the start-session modal can open.
**Scope:** Frontend routing (Sales menu → /sales) + backend session check.
**Workspace:** worktree `@ /home/mac/Projects/pylab/eixnpos/.worktrees/t_658341d5` (branch `integration/pos-v1`).
**Date:** 2026-11-16

---

## 1. Decision (single sentence)

The route guard results in the "start your shift" starting-cash modal never opening because its initial-mount check, when `GET /api/sessions/current/` reports `{ open: false }` (no open session for this cashier), calls `navigate('/', { replace: true })` — a synchronous replace navigation that unmounts the `/sales` route tree (SalesPage + the StartSessionModal) before they render.

## 2. Navigation flow (end-to-end)

1. **Sidebar** (`frontend/src/Sidebar.tsx`) renders `<NavLink to="/sales">` for the Sales menu item.
2. **AppRouter** (`frontend/src/AppRouter.tsx`, `createBrowserRouter`) routes `/sales` under a `RequireOpenSession` guard:
   ```tsx
   <Route element={<RequireOpenSession />}>
     <Route path="sales" element={<SalesPage />} />
   </Route>
   ```
3. **RequireOpenSession** (`frontend/src/RequireOpenSession.tsx`) mounts as the parent route element. On mount, `useEffect(..., [me, loading])` fires `revalidate()`.
4. `revalidate()` calls `fetchOpen()`, which `apiFetch('/api/sessions/current/')`.
5. Backend (`backend/sessions/views.py`) returns `{ open: false }` when the cashier has no open `Session` row (`status='open'`).
6. Guard: `if (!isOpen) navigate('/', { replace: true })` — synchronous replace navigation to `/`.
7. React Router unmounts the `/sales` route subtree (SalesPage + StartSessionModal) before it renders.
8. The cashier lands on the dashboard; the start-session modal never opens.

## 3. Root cause

The guard resolves "no open session" (`{ open: false }`) as an **instant terminal redirect** on the initial mount, instead of first rendering the start-session modal. The start-session modal lives inside the `/sales` route's `SalesPage` component. Because the guard performs a synchronous `replace: true` navigation to `/` on mount, the `/sales` route subtree is unmounted before the `SalesPage` (and its embedded `StartSessionModal`) can mount. The modal therefore never opens; the user is sent straight to the dashboard.

The guard conflates two states that must be handled differently:
- **Open session exists** → continue on the Sales page (resume/carry on the open sale).
- **No open session** → offer the starting-cash setup via the "Start your shift" modal, then redirect/not redirect based on whether the setup completed.

## 4. Guard logic involved

`frontend/src/RequireOpenSession.tsx`:

```tsx
// On mount (and whenever the signed-in user changes).
useEffect(() => {
  if (loading || !me) return;
  void revalidate();
}, [me, loading, revalidate]);

// Re-check the session and act on the result. Used both for the initial
// mount check and for every focus/visibility re-validation.
const revalidate = useCallback(async () => {
  const isOpen = await fetchOpen();
  setOpen(isOpen);
  if (!isOpen) {
    // The open shift is gone — send the cashier back to the dashboard and
    // unmount the Sales screen (this component's <Outlet />).
    navigate('/', { replace: true });
  }
}, [navigate]);

async function fetchOpen(): Promise<boolean> {
  try {
    const data = await apiFetch<CurrentSessionResponse>('/api/sessions/current/');
    return data.open === true;
  } catch {
    return true; // fail-open: a transient failure must never evict the user
  }
}
```

Key state conditions:
- `me` is set (authenticated) **and** `loading` is false (auth fully resolved) before the check runs.
- `open` state: `null` → `false` after the check.
- The redirect (`navigate('/', { replace: true })`) is a **replace** navigation, so the back button cannot re-enter `/sales`.

## 5. Affected files / components

| File | Role |
|------|------|
| `frontend/src/Sidebar.tsx` | Sales menu item → `<NavLink to="/sales">` |
| `frontend/src/AppRouter.tsx` | Routes `/sales` under `RequireOpenSession` |
| `frontend/src/RequireOpenSession.tsx` | The guard; `revalidate()` → `fetchOpen()` → `navigate('/')` |
| `frontend/src/SalesPage.tsx` | Sales page that hosts the start-session modal (via `StartSessionModal` / `<Outlet />`) |
| `frontend/src/StartSessionModal.tsx` | The "Start your shift" starting-cash modal |
| `backend/sessions/views.py` | `GET /api/sessions/current/` → `{ open: false }` when no open session |
| `frontend/tests/sales-navigation.spec.ts` | Regression tests for the modal flow (expect modal → Escape → redirect) |

## 6. Exact code path

1. `Sidebar.tsx`: user clicks `<NavLink to="/sales">`.
2. `AppRouter.tsx`: `/sales` matched under `<Route element={<RequireOpenSession />}>`.
3. `RequireOpenSession.tsx`:
   - `useEffect(...)` runs `revalidate()` on mount.
   - `revalidate()`: `isOpen = await fetchOpen()` → `GET /api/sessions/current/` → `{ open: false }` (no open Session for this cashier).
   - `setOpen(false)`; `if (!isOpen) navigate('/', { replace: true })`.
   - Browser navigates to `/`, unmounting the `/sales` route tree (SalesPage + StartSessionModal).
4. `SalesPage.tsx` and `StartSessionModal.tsx` are never mounted → modal never opens.

## 7. Historical context & state conditions

- The guard's open-session redirect was **fixed** in commit `1ea0719` ("open /sales when a session is open; redirect to dashboard only when starting cash is incomplete"). That fix made an open session render the Sales screen and restricted the dashboard redirect to the case where the cashier dismisses the start-shift modal without completing the starting-cash set-up.
- The current code (`1ea0719` + `ab94ed5` + `c9610c1` + `ad31d5c` merged) retains the redirect-to-dashboard for the **no-session** case on mount, which blocks the start-session modal from rendering. This is the behavior the task asks about.
- **State conditions for the redirect to fire:**
  1. User is authenticated (`me` set) and auth is fully resolved (`loading` false).
  2. Backend DB has no open `Session` for this cashier (`status='open'`).
  3. `GET /api/sessions/current/` returns `{ open: false }`.
  4. Guard's `open` state transitions `null` → `false` after the check.
  5. Guard re-validates on `visibilitychange`/`focus` when the tab is backgrounded; if the session was closed elsewhere, the same redirect fires on return.

## 8. Recommendation (for the implementer)

The guard should **not** redirect on the initial mount when there is no open session. Instead, it should render the `StartSessionModal` (starting-cash set-up) on the `/sales` route; only after the cashier dismisses the modal without completing the set-up (backdrop/Escape/close) should it redirect to `/`. Serve the modal with non-replace navigation so the user can return to `/sales` to retry, and keep the existing fail-open behavior for transient API failures.

---

*Grades of confidence: the code path is read directly from `frontend/src/RequireOpenSession.tsx` and `frontend/src/AppRouter.tsx`; the API contract is from `backend/sessions/views.py` (`GET /api/sessions/current/` → `{ open: bool }`). This report is a research artifact, not a code change.*
