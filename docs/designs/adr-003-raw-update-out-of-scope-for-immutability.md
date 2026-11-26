# ADR-003 — Raw `QuerySet.update()` / SQL is out of scope for app-layer sale immutability

**Date:** 2026-11-26
**Status:** Accepted (recorded decision)
**Decision-maker:** coder (sales concurrency re-review t_10f1424a)
**Owners:** `backend/sales/models.py` (`Sale`, `SaleQuerySet`)

---

## Decision (one sentence)

Sale immutability (`completed`/`voided`/`refunded` rows are protected against
identity, audit, status and hard-delete rewrites) is enforced **only at the
Django model/ORM layer** (`Sale.save`, `transition_to`, `Sale.delete`,
`SaleQuerySet.delete`, admin read-only fields). A raw Django
`QuerySet.update()` (e.g. `Sale.objects.filter(pk=…).update(status='open', …)`)
or hand-written SQL is **deliberately out of scope** for the app-layer guard.

---

## Context

A re-review probe showed a raw write bypasses every model guard:

```python
Sale.objects.filter(pk=sale.pk).update(status='open', customer=<other>)  # succeeds on a completed sale
sale.delete()  # the completed row (now relabeled 'open') is then erased
```

`QuerySet.update()` and raw SQL run entirely below Django's field-level
`save()`/manager hooks, so the `_guard_immutable_save` / `transition_to`
whitelist never execute. No production caller does this today — all writes go
through `save()`, the sanctioned transitions, or the admin — but the earlier
handoff's claim of having "closed every ORM bypass" is only true at the
save/queryset-delete layer, not for raw `update()`.

We considered defeating it with a database-level guard (a trigger/check that
forbids `status` reverting off-immutable, or that refuses writes to protected
columns once `status` is immutable). That was rejected:

- A column-level CHECK cannot see the *row's previous* `status`, so it cannot
  distinguish a legitimate `open -> completed` flip from an illegitimate
  `completed -> open` revert — status transitions need row-level temporal
  reasoning only a trigger or locked application transaction can express.
- A trigger is a large, migration-heavy surface for a path no caller exercises,
  and it would run in the test DB too, adding risk to every write.
- The genuinely reachable races (concurrent completion vs void/complete/delete)
  are already closed by `select_for_update` re-checks under `transaction.atomic`
  in the service layer and model `delete()`; raw `update()` is not concurrency
  so much as deliberate SQL escaping.

The boundary is therefore an explicit, documented scope decision rather than a
silent gap.

## Consequence

The documented invariant is: **immutability is an application-layer contract**,
enforced at every ORM write/delete seam that production and the admin use.
Anyone writing raw `QuerySet.update()` or SQL against `sales_sale` must
themselves respect the status whitelist and audit fields; the model layer will
not stop them. This ADR exists so a future engineer does not rediscover the
bypass and assume it is an oversight.
