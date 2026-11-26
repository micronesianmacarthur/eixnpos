"""Authorization policy for invoicing a customer on account.

Single source of truth for *who may book an invoice charge* against a
registered customer. Historically this required a manager/admin approval
(PIN) on every invoice; the v1 owner decision removes that gate so a Cashier
can here-and-now invoice a **registered** customer without approval, with the
``credit_limit`` ceiling (enforced by ``LedgerService``) remaining the only
credit control.

Everything else stays restricted: walk-in (unregistered) customers cannot be
invoiced at all, and discount / refund / void / credit-override actions
remain gated by their existing manager/admin rules. This module grants only
the single invoicing action described above.
"""

#: Roles entitled to invoice a registered customer on account. This is the
#: policy grant; ``credit_limit``/``LedgerService`` remains the ceiling.
INVOICE_ROLES = frozenset({'cashier', 'manager', 'admin'})


def can_invoice_on_account(user, customer) -> bool:
    """Return True if *user* may invoice *customer* on account, by policy.

    A user may invoice a registered customer when they are authenticated,
    active and hold a role in ``INVOICE_ROLES`` (or are a superuser, which
    bypasses role). ``customer`` must be a real ``Customer`` instance — walk-in
    / unregistered (``None``) is never allowed, so the invoice requires a
    customer row selected by the caller.

    This is purely the *authorization* predicate. The addressable credit
    beyond the customer's ``credit_limit`` is a separate, hard guard enforced
    in ``LedgerService`` (the ceiling stays the only credit control).
    """
    if not user or not getattr(user, 'is_authenticated', False):
        return False
    if not getattr(user, 'is_active', True):
        return False
    if customer is None:  # walk-in / unregistered
        return False
    if getattr(user, 'is_superuser', False):
        return True
    return getattr(user, 'role', '') in INVOICE_ROLES
