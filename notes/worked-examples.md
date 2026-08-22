# Worked examples - one instance of every case type

Generated from `data/demo/` (seed 99, all ten case types). Every figure is copied from
the CSVs; the arithmetic is shown so a reviewer can check a row by hand without running
anything. Gateway money is integer paise, as Razorpay reports it.

Note `order_receipt`: populated on only ~38% of gateway rows. Where it is empty the
invoice link does not exist in the data and must be inferred through the bank
narration - see notes/failure-modes.md.

Regenerate with: `ledgerloop generate --rows 500 --seed 99 --out data/demo`


---

## `batched_settlement`

> 1 of 5

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000001` | BHAVNAGAR FORGINGS LIMITED - Rs 50000.00 |
| gateway | `pay_000000000001` type=payment | amount=5000000 fee=100000 tax=18000 net=4882000 |
| gateway | order_receipt | `INV-2026-000001` |
| gateway | `pay_000000000002` type=payment | amount=6946100 fee=138922 tax=25006 net=6782172 |
| gateway | order_receipt | *(empty - must be inferred)* |
| gateway | `pay_000000000003` type=payment | amount=4515650 fee=90313 tax=16256 net=4409081 |
| gateway | order_receipt | *(empty - must be inferred)* |
| gateway | `pay_000000000004` type=payment | amount=14142400 fee=282848 tax=50913 net=13808639 |
| gateway | order_receipt | *(empty - must be inferred)* |
| gateway | `pay_000000000005` type=payment | amount=500000 fee=10000 tax=1800 net=488200 |
| gateway | order_receipt | *(empty - must be inferred)* |
| gateway | settlement | `setl_000000000001` utr=`300000000001` |
| bank | `TXN00000001` (ICICI) | credit=Rs 303700.92 value_date=16-08-2026 |

Narration:

```
BIL/ONL/300000000001/BFL
```

**Arithmetic**

```
pay_000000000001: 5000000 - 100000 - 18000 = 4882000
pay_000000000002: 6946100 - 138922 - 25006 = 6782172
pay_000000000003: 4515650 - 90313 - 16256 = 4409081
pay_000000000004: 14142400 - 282848 - 50913 = 13808639
pay_000000000005: 500000 - 10000 - 1800 = 488200
                         sum = 30370092 paise = Rs 303,700.92
                 bank credit = 30370092 paise
                       match = True
```

---

## `clean`

> exact 1:1:1

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000061` | JODHPUR HANDICRAFTS LLP - Rs 465343.00 |
| gateway | `pay_000000000061` type=payment | amount=46534300 fee=0 tax=0 net=46534300 |
| gateway | order_receipt | `INV-2026-000061` |
| gateway | settlement | `setl_000000000017` utr=`300000000017` |
| bank | `TXN00000017` (ICICI) | credit=Rs 465343.00 value_date=03-06-2026 |

Narration:

```
UPI/300000000017/JHL/jodhpurhan@ibl
```

**Arithmetic**

```
amount          = 46534300
fee             = 0
tax (18% fee)   = 0
net             = 46534300 - 0 - 0 = 46534300
bank credit     = 46534300 = Rs 465,343.00
match           = True
```

---

## `date_skew`

> bank later by 1d

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000336` | CALICUT HANDICRAFTS LLP - Rs 17146.00 |
| gateway | `pay_000000000336` type=payment | amount=1714600 fee=0 tax=0 net=1714600 |
| gateway | order_receipt | *(empty - must be inferred)* |
| gateway | settlement | `setl_000000000292` utr=`300000000292` |
| bank | `TXN00000292` (HDFC) | credit=Rs 17146.00 value_date=16/06/26 |

Narration:

```
NEFT-HDFC0009812-CALICUT HANDICRAFTS LLP-7411523786
```

**Arithmetic**

```
amount          = 1714600
fee             = 0
tax (18% fee)   = 0
net             = 1714600 - 0 - 0 = 1714600
bank credit     = 1714600 = Rs 17,146.00
match           = True
```

---

## `duplicate_utr`

> UTR 300000000307 reused

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000351` | HALDIA ENTERPRISES INDIA PRIVATE LIMITED - Rs 42536.99 |
| gateway | `pay_000000000351` type=payment | amount=4253699 fee=85074 tax=15313 net=4153312 |
| gateway | order_receipt | *(empty - must be inferred)* |
| gateway | settlement | `setl_000000000307` utr=`300000000307` |
| bank | `TXN00000307` (HDFC) | credit=Rs 41533.12 value_date=08/07/26 |

Narration:

```
IMPS-6999383072-HALDIA ENTERPRISES INDIA PRIVATE LIMITED-KKBK
```

**Arithmetic**

```
amount          = 4253699
fee             = 85074
tax (18% fee)   = 15313
net             = 4253699 - 85074 - 15313 = 4153312
bank credit     = 4153312 = Rs 41,533.12
match           = True
```

---

