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
| invoice_ledger | `INV-2026-000001` | HALDIA GARMENTS PRIVATE LIMITED - Rs 315278.00 |
| gateway | `pay_000000000001` type=payment | amount=31527800 fee=630556 tax=113500 net=30783744 |
| gateway | `pay_000000000002` type=payment | amount=7085899 fee=141718 tax=25509 net=6918672 |
| gateway | `pay_000000000003` type=payment | amount=38322250 fee=766445 tax=137960 net=37417845 |
| gateway | `pay_000000000004` type=payment | amount=49297925 fee=985959 tax=177473 net=48134493 |
| gateway | `pay_000000000005` type=payment | amount=22206075 fee=444122 tax=79942 net=21682011 |
| gateway | settlement | `setl_000000000001` utr=`300000000001` |
| bank | `TXN00000001` (ICICI) | credit=Rs 1449367.65 value_date=26-06-2026 |

Narration:

```
RTGS/ICICR52023994184/HALDIA GARMENTS PRIVATE LIMITED
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
| invoice_ledger | `INV-2026-000061` | ANANTAPUR CASTINGS LLP - Rs 16229.00 |
| gateway | `pay_000000000061` type=payment | amount=1622900 fee=0 tax=0 net=1622900 |
| gateway | settlement | `setl_000000000017` utr=`300000000017` |
| bank | `TXN00000017` (ICICI) | credit=Rs 16229.00 value_date=28-06-2026 |

Narration:

```
BIL/ONL/9937675499/ACL
```

**Arithmetic**

```
amount          = 1622900
fee             = 0
tax (18% fee)   = 0
net             = 1622900 - 0 - 0 = 1622900
bank credit     = 1622900 paise = Rs 16,229.00
match           = True
```

---

## `date_skew`

> bank later by 3d

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000336` | HOSUR PHARMACEUTICALS INDIA PRIVATE LIMITED - Rs 489918.99 |
| gateway | `pay_000000000336` type=payment | amount=48991899 fee=0 tax=0 net=48991899 |
| gateway | settlement | `setl_000000000292` utr=`300000000292` |
| bank | `TXN00000292` (HDFC) | credit=Rs 489918.99 value_date=26/08/26 |

Narration:

```
NEFT-HDFC0004567-HOSUR PHARMACEUTICALS INDIA-300000000292
```

**Arithmetic**

```
amount          = 48991899
fee             = 0
tax (18% fee)   = 0
net             = 48991899 - 0 - 0 = 48991899
bank credit     = 48991899 paise = Rs 489,918.99
match           = True
```

---

## `duplicate_utr`

> UTR 300000000307 reused

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000351` | COIMBATORE PACKAGING LLP - Rs 425226.50 |
| gateway | `pay_000000000351` type=payment | amount=42522650 fee=850453 tax=153082 net=41519115 |
| gateway | settlement | `setl_000000000307` utr=`300000000307` |
| bank | `TXN00000307` (ICICI) | credit=Rs 415191.15 value_date=29-07-2026 |

Narration:

```
UPI/8451804905/CPL/coimbatore@paytm
```

**Arithmetic**

```
amount          = 42522650
fee             = 850453
tax (18% fee)   = 153082
net             = 42522650 - 850453 - 153082 = 41519115
bank credit     = 41519115 paise = Rs 415,191.15
match           = True
```

---

## `fee_deducted`

> fee 2% + GST 18% on fee

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000361` | RAIPUR CERAMICS PVT LTD - Rs 283157.00 |
| gateway | `pay_000000000361` type=payment | amount=28315700 fee=566314 tax=101937 net=27647449 |
| gateway | settlement | `setl_000000000317` utr=`300000000312` |
| bank | `TXN00000317` (ICICI) | credit=Rs 276474.49 value_date=11-07-2026 |

Narration:

```
UPI/300000000312/Payment from/raipurcera@apl/SBIN
```

