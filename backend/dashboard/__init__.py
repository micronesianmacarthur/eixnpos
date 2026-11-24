"""eixnPOS manager dashboard — server-side aggregation service.

Exposes the metrics defined in the dashboard metrics & data contract
(``research/dashboard-metrics-and-data-contract.md``) as stable endpoints under
``/api/dashboard/``. Aggregation happens server-side, in the business-local
timezone, against the existing sales / inventory / session models. No demo or
hard-coded data.
"""
