# Kronos, explained in plain language

*Written 2026-07-28. This is the readable companion to the technical files. If you only read
one document about this project, read this one.*

---

## The short version

We are testing a free, pre-trained AI model called **Kronos** to see whether it can predict
gold and Bitcoin price moves well enough to trade. That is the entire project. One question,
answered yes or no.

We are **not** building a strategy. We are finding out whether there is anything here worth
building a strategy on. Most of the time the answer to a question like this is no — and no is
a perfectly good result, because it costs a few hours instead of a few weeks.

**Nothing in this project touches your live bot.** No strategy files, no configs, no risk
engine. It lives in its own folder with its own Python setup, deliberately sealed off.

---

## What Kronos is

Someone else trained a neural network on an enormous pile of candlestick charts from many
different markets. You show it a stretch of recent candles; it predicts the next few. It is
published free, already trained.

The appeal is obvious: no training, no tuning, no data collection. Download it and ask.

The catch is equally obvious once you say it out loud: if a free model could predict markets,
the people who released it would be trading it rather than publishing it. That is not proof it
is useless — but it is the reason we test cheaply and expect a "no".

We are using it **zero-shot**, meaning straight out of the box with no customisation. So
whatever we conclude applies to the out-of-the-box model, not to Kronos-in-principle.

---

## What we actually measure

### Stage 1 — does the prediction line up with reality at all?

We walk backwards through your historical 15-minute data. At thousands of points in time, we
show Kronos only the candles it would have had *at that moment*, ask what happens next, then
compare its answer with what genuinely did happen.

Score this and you get a number called the **information coefficient**, or **IC**. Think of it
as "how well do the predictions rank against reality":

- **IC = 0** — the model is guessing. Its predictions tell you nothing.
- **IC = 1** — perfect foresight. Does not exist.
- **IC = 0.03 to 0.05** — genuinely interesting. This sounds tiny, and it is, but real edges in
  liquid intraday markets are tiny. Anything large is nearly always a bug or a data artifact.

If you take one thing from this section: **a small IC is not disappointing, it is the realistic
target.** A huge IC means we made a mistake.

### Stage 2 — would it have made money?

Correlation and profit are not the same thing, and the gap between them has already killed one
of your strategies. Your `session_vwap_reversion` research looked fine on paper and died once
realistic fill costs went in. So Stage 2 takes the predictions and asks: if you had actually
traded these, paying spread and slippage, would you have come out ahead?

The output is **PF**, or **profit factor** — total winnings divided by total losses.

- **PF = 1.0** — you broke exactly even.
- **PF below 1.0** — you lost money.
- **PF = 1.1** — you made 10% more than you lost. This is our minimum bar.

---

## The tricky part: is it predicting, or just remembering?

This is the one genuinely subtle idea in the project, and it is worth the two minutes.

Kronos was trained on historical market data. The people who released it did not publish
exactly which years went in. So it is entirely possible that the years we are testing on were
part of its training.

If so, the model isn't forecasting those years — it is **recalling** them. It would look
brilliant on our test and be worthless in live trading, because next Tuesday is not in anyone's
training data.

You cannot ask the model whether it remembers. So we do this instead: **score every year
separately, and treat 2026 as the real exam.** 2026 is the year most likely to fall *after*
the model finished training.

That gives a clean rule:

- Looks good in 2024 and 2025 but falls apart in 2026 → that is **memory**, not skill. **Fails.**
- Holds up in 2026 → that is at least *consistent with* genuine forecasting ability.

This is the single most important idea in the project. It is why the reports break everything
out by year instead of giving one headline number, and why a strong overall score means nothing
on its own.

---

## The pass/fail rule

Decided in advance, and deliberately mechanical — so that neither of us can look at a
disappointing number and talk ourselves into liking it. All three must hold:

1. **2026 IC is at least 0.03** — there is a real signal in the most recent year.
2. **2026 profit factor is at least 1.1** — that signal survives trading costs.
3. **No other year loses money** — it is not a one-year fluke.

Pass all three → **GREEN**. Fail any → **RED**.

