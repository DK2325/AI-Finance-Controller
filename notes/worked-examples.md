# Worked examples - one instance of every case type

Generated from `data/demo/` (seed 99, all ten case types). Every figure below is
copied from the CSVs; the arithmetic is shown so a reviewer can check a row by hand
without running anything. Money in the gateway file is integer paise, as Razorpay
reports it; the bank and ledger files use two-decimal rupees.

Regenerate with: `ledgerloop generate --rows 500 --seed 99 --out data/demo`


---

## `batched_settlement`

> 1 of 5

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000001` | MARUTI TRADERS PRIVATE LIMITED - Rs 315278.00 |
| gateway | `pay_000000000001` type=payment | amount=31527800 fee=630556 tax=113500 net=30783744 |
| gateway | `pay_000000000002` type=payment | amount=7085899 fee=141718 tax=25509 net=6918672 |
| gateway | `pay_000000000003` type=payment | amount=38322250 fee=766445 tax=137960 net=37417845 |
| gateway | `pay_000000000004` type=payment | amount=49297925 fee=985959 tax=177473 net=48134493 |
| gateway | `pay_000000000005` type=payment | amount=22206075 fee=444122 tax=79942 net=21682011 |
| gateway | settlement | `setl_000000000001` utr=`300000000001` |
| bank | `TXN00000001` (ICICI) | credit=Rs 1449367.65 value_date=26-06-2026 |

Narration:

```
RTGS/ICICR52023994184/MARUTI TRADERS PRIVATE LIMITED
```

**Arithmetic**

```
pay_000000000001: 31527800 - 630556 - 113500 = 30783744
pay_000000000002: 7085899 - 141718 - 25509 = 6918672
pay_000000000003: 38322250 - 766445 - 137960 = 37417845
pay_000000000004: 49297925 - 985959 - 177473 = 48134493
pay_000000000005: 22206075 - 444122 - 79942 = 21682011
                         sum = 144936765 paise = Rs 1,449,367.65
                 bank credit = 144936765 paise = Rs 1,449,367.65
                       match = True
```

---

## `clean`

> exact 1:1:1

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000061` | ACME RETAIL PRIVATE LIMITED - Rs 187095.00 |
| gateway | `pay_000000000061` type=payment | amount=18709500 fee=0 tax=0 net=18709500 |
| gateway | settlement | `setl_000000000016` utr=`300000000016` |
| bank | `TXN00000016` (ICICI) | credit=Rs 187095.00 value_date=21-06-2026 |

Narration:

```
NEFT-ICICR52023081129-ACME RETAIL PRIVAT
```

**Arithmetic**

```
amount         = 18709500
fee            = 0
tax (18% fee)  = 0
net            = 18709500 - 0 - 0 = 18709500
bank credit    = 18709500 paise = Rs 187,095.00
match          = True
```

---

## `date_skew`

> bank later by 2d

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000336` | DECCAN AGRO EXPORTS PVT LTD - Rs 339256.00 |
| gateway | `pay_000000000336` type=payment | amount=33925600 fee=0 tax=0 net=33925600 |
| gateway | settlement | `setl_000000000291` utr=`300000000291` |
| bank | `TXN00000291` (ICICI) | credit=Rs 339256.00 value_date=03-07-2026 |

Narration:

```
BIL/ONL/300000000291/DAE
```

**Arithmetic**

```
amount         = 33925600
fee            = 0
tax (18% fee)  = 0
net            = 33925600 - 0 - 0 = 33925600
bank credit    = 33925600 paise = Rs 339,256.00
match          = True
```

---

## `duplicate_utr`

> UTR 300000000306 reused

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000351` | DECCAN AGRO EXPORTS PVT LTD - Rs 113130.00 |
| gateway | `pay_000000000351` type=payment | amount=11313000 fee=226260 tax=40727 net=11046013 |
| gateway | settlement | `setl_000000000306` utr=`300000000306` |
| bank | `TXN00000306` (HDFC) | credit=Rs 110460.13 value_date=10/07/26 |

Narration:

```
NEFT CR-HDFC0004567-DECCAN AGRO EXPORTS PVT LTD-UTR300000000306
```

**Arithmetic**

```
amount         = 11313000
fee            = 226260
tax (18% fee)  = 40727
net            = 11313000 - 226260 - 40727 = 11046013
bank credit    = 11046013 paise = Rs 110,460.13
match          = True
```

---

