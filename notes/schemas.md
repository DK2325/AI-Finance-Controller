# Where these schemas come from

This file answers one question a panel will ask: *"your metrics are on data you generated
— how do you know it resembles real settlement data?"*

The answer is that the column names, the units, and the narration grammars were not
invented. They were taken from Razorpay's published recon report and from two real Indian
bank statement export formats, and every one is cited below.

---

## 1. Gateway settlements — `gateway_settlements.csv`

Mirrors the **Razorpay Settlement Recon report**.

**Sources**
- <https://razorpay.com/docs/api/settlements/fetch-recon/> — field list, types, units
- <https://razorpay.com/docs/api/settlements/entity/> — settlement entity
- <https://razorpay.com/docs/payments/settlements/dashboard/> — dashboard report

### Fields as Razorpay publishes them

| Field | Type | Notes |
|---|---|---|
| `entity_id` | string | id of the settled transaction (`pay_…`, `rfnd_…`) |
| `type` | string | `payment` \| `refund` \| `transfer` \| `adjustment` |
| `debit` | integer | **paise** |
| `credit` | integer | **paise** |
| `amount` | integer | **paise** |
| `currency` | string | 3-letter ISO |
| `fee` | integer | **paise** |
| `tax` | integer | **paise** — GST charged on the fee |
| `settlement_id` | string | `setl_…` |
| `settlement_utr` | string | bank UTR for the payout |
| `payment_id` | string | `pay_…` |
| `order_id` | string | `order_…` |
| `order_receipt` | string | merchant's own reference |
| `method` | string | `card` \| `netbanking` \| `wallet` \| `upi` \| `emi` |
| `created_at` | integer | unix timestamp |
| `settled_at` | integer | unix timestamp |

### Amounts are integers in paise

Razorpay reports money as integer currency subunits — paise — not as decimal rupees.
The generator does the same, and every internal calculation is integer paise, converted to
two-decimal rupees only when a CSV is written.

This is not only fidelity, it is a correctness requirement. Fee is ~2% and GST is 18% *of
the fee*; in float those produce repeating decimals, and the resulting drift of a paisa or
two would be **indistinguishable from the deliberately injected `rounding_drift` case
type** — a case the classifier is graded on. Integer paise means the only small
discrepancies in the data are the ones we injected and labelled.

### What we changed, and why

BUILD.md's original data contract was illustrative. Where it disagreed with the published
format, the published format won — the Phase 1 exit criterion requires real column names.

| BUILD.md (original) | Adopted | Why |
|---|---|---|
| `utr` | `settlement_utr` | real field name |
| `gross_amount` | `amount` | real field name |
| `gst_on_fee` | `tax` | real field name; it *is* GST on the fee |
| `settled_on` | `settled_at` | real field name, unix timestamp |
| `merchant_ref` | `order_receipt` | real field name |
| *(absent)* | **`type`** | **see below — load-bearing** |
| *(absent)* | `entity_id`, `currency`, `method` | present in real reports |
| `net_amount` | `net_amount` *(kept, derived)* | not a Razorpay field; see below |

#### `type` is load-bearing, and its absence was a real gap

In a genuine recon report a refund is **its own row**, with `type=refund`, carrying a
debit against the same payout.

That is exactly how the `refund_netted` case type (4% of the batch) manifests in
production. Without a `type` column the generator would have to invent some other
mechanism for it — and the classifier would then learn a signal that does not exist in
real data, on a case type it is graded on. Adding `type` removes that trap.

#### `net_amount` is derived, and is not a Razorpay field

Razorpay does not publish `net_amount`; real reports carry `credit` and `debit`. It is
retained as a convenience column because BUILD.md's contract names it and downstream
phases read it, and it is computed, exactly:

```
net_amount = amount - fee - tax        (for type=payment)
net_amount = -amount                   (for type=refund)
```

Anything reading it should know it is a convenience, not a source field.

---

## 2. Bank statement — `bank_statement.csv`

A real merchant banks with more than one bank, and the two produce genuinely different
statement exports. The generator emits **one** normalised file — BUILD.md's stated
contract — whose rows are drawn from **two dialects**, marked by a `bank` column.

Splitting into two files was rejected: it breaks the contract and complicates every
downstream phase, while the thing that actually makes matching hard is the *narration
grammar*, not the column headers, which get normalised away immediately.

### Source formats

**HDFC Bank** — `Date, Narration, Chq./Ref.No., Value Dt, Withdrawal Amt., Deposit Amt., Closing Balance`
<https://bankconv.com/blog/convert-hdfc-bank-statement-to-csv>

**ICICI Bank** — `Transaction Date, Value Date, Transaction Remarks, Withdrawal Amount (INR), Deposit Amount (INR), Balance (INR)`
<https://www.indiastatement.com/blog/icici-bank-statement-pdf-to-csv>

### Normalised contract

| Column | Source |
|---|---|
| `txn_id` | synthetic row id |
| `value_date` | HDFC `Value Dt` / ICICI `Value Date` |
| `narration` | HDFC `Narration` / ICICI `Transaction Remarks` |
| `debit` | HDFC `Withdrawal Amt.` / ICICI `Withdrawal Amount (INR)` |
| `credit` | HDFC `Deposit Amt.` / ICICI `Deposit Amount (INR)` |
| `balance` | HDFC `Closing Balance` / ICICI `Balance (INR)` |
| `bank_ref` | HDFC `Chq./Ref.No.` / ICICI reference token |
| `bank` | dialect marker: `HDFC` or `ICICI` |

### How the two dialects actually differ

Not just headers — the differences survive normalisation and are what the matcher has to
cope with:

| | HDFC | ICICI |
|---|---|---|
| delimiter | hyphen | slash |
| casing | upper | mixed |
| date written as | `DD/MM/YY` | `DD-MM-YYYY` |
| UPI shape | `UPI-ACME RETAIL-acme@ybl-4083710` | `UPI/4083710/Payment from/acme@ybl` |
| NEFT shape | `NEFT-HDFC0000123-ACME RETAIL PVT-UTR…` | `NEFT/ICICR5202308/ACME RETAIL PVT LTD` |
| IMPS shape | `IMPS-4083710-ACME RETAIL-HDFC` | `MMT/IMPS/4083710/ACME RETAIL/HDFC` |

Both are additionally degraded with the noise real statements carry: truncated
counterparty names, inconsistent casing, missing separators, doubled spaces.

---

## 3. Invoice ledger — `invoice_ledger.csv`

The merchant's own book. No external format to mirror; fields follow BUILD.md's contract.

`invoice_id, customer_name, invoice_date, due_date, amount, tds_applicable, tds_section, status`

TDS sections and rates follow the Income Tax Act as commonly applied to B2B receipts:

| Section | Applies to | Rate |
|---|---|---|
| `194C` | contractor payments | 1–2% |
| `194J` | professional / technical fees | 10% |
| `194H` | commission or brokerage | 5% |
| `194Q` | purchase of goods above threshold | 0.1% |

---

## 4. Ground truth — `truth.csv`

`invoice_id, settlement_id, txn_id, case_type, notes`

**Never read outside `evals/`.** Enforced by `tests/test_import_lint.py`, which fails the
build if `core/`, `model/`, `llm/` or `api/` so much as name this file in a string literal.
