# Pricing settings and suggested price

The **default target margin** is one store-wide percentage. A manager or admin
sets it once in **Settings → Pricing**; the product form uses it to show a
**Suggested price** next to a product's unit cost. The suggestion is advisory
only: it never changes the **Retail price** you save.

For the release note, see [CHANGELOG.md](../CHANGELOG.md).

## Contents

- [Where do I set the default margin?](#where-do-i-set-the-default-margin)
- [How is the value saved?](#how-is-the-value-saved)
- [How is the suggested price calculated?](#how-is-the-suggested-price-calculated)
- [Worked examples](#worked-examples)
- [Troubleshooting](#troubleshooting)
- [Developer reference](#developer-reference)

## Where do I set the default margin?

1. Open **Settings** and select the **Pricing** tab. Settings has four tabs:
   **Store**, **Pricing**, **Backup**, and **Appearance**; Pricing is the
   second one.
2. Set **Default target margin (%)**. A fresh install starts at **15**.
3. Select **Save**. You should see **"Pricing settings saved."**

The input accepts a number from **0** to **100** inclusive, with at most **two
decimal places**. `15`, `18.5`, `0`, and `100` are valid. `15.500` is accepted
as `15.5` because trailing zeros are not extra precision. A saved `100` is
valid, but the product form cannot derive a price from a 100% gross margin — it
would divide by zero — so the suggestion falls back to 15% instead (see
[How is the suggested price calculated?](#how-is-the-suggested-price-calculated)).
**Cancel** discards
an edit and restores the last saved value. An unsaved margin edit raises the
same leave-page warning as the Store and Backup tabs.

Only managers and admins can open Settings and save the margin. Cashiers do not
see the tab, but they do see the suggested price on the product form.

## How is the value saved?

When you open the tab, the page reads `GET /api/settings/pricing/` and fills the
input with the configured number. It shows `15` while that request is in
flight, so a fresh install never shows an empty field. Selecting **Save** sends
a `PATCH` with a JSON number:

```json
{ "default_target_margin_percent": 18.5 }
```

The value shown afterwards comes from the server's response — the value that
will be used on the next load — not from what you typed. Reading the setting is
allowed for every signed-in role because the product form needs the margin;
changing it is manager/admin-only.

The value is stored in the `business` app's `AppSetting` table under the key
`default_target_margin_percent`. A migration seeds it to `15` on a fresh
install. If the row is missing or corrupt (for example, hand-edited in the
database), reads fall back to `15` rather than failing.

If the initial read fails, the tab hides the form and shows the error plus a
**Try again** button instead of an editable input. This is deliberate: an
unknown saved margin must not be silently overwritten by a save. The product
form behaves differently because its suggestion is advisory — a failed read
there keeps the `15%` fallback and leaves the form usable.

## How is the suggested price calculated?

Open a product (**Products → New product** or **Edit product**) and look in the
**Pricing & stock** section. **Suggested price** is a read-only field beside
**Cost**, with the hint "Price for a 15% gross margin" (or the current
configured percentage). It is recalculated as you type, so changing the cost
updates the suggestion immediately; reloading the form picks up a changed
margin.

For a unit cost `C` and the configured margin `m` in percent:

```text
suggested_price = C / (1 - m / 100)
```

The margin is a **gross margin on the selling price**, not a markup on cost:
at 15%, a cost of `100.00` suggests `117.65`, because 15% of `117.65` is the
`17.65` gross profit. The result is rounded to two decimal places (the currency
minor unit), half away from zero, and always displayed with two decimals —
`100.00`, not `100`. A small float tolerance keeps a value such as
`2.53 ÷ 0.88` (binary `2.8749999…`) rounding up to `2.88` rather than down to
`2.87`.

If the cost is blank, non-numeric, or negative, the suggestion is **empty**,
not `0.00`. A cost of `0` is a real value and suggests `0.00`.

The suggestion is a hint, not a stored price:

- There is no `suggested_price` field; nothing is saved from this calculation.
- It does not overwrite **Retail price**, which you still enter yourself.
- The product's **Markup margin (%)** field is still saved with the product,
  but it no longer drives the suggestion. The suggestion always uses the
  store-wide default from Settings → Pricing.
- If the settings request fails or returns an unusable margin — missing,
  non-numeric, negative, or 100 and above — the suggestion falls back to
  **15%**. A margin of `100` is storable in Settings (the API allows 0–100),
  but a 100% gross margin would divide by zero, so the form treats it as
  unusable and shows the 15% suggestion instead of `Infinity`.

## Worked examples

| Unit cost | Margin | Calculation | Suggested price |
|---|---|---|---|
| 100.00 | 15% (default) | 100 ÷ 0.85 = 117.647… | `117.65` |
| 100.00 | 10% | 100 ÷ 0.90 = 111.111… | `111.11` |
| 100.00 | 25% | 100 ÷ 0.75 = 133.333… | `133.33` |
| 100.00 | 0% | 100 ÷ 1.00 = 100 | `100.00` |
| 8.00 | 18.5% (configured) | 8 ÷ 0.815 = 9.816… | `9.82` |
| 9.25 | 15% | 9.25 ÷ 0.85 = 10.882… | `10.88` |
| 12.34 | 15% | 12.34 ÷ 0.85 = 14.518… | `14.52` |
| 1.10 | 15% | 1.1 ÷ 0.85 = 1.2941… | `1.29` |
| 0.05 | 15% | 0.05 ÷ 0.85 = 0.0588… | `0.06` |
| 0.00 | 15% | 0 ÷ 0.85 = 0 | `0.00` |
| *(blank)* | 15% | no cost to calculate | *(empty)* |

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| "Enter a number between 0 and 100." | The value is empty, not a plain decimal number, or outside 0–100. Exponents (`1e2`), hex (`0x10`), and `15%` are rejected. | Enter a decimal such as `18.5`. |
| "Enter a percentage with at most 2 decimal places." | More than two fraction digits; trailing zeros do not count. | Enter `15.5`, not `15.555`. |
| "Failed to load pricing settings. The default of 15% is used until they load." plus **Try again** | The initial `GET` failed, so the form is hidden to protect an unknown saved value. | Select **Try again**. If it keeps failing, check the API service. |
| Save shows an error instead of "Pricing settings saved." | Validation blocked the request, you lack manager/admin authority, or the `PATCH` failed. | Correct the value or ask a manager/admin to save it. |
| Suggested price still shows `117.65` after a margin change | The product form could not read the settings and fell back to 15%. | Reload the form and check `GET /api/settings/pricing/`. |
| Suggested price shows the 15% value even though Settings holds `100` | A 100% gross margin divides by zero, so the form treats it as unusable and falls back to 15%. | Set a margin below 100 if you want the suggestion to use it. |
| Suggested price is empty | Cost is blank, non-numeric, or negative. | Enter a non-negative cost. |
| Suggested price differs from Retail price | The suggestion is a hint; Retail price is entered independently. | Set Retail price yourself. |

## Developer reference

### Pricing settings API

| Method | Permission | Request body | Response |
|---|---|---|---|
| `GET /api/settings/pricing/` | any authenticated role | — | `{"default_target_margin_percent": <number>}` |
| `PATCH` / `PUT /api/settings/pricing/` | manager/admin | `{"default_target_margin_percent": <number or numeric string>}` | same shape, echoing the stored value |

Accepted values are finite numbers or numeric strings from `0` to `100`
inclusive with at most two fraction digits; trailing zeros are allowed and
booleans are not. The response value is a JSON number (`15`), not a string
(`"15.00"`) or a DRF decimal string. Validation runs before any write, so an
invalid request persists nothing, and unknown keys are rejected to prevent
mass assignment.

| Case | Status | Body |
|---|---|---|
| Missing `default_target_margin_percent` | 400 | `{"default_target_margin_percent": ["This field is required."]}` |
| Non-numeric, out of range, boolean, non-finite, or more than 2dp | 400 | `{"default_target_margin_percent": ["Enter a number between 0 and 100."]}` or `["Enter a percentage with at most 2 decimal places."]` |
| Unknown key or non-object body | 400 | `{"detail": "..."}` |
| Not signed in | 401 | authentication error |
| Non manager/admin write | 403 | `{"detail": "Only managers and admins can access this resource."}` |

On a fresh install the migration `business.0004` seeds the
`AppSetting` row with `15`. `backend/business/services.py` reads and validates
it and falls back to `Decimal('15')` when the row is missing or corrupt;
`backend/business/views.py` exposes the endpoint under `/api/settings/`.

### Calculation source

- `frontend/src/pricingSettings.ts` — parsing, validation messages, and the
  `15` fallback for the Settings tab.
- `frontend/src/suggestedPrice.ts` — `computeSuggestedPrice` (gross-margin
  formula `cost / (1 - margin / 100)`, half-up rounding to cents, fixed
  two-decimal string, empty for no cost) and `resolveTargetMarginPercent` (the
  `15` fallback; a margin of `100` or more is unusable because it divides by
  zero).
- `frontend/src/ProductPage.tsx` — loads the setting on mount and renders the
  read-only **Suggested price** field.
