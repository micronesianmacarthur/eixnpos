# Runbook: repack stock

For cashiers, managers, and admins working the **Inventory** screen. The setup,
permissions, API, and full limitations are in
[Inventory repack](../inventory-repack.md).

A repack converts whole units between a pack product and its unit product. The
app records the conversion in the stock ledger as you do it, so you do not need
a manager to approve it.

## Before you start

- Log in with an active account whose role is `cashier`, `manager`, or `admin`.
- Pack mapping is optional when a product is added: a standard product needs
  none. To repack, however, the mapping for the pair must already be configured.
  If the repack dialog says the product "is not configured as a pack or a pack
  child", stop and ask a manager to set it up — you cannot change the mapping
  yourself.
- The product you are converting **from** must have enough units on hand, and
  both products must be active stock products with SKUs.
- Decide whether you are **splitting** (pack → units) or **merging** (units →
  pack). The dialog shows which one applies and the units-per-pack ratio.

## Worked example: open a case of singles

Pack **DIA-0005** contains **12** singles. You have **7** packs and **14**
singles on hand. You open **3** packs.

1. Open **Inventory** and find the **DIA-0005** row.
2. Click **Repack** on that row. The dialog uses DIA-0005 as the source and
   shows on hand: **7**.
3. It offers **Convert into DIA-0009 (split, 12 units per pack)**. If more than
   one pack or unit is possible, choose the right target from the
   **Convert into** list.
4. Enter **3** in **Source quantity**. The preview should read: produce **36**
   units, source on hand becomes **4**, target on hand becomes **50**.
5. Add a note if useful, then click **Confirm repack**.
6. Note the reference in the success message, for example
   `RP-4F2A9C31B7E0` (yours will differ).
7. Check the result: the DIA-0005 row shows **4** packs, the DIA-0009 row shows
   **50** singles, and the **Repack history** card lists the new conversion.
   If you have a manager/admin account, the product detail movement table shows
   **Repack out −3** and **Repack in +36** with the same `RP-…` reference.

## Merge units back into a pack

You now have 50 singles of DIA-0009 and want to put **24** of them back into
packs of 12.

1. On **Inventory**, click **Repack** on the **DIA-0009** row.
2. Choose **DIA-0005** from **Convert into** — the dialog shows
   **merge, 12 units per pack**.
3. Enter **24** as the source quantity. The preview should show **2** packs
   produced, source on hand **26**, target on hand **6**.
4. Click **Confirm repack** and note the new reference.

The result is **6** packs and **26** singles. The earlier split stays in history
unchanged: a merge is its own record, with its own reference. The original
conversion cannot be undone or edited.

## When the repack will not go through

| What you see | What to do |
| --- | --- |
| "not configured as a pack or a pack child" | Ask a manager/admin to configure the pack mapping on the pack product. |
| `Only N units on hand` | Lower the source quantity. N is what is currently available. |
| "X is not a multiple of R (remainder N)" | Enter a whole multiple of R (the units per pack). The remainder units stay on hand. |
| "Computed target exceeds 1,000,000" | Use a smaller source quantity and repeat. |
| `not_a_repack_pair` | The products are not directly linked. Repack one linked pair at a time; carton → pack → single in one step is not supported. |
| `sku_required` | Ask a manager/admin to give both products an SKU. |
| HTTP 409 "Stock changed concurrently; please retry." | Someone changed the stock since you opened the dialog. Recheck on-hand and retry. |
| HTTP 401 | You are not signed in. Sign in and retry. |
| HTTP 403 | Your account is inactive or has the wrong role. Ask an admin to check your account and role. |
| `mapping_in_use` (a manager editing a used mapping) | Leave the used mapping alone; create new SKUs for a new pack size. |

If the dialog rejects the quantity, nothing is recorded and no stock moves.
Correct the value and try again.

## Do not

- Do not enter fractions or decimals. Only whole units are accepted; there is
  no half-pack.
- Do not submit the same repack twice. There is no duplicate detection: two
  submissions create two repacks even if you meant one. Confirm disables itself
  while the request is in flight, then check the history before clicking again.
- Do not try to edit or delete a repack, or reverse it by changing an old
  record. Use a new merge repack.
- Do not change a pack mapping that already has repack history, and do not
  hard-delete a product used in a repack. Create new SKUs instead.
- Do not repack non-stock or inactive products; the **Repack** button is
  disabled for non-stock items and the API rejects both cases.
