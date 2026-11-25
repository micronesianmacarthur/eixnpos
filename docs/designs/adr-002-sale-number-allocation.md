# ADR-002 — Sale number allocation stays at sale creation (not completion)

**Date:** 2026-11-26
**Status:** Accepted (recorded decision)
**Decision-maker:** coder (sale-hardening follow-up t_4e7e5457)
**Owners:** `backend/sales/models.py` (`Sale.save`, `SaleNumberSequence`), `backend/sales/migrations/0006`

---

## Decision (one sentence)

A sale's human-facing `number` is allocated **once at cart creation** (on first
`Sale.save()`), from a monotonic, never-reused `SaleNumberSequence` high-water
mark — it is **not** deferred to completion, and it is documented here as a
deliberate, tested invariant.

---

## Context

The root sale-hardening spec asked to allocate the sale number "at completion
instead of burning it at cart creation". A review probe confirmed allocation
still happens on the first `Sale.save()` (an open sale already has
`number=1`).

The uniqueness/no-reuse behaviour is verified correct on any allocation point:
the `SaleNumberSequence` is a single seeded row replenished to `max(sale.number)`
by the 0006 data migration, `allocate_next()` increments under a row lock with a
bounded retry on `IntegrityError`, and `Sale.number` has a DB `unique`
constraint. Deletion never frees a number (`TestSaleNumberNeverReused`).

Moving allocation to completion was rejected because the number is load-bearing
**before** the sale is complete:

- `SaleSerializer` exposes `number` on every open cart the POS renders; deferring
  it would leave open drafts without a displayable number.
- `complete_sale` / `finalize_sale` build the `StockMovement.reference` as
  `sale:{number}` at completion; the reference is written from the already-set
  number.
- The immutability model (parent blocker t_4db3354b) treats `number` as an
  `INVIOLATE_FIELD`; allocating at creation is what makes that invariant hold for
  every draft before it leaves open/pending.
- The 0006 seed migration computes `max(existing number)` and must already run
  before any new allocation; the sequence guarantees the next number is
  strictly above all history regardless of when a row is (or was) deleted.

## Consequence

`Sale.save()` allocates a fresh, unique number on first insert (when
`self.number` is falsy). Allocations are monotonic and never reused; a deletion
never lowers the high-water mark. This is explicitly the production behaviour
and is covered by `TestSaleNumberNeverReused` and the 0006 sequence-seed test.

## Alternatives considered

| Alternative | Verdict | Why |
|---|---|---|
| Defer allocation to completion | Rejected | Leaves open carts with NULL number, breaking serializer output, POS display, and the immutability guarantee; high cost, no correctness gain since the sequence already guarantees uniqueness/no-reuse. |
| Per-line/fuzzy number | Rejected | Loses the single monotonic invariant and the deletion-proof guarantee. |