## `fee_deducted`

> fee 2% + GST 18% on fee

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000361` | FIRSTLIGHT MEDIA LLP - Rs 307698.25 |
| gateway | `pay_000000000361` type=payment | amount=30769825 fee=615397 tax=110771 net=30043657 |
| gateway | settlement | `setl_000000000316` utr=`300000000311` |
| bank | `TXN00000316` (ICICI) | credit=Rs 300436.57 value_date=21-08-2026 |

Narration:

```
BIL/ONL/300000000311/FML
```

**Arithmetic**

```
amount         = 30769825
fee            = 615397
tax (18% fee)  = 110771
net            = 30769825 - 615397 - 110771 = 30043657
bank credit    = 30043657 paise = Rs 300,436.57
match          = True
```

---

## `orphan`

> unmatchable bank credit

Bank row `TXN00000366` (HDFC) credits **Rs 406,239.25**, narration:

```
RTGS-HDFC0002341-HIMALAYA FOODS PRIVATE LIMITED-300000000361
```

No invoice and no settlement exist for it. It appears in `truth.csv` with empty
`invoice_id` and `settlement_id` so `evals/` can distinguish a correct refusal
from a miss.


---

## `partial_payment`

> customer short-paid

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000411` | DECCAN AGRO EXPORTS PVT LTD - Rs 304098.99 |
| gateway | `pay_000000000411` type=payment | amount=18245939 fee=0 tax=0 net=18245939 |
| gateway | settlement | `setl_000000000366` utr=`300000000366` |
| bank | `TXN00000371` (ICICI) | credit=Rs 182459.39 value_date=19-06-2026 |

Narration:

```
BIL/ONL/300000000366/DAE
```

**Arithmetic**

```
amount         = 18245939
fee            = 0
tax (18% fee)  = 0
net            = 18245939 - 0 - 0 = 18245939
bank credit    = 18245939 paise = Rs 182,459.39
match          = True
```

---

## `refund_netted`

> refund netted in payout

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000441` | FIRSTLIGHT MEDIA LLP - Rs 231631.99 |
| gateway | `pay_000000000441` type=payment | amount=23163199 fee=463264 tax=83388 net=22616547 |
| gateway | `rfnd_000000000001` type=refund | amount=5790800 debit=5790800 net=-5790800 |
| gateway | settlement | `setl_000000000396` utr=`300000000396` |
| bank | `TXN00000401` (HDFC) | credit=Rs 168257.47 value_date=25/06/26 |

Narration:

```
IMPS-300000000396-FIRSTLIGHT MEDIA LLP-KKBK
```

**Arithmetic**

```
payment net    = 22616547
refund  amount = 5790800   (type=refund, carries a debit)
expected credit= 22616547 - 5790800 = 16825747 paise = Rs 168,257.47
bank credit    = 16825747 paise
match          = True
```

---

## `rounding_drift`

> +5 paise discrepancy

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000461` | BHARAT LOGISTICS PVT LTD - Rs 276976.75 |
| gateway | `pay_000000000461` type=payment | amount=27697675 fee=0 tax=0 net=27697675 |
| gateway | settlement | `setl_000000000416` utr=`300000000416` |
| bank | `TXN00000421` (ICICI) | credit=Rs 276976.80 value_date=24-06-2026 |

Narration:

```
UPI/6123105074/Payment from/bharatlogi@paytm/UTIB
```

**Arithmetic**

```
expected credit= 27697675 paise
bank credit    = 27697680 paise
drift          = +5 paise   <- deliberately injected and labelled
```

---

## `tds_deducted`

> receipt net of TDS

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000471` | BHARAT LOGISTICS PVT LTD - Rs 115387.00 |
| invoice_ledger | TDS | section 194H |
| gateway | `pay_000000000471` type=payment | amount=10961765 fee=0 tax=0 net=10961765 |
| gateway | settlement | `setl_000000000426` utr=`300000000426` |
| bank | `TXN00000431` (HDFC) | credit=Rs 109617.65 value_date=13/08/26 |

Narration:

```
NEFT-HDFC0004567-BHARATLOGISTICSPVTLTD-300000000426
```

**Arithmetic**

```
invoice gross  = 11538700 paise = Rs 115,387.00
TDS 194H       = 576935 paise = Rs 5,769.35  (5.0%)
captured       = 10961765 paise = Rs 109,617.65
bank credit    = 10961765 paise
match          = True
```
