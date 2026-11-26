# Runbook: invoice a registered customer

For cashiers working the sales screen. The roles, audit notes, and full error
list are in [Cashier invoicing of registered customers](../cashier-invoice-permission.md).

## Before you start

- Log in with an active account whose role is `cashier`, `manager`, or `admin`.
- Open the register for your shift; the Sales page requires an open session.
- Make sure the customer is already in the customer list. Walk-ins cannot be
  invoiced, and only an admin can add a customer or change a credit limit.

## Invoice the sale

1. Ring up the items and check the total.
2. In the payment panel, choose **Invoice** as the payment method.
3. In **Customer**, select the registered customer. Do not leave *Walk-in
   customer* selected.
4. Check **Available credit**. If it is negative or too small for this sale,
   stop and follow "The invoice is blocked" below.
5. Enter the invoice amount, normally the sale total, and record the payment.
   The invoice itself asks for no manager username, password, or PIN.
6. Confirm the payment appears on the sale, then complete the sale as usual.

## The invoice is blocked

| What you see | What to do |
| --- | --- |
| `Select a customer.` or `customer required` | Choose a registered customer. Walk-ins pay by cash, card, or split. |
| `Your account may not invoice to an account.` | Ask an admin to check your role and that your account is active. |
| A message about exceeding the credit limit, followed by `An admin must raise this customer's credit limit.` | Ask an admin to raise the customer's credit limit or complete the invoice themselves, then retry. Manager approval alone will not clear this. |
| `Invoice amount exceeds the outstanding sale balance.` | Lower the amount to the unpaid balance. Do not re-invoice an amount already paid. |
| `Cannot change the customer once a payment is recorded; void or refund first.` | The customer is locked after payment. Ask a manager/admin to void or refund, then re-ring for the correct customer. |
| `Failed to record payment` with no server detail | Check you are still logged in and the register is open, then retry. |

When a tender is rejected, the sale and cart stay intact and the payment button
is usable again. Correct the problem and retry rather than starting a new cart.

## Do not

- Do not ask for a manager PIN for the invoice itself; the sales screen no
  longer requests one. Discounts still need manager credentials.
- Do not change the customer after a payment has been recorded.
- Do not invoice more than the sale's outstanding balance.