**RED is the expected outcome and it is a good outcome.** It costs a few hours and permanently
closes off a question, the same way your Fourier, EURUSD and crypto hunts did. The expensive
mistake is not getting a RED — it is half-testing something, staying curious about it, and
coming back to re-research it three more times.

**GREEN would not mean "start trading it."** It would mean the idea earned a proper
walk-forward study against your usual `backtest.md` gates. GREEN is permission to keep looking,
nothing more.

---

## How to read the generated reports

The tool writes `reports/kronos_ic_smelltest_XAUUSD.md` and `..._BTCUSD.md`. They are dense.
Here is the decoder.

**The verdict line** — `**Verdict: GREEN**` or `**Verdict: RED**`, with the reasons underneath
in order. Read the reasons; they name which of the three rules failed and at what number.

**The header line** — records the exact settings the run used, so a result can never be
mistaken for one produced under different conditions. `ctx 256` is how many candles of history
the model saw (256 × 15 min ≈ 2.5 days). `paths 5` is how many predictions were averaged per
decision point.

**Stage 1 table — the IC numbers.** Rows are how far ahead it predicted; columns are years.

- `h1` = 15 minutes ahead, `h2` = 30, `h3` = 45, `h4` = 1 hour ahead.
- Values hovering around 0.00, plus or minus about 0.02, mean **no signal**. Expect this.
- Look down the **2026 column first.** That is the exam. The other columns are context.

**Stage 2 table — the profit factors**, same layout. Below 1.0 anywhere means that cell lost
money.

A worked example of the reasoning: if `h4` shows 0.08 in 2024, 0.06 in 2025 and 0.00 in 2026,
that is not a decaying edge — that is the memory problem, caught exactly as designed.

---

## What is running right now, and what it cost

Both instruments are being forecast: roughly 4,300 decision points for gold, 4,600 for Bitcoin.
About 3½ hours of computation on your M1, running detached so it survives you closing the
laptop. When it finishes you get a desktop notification with both verdicts.

Two things worth knowing about the run:

**It is far slower than planned.** The plan budgeted 20 minutes; the true cost at full fidelity
is about 15 hours. We are running a faster, slightly lower-fidelity setting instead — the model
sees 2.5 days of history rather than 5, and averages 5 predictions per point rather than 15.

**That creates one honest loophole, which we are closing.** A blurrier prediction pushes the IC
score toward zero all by itself. So a RED from this run could in principle be caused by the
faster setting rather than by the model lacking skill. The follow-up check handles it: if the
answer is RED, we re-test a slice of 2026 at full fidelity and confirm the RED holds. If the
answer is GREEN, we re-run properly to confirm before believing it.

Either way the conclusion gets checked at full quality before it goes in the books. It is worth
knowing that this loophole exists, because "we ran it cheaper and it looked bad" is not the same
statement as "it is bad."

---

## Where everything lives

| File | What it is |
|---|---|
| `reports/kronos_explained.md` | This document. |
| `reports/kronos_ic_smelltest_XAUUSD.md` | **The gold answer.** Written when the run finishes. |
| `reports/kronos_ic_smelltest_BTCUSD.md` | The Bitcoin answer. |
| `docs/superpowers/specs/2026-07-25-kronos-ic-smelltest-design.md` | The design. §1, §7 and §9 are the parts that matter. |
| `.superpowers/sdd/2026-07-25-kronos-ic-smelltest/progress.md` | Blow-by-blow of what actually happened, including what went wrong. |
| `docs/superpowers/plans/2026-07-25-kronos-ic-smelltest.md` | The build instructions. Long, and safe to skip. |
| `scripts/kronos/` | The code. Four small files. |

**Why Bitcoin is in here at all:** it is the instrument Kronos's own authors highlighted. It is
the sanity check. If Kronos shows nothing on gold *and* nothing on Bitcoin, the honest reading is
that we may have built the test wrong. If it shows something on Bitcoin but nothing on gold, the
test works fine and gold is simply the harder market — which is a much more trustworthy result.

---

## Results

*Pending — the run is still going. This section gets filled in with both verdicts, the per-year
numbers behind them, and the outcome of the full-fidelity confirmation check.*