## `fee_deducted`

> fee 2% + GST 18% on fee

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000361` | CALICUT CHEMICALS INDIA PRIVATE LIMITED - Rs 43000.99 |
| gateway | `pay_000000000361` type=payment | amount=4300099 fee=86002 tax=15480 net=4198617 |
| gateway | order_receipt | *(empty - must be inferred)* |
| gateway | settlement | `setl_000000000317` utr=`300000000312` |
| bank | `TXN00000317` (HDFC) | credit=Rs 41986.17 value_date=04/07/26 |

Narration:

```
IMPS-300000000312-CALICUT CHEMICALS INDIA PRIVATE LIMITED-SBIN
```

**Arithmetic**

```
amount          = 4300099
fee             = 86002
tax (18% fee)   = 15480
net             = 4300099 - 86002 - 15480 = 4198617
bank credit     = 4198617 = Rs 41,986.17
match           = True
```

---

## `orphan`

> unmatchable bank credit

Bank row `TXN00000367` (ICICI) credits **Rs 171,461.25**:

```
UPI/300000000362/Payment from/anantapura@apl/AXIS
```

No invoice and no settlement exist for it. Recorded in `truth.csv` with empty
`invoice_id` and `settlement_id` so `evals/` can tell a correct refusal from a miss.


---

## `partial_payment`

> customer short-paid

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000411` | RAIPUR AGRO EXPORTS PRIVATE LIMITED - Rs 9561.00 |
| gateway | `pay_000000000411` type=payment | amount=573660 fee=0 tax=0 net=573660 |
| gateway | order_receipt | *(empty - must be inferred)* |
| gateway | settlement | `setl_000000000367` utr=`300000000367` |
| bank | `TXN00000372` (HDFC) | credit=Rs 5736.60 value_date=06/08/26 |

Narration:

```
UPI-RAIPUR AGRO EXPORTS PRIVATE LIMITED-RAIPURAGRO@OKAXIS-2375515329-PAYMENT FROM RAE
```

**Arithmetic**

```
amount          = 573660
fee             = 0
tax (18% fee)   = 0
net             = 573660 - 0 - 0 = 573660
bank credit     = 573660 = Rs 5,736.60
match           = True
```

---

## `refund_netted`

> refund netted in payout

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000441` | ANANTAPUR ENGINEERING PVT LTD - Rs 32871.50 |
| gateway | `pay_000000000441` type=payment | amount=3287150 fee=65743 tax=11834 net=3209573 |
| gateway | order_receipt | `INV-2026-000441` |
| gateway | `rfnd_000000000001` type=refund | amount=821788 debit=821788 |
| gateway | settlement | `setl_000000000397` utr=`300000000397` |
| bank | `TXN00000402` (ICICI) | credit=Rs 23877.85 value_date=23-07-2026 |

Narration:

```
UPI/8371433567/Payment from/anantapure@okhdfcbank/KKBK
```

**Arithmetic**

```
payment net     = 3209573
refund amount   = 821788   (type=refund, carries a debit)
expected credit = 3209573 - 821788 = 2387785 paise = Rs 23,877.85
bank credit     = 2387785
match           = True
```

---

## `rounding_drift`

> +2 paise discrepancy

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000461` | HOSUR AGRO EXPORTS PRIVATE LIMITED - Rs 25000.00 |
| gateway | `pay_000000000461` type=payment | amount=2500000 fee=0 tax=0 net=2500000 |
| gateway | order_receipt | `INV-2026-000461` |
| gateway | settlement | `setl_000000000417` utr=`300000000417` |
| bank | `TXN00000422` (HDFC) | credit=Rs 25000.02 value_date=26/07/26 |

Narration:

```
NEFT CR-HDFC0000123-HOSUR AGRO EXPORTS-UTR9904738795
```

**Arithmetic**

```
expected credit = 2500000
bank credit     = 2500002
drift           = +2 paise   <- deliberately injected and labelled
```

---

## `tds_deducted`

> receipt net of TDS

| file | key | value |
|---|---|---|
| invoice_ledger | `INV-2026-000471` | CUTTACK LOGISTICS PRIVATE LIMITED - Rs 41360.00 |
| invoice_ledger | TDS | section 194C |
| gateway | `pay_000000000471` type=payment | amount=4053280 fee=0 tax=0 net=4053280 |
| gateway | order_receipt | `INV-2026-000471` |
| gateway | settlement | `setl_000000000427` utr=`300000000427` |
| bank | `TXN00000432` (ICICI) | credit=Rs 40532.80 value_date=24-08-2026 |

Narration:

```
MMT/IMPS/300000000427/CUTTACK LOGISTIC/ICIC
```

**Arithmetic**

```
invoice gross   = 4136000 = Rs 41,360.00
TDS 194C        = 82720 = Rs 827.20  (2.0%)
captured        = 4053280 = Rs 40,532.80
bank credit     = 4053280
match           = True
```