**Arithmetic**

```
amount          = 28315700
fee             = 566314
tax (18% fee)   = 101937
net             = 28315700 - 566314 - 101937 = 27647449
bank credit     = 27647449 paise = Rs 276,474.49
match           = True
```

---

## `orphan`

> unmatchable bank credit

Bank row `TXN00000367` (ICICI) credits **Rs 492,842.99**, narration:

```
MMT/IMPS/6648951933/BHOPAL GARME/KKBK
```

No invoice and no settlement exist for it. It appears in `truth.csv` with empty
`invoice_id` and `settlement_id` so `evals/` can distinguish a correct refusal
from a miss.


---

## `partial_payment`

> customer short-paid

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000411` | NAGPUR FABRICATORS AND COMPANY - Rs 401165.75 |
| gateway | `pay_000000000411` type=payment | amount=16046630 fee=0 tax=0 net=16046630 |
| gateway | settlement | `setl_000000000367` utr=`300000000367` |
| bank | `TXN00000372` (ICICI) | credit=Rs 160466.30 value_date=19-07-2026 |

Narration:

```
NEFT/ICICR52023994791/NAGPUR FABRICATORS A
```

**Arithmetic**

```
amount          = 16046630
fee             = 0
tax (18% fee)   = 0
net             = 16046630 - 0 - 0 = 16046630
bank credit     = 16046630 paise = Rs 160,466.30
match           = True
```

---

## `refund_netted`

> refund netted in payout

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000441` | SOLAPUR AGRO EXPORTS PRIVATE LIMITED - Rs 404770.25 |
| gateway | `pay_000000000441` type=payment | amount=40477025 fee=809541 tax=145717 net=39521767 |
| gateway | `rfnd_000000000001` type=refund | amount=8095405 debit=8095405 net=-8095405 |
| gateway | settlement | `setl_000000000397` utr=`300000000397` |
| bank | `TXN00000402` (ICICI) | credit=Rs 314263.62 value_date=07-08-2026 |

Narration:

```
NEFT/ICICR52023081808/SOLAPUR AGRO EXPORTS PRIVATE LIMITED
```

**Arithmetic**

```
payment net     = 39521767
refund  amount  = 8095405   (type=refund, carries a debit)
expected credit = 39521767 - 8095405 = 31426362 paise = Rs 314,263.62
bank credit     = 31426362 paise
match           = True
```

---

## `rounding_drift`

> -2 paise discrepancy

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000461` | JALANDHAR MEDIA PVT LTD - Rs 438807.00 |
| gateway | `pay_000000000461` type=payment | amount=43880700 fee=0 tax=0 net=43880700 |
| gateway | settlement | `setl_000000000417` utr=`300000000417` |
| bank | `TXN00000422` (ICICI) | credit=Rs 438806.98 value_date=17-07-2026 |

Narration:

```
UPI/300000000417/JMP/jalandharm@ybl
```

**Arithmetic**

```
expected credit = 43880700 paise
bank credit     = 43880698 paise
drift           = -2 paise   <- deliberately injected and labelled
```

---

## `tds_deducted`

> receipt net of TDS

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000471` | BHAVNAGAR LOGISTICS PRIVATE LIMITED - Rs 81932.00 |
| invoice_ledger | TDS | section 194Q |
| gateway | `pay_000000000471` type=payment | amount=8185007 fee=0 tax=0 net=8185007 |
| gateway | settlement | `setl_000000000427` utr=`300000000427` |
| bank | `TXN00000432` (ICICI) | credit=Rs 81850.07 value_date=08-06-2026 |

Narration:

```
BIL/ONL/300000000427/BLP
```

**Arithmetic**

```
invoice gross   = 8193200 paise = Rs 81,932.00
TDS 194Q        = 8193 paise = Rs 81.93  (0.1%)
captured        = 8185007 paise = Rs 81,850.07
bank credit     = 8185007 paise
match           = True
```
