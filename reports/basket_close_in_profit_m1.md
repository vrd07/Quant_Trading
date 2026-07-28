# No-SL "close when in profit" basket on M1 XAUUSD (2026-07-29)

**Verdict: the rule is a loss-HIDING mechanism, not a profit mechanism.**
100% of cycles close green. Every configuration loses badly. Uncapped, a $5,000
account is gone on day 12 of 56.

Script: `scripts/research_basket_close_m1.py`

## Rules under test (exactly as specified)

- open one 0.01-lot position at every M1 candle close (direction = EMA9 vs EMA21)
- **no stop loss, no take profit**
- the moment TOTAL floating P&L across all open positions ≥ $0.01, close ALL
- repeat

56 days of Dukascopy ticks, 2026-05-01..2026-07-17. Positions still open at each
day's end are marked to market rather than ignored — that single accounting choice
is what makes the result visible.

## Results

| version | cycles green | realized | unrealized | **TRUE total** |
|---|---|---|---|---|
| uncapped | 9,089 (100%) | **+$4,192.06** | −$64,007.44 | **−$59,815.38** |
| cap 20 pos | 4,302 (100%) | +$1,281.63 | −$10,090.05 | **−$8,808.42** |
| cap 5 pos | 4,147 (100%) | +$1,018.36 | −$8,470.21 | **−$7,451.84** |

**Realized P&L is positive in every single version. True P&L is deeply negative in
every single version.** The gap is the whole story.

## Why it looks like it works

"Close when in profit" cannot produce a losing closed trade — that is its
definition. So it sorts outcomes rather than improving them:

- **winners get realized** → they land in account history as closed green trades
- **losers get warehoused** → they sit as floating positions and are never booked

The result is a 100% win rate, 56/56 profitable days, and a −$59,815 account. The
statement "I have never had a losing trade" and "I have lost $59,815" are both true
simultaneously. The win rate is not evidence of edge; it is an artifact of the
closing rule.

## What the uncapped version costs

| | |
|---|---|
| Worst floating drawdown | **$11,885.29** (2026-05-26) |
| Peak open positions | **1,266** → $50,640 margin required |
| Median daily max float | $1,267.59 |
| Days floating > $500 | 54 / 56 |
| Days floating loss alone exceeds a $5k account | 4 / 56 |
| Days margin requirement alone exceeds a $5k account | **54 / 56** |

**First account-killing day: 2026-05-18 — day 12 of 56**, carrying $7,441 floating
on a $5,000 account. In practice it dies sooner: margin required exceeds the
account on 54 of 56 days, so the broker closes it out long before the drawdown
matures.

Median daily floating loss is $1,267 — a 25% drawdown carried on a typical day to
harvest roughly $30/day of realized gains.

## Capping does not fix it

A position cap bounds the margin (0/56 days breach the $5k account) but does not
change the mechanism — the losers still never close. Cap 20 still ends −$8,808 and
cap 5 still ends −$7,452. Tighter caps lose *less* only because they trade less.

## Why no variant works

Every version pays the 0.69 spread on every entry and never cuts a loser. The
realized total is a sample of the winning tail only; the true total is that plus
the warehoused losers, and it is negative by roughly the spread times the entry
count. Direction rule, threshold and cap change the size of the number, not the
sign.

## Do not re-research

Basket / grid / recovery / martingale variants of "close only when green" on gold.
The mechanism is arithmetic, not a parameter: a rule that can only realize winners
guarantees a 100% win rate and tells you nothing about expectancy. Judge any such
system on **equity including open positions**, never on closed-trade statistics.
