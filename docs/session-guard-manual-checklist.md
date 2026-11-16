# Manual test checklist — session guard & Start Session modal

Automated coverage: `frontend/tests/session-guard.spec.ts` (7 tests).
Run against a clean backend (`GET /api/sessions/` empty unless noted).

## 1. No open session → modal, no navigation
- [ ] Log in with no open session. Click **Sales**: the "Start session" modal appears; the Sales screen never opens.
- [ ] Click **Sales Manager**: modal appears, Sales Manager screen does not open.
- [ ] Click **Close**: modal appears, Close screen does not open.
- [ ] While the modal is open, the URL is the gated route but the underlying transaction screen is not rendered (no way to add products / record sales).

## 2. Dismissing leaves the user blocked
- [ ] Open the modal via Sales, press **Escape**: modal closes, you return to the previous screen (Dashboard if direct), no session is created.
- [ ] Open the modal via Sales, click the backdrop outside the dialog: same result, no session created.
- [ ] Open the modal via Sales, click **Cancel**: same result.
- [ ] Open the modal via Sales, click the **×** header button: same result.
- [ ] After any dismissal, clicking Sales again re-opens the modal (still blocked) until a session starts.

## 3. Starting a session continues to the intended menu
- [ ] Click Sales, enter a valid amount (e.g. `25.00`), click **Start Session**: modal closes and the Sales screen opens.
- [ ] Verify POST `/api/sessions/` body is `{"starting_cash": "25.00"}` (one request only).
- [ ] From the dashboard, click **Sales Manager** (no session), start a session: you land on Sales Manager, not Sales.
- [ ] Same for **Close**.

## 4. Already-open session navigates directly
- [ ] With a session open (or after starting one), click Sales / Sales Manager / Close: each opens immediately, no modal, no extra POST to `/api/sessions/`.

## 5. Starting-cash validation
- [ ] Empty input: **Start Session** is disabled; pressing Enter does nothing.
- [ ] Negative value (e.g. `-5`): inline error "Starting cash cannot be negative.", field marked invalid, submit disabled, no request sent.
- [ ] Correct the value to a valid one: error clears, submit enables, session starts.

## 6. Legacy alert removed
- [ ] With no open session, there is no path that shows the old browser alert (product click / "start new shift"); the transaction screens are simply unreachable.

## Notes
- Backend remains authoritative: even if the guard fails open (API error), mutating endpoints must still reject sales without an open session.
- If `GET /api/sessions/` errors, the guard fails open (modal not forced) — verify sales still work and note it.
