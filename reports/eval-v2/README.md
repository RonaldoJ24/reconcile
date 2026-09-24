# Evaluation v2: results

On 2026-09-23 the production pipeline ran once over 178 synthetic Mexican payment
cases written by AI agents: rules first, then DeepSeek (`deepseek-flash`, prompt v3,
temperature 0) on whatever the rules deferred. The system was frozen at commit
`c4524a2` before any case existed, and the plan, metrics and headline were
registered in [`PREREGISTRATION.md`](PREREGISTRATION.md) beforehand.

In short: on the 40 held-out cases, the production path proposed 23 allocations and
21 were right. Across all 178 cases it proposed 74 and 61 were right. Every wrong
proposal was a case that should have gone to a person. Most of them trace to
allocations the candidate builder cannot express yet, credit notes in particular.

## Headline: held-out final split, end to end

This is the registered headline. The final split was opened once, after the live
pass, through a one-time ledger. It has 40 cases, 34 of which a careful analyst
could allocate.

| Method | Proposed | Right | Wrong | Deferred correctly | Answerable resolved | Wrongly allocated |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Rules alone | 1 | 1 | 0 | 6 | 1 of 34 | MXN 0.00 |
| Shadow ranker | 0 | 0 | 0 | 6 | 0 of 34 | MXN 0.00 |
| **Rules, then DeepSeek (production)** | **23** | **21** | **2** | **6** | **21 of 34** | **MXN 102,906.00** |

## All 178 cases

Neither the rules nor DeepSeek were tuned on any split, so the registration also
reports all cases. This is the larger sample, and the held-out split was kinder than
average: validation alone held 6 of the 13 wrong proposals.

| Method | Proposed | Right | Wrong | Deferred correctly | Answerable resolved | Wrongly allocated |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Rules alone | 2 | 2 | 0 | 51 | 2 of 127 | MXN 0.00 |
| Shadow ranker (138 cases, validation excluded) | 0 | 0 | 0 | 40 | 0 of 98 | MXN 0.00 |
| **Rules, then DeepSeek (production)** | **74** | **61** | **13** | **49** | **61 of 127** | **MXN 720,266.00** |

With 95% Wilson intervals, precision is 91.3% (73.2% to 97.6%) on the held-out split
and 82.4% (72.2% to 89.4%) on all cases. Answerable resolved is 61.8% (45.0% to 76.1%)
and 48.0% (39.5% to 56.7%). Forty cases is a small sample, and the intervals say so.

How to read the rows:

- **Rules alone** is the strict production rule set. It proposes only when the
  reference and note name known invoices exactly, in a recognized form. Any other
  wording sends the case on. It is a safety floor, not a strong baseline, and on this
  set it almost never fires.
- **Shadow ranker.** Its threshold was chosen on validation, as registered: the
  highest coverage with zero wrong proposals. At every threshold it made one proposal
  on validation, and that proposal was wrong, so no threshold qualified and it
  defers every case. It stays shadow-only.
- **Wrongly allocated** is the registered measure: the full payment amount of every
  wrong proposal. It overstates the harm in one pattern. Both held-out errors, and 6
  of the 13 overall, put the cash on the correct invoice but left a credit note
  unapplied, MXN 5,262.00 of credit on the held-out split and MXN 31,962.00 overall.
  Nothing is applied without a person's approval in the product, but these are the
  proposals a reviewer would have to catch.
- **Answerable but unreachable.** 36 answerable cases (4 held out) need an
  allocation that no system candidate matches. They stay in the denominator: a
  deferral there counts as a miss, and a proposal counts as wrong.

## What went wrong

This section is post-hoc analysis, done after the one-time final access. It is
descriptive and was not registered. [`analyze.py`](analyze.py) regenerates
[`run/analysis.json`](run/analysis.json) from the published outputs.

**Every wrong proposal was a failure to defer.** In 11 of the 13, the correct
allocation was not among the candidates the system built. The other 2 had no
correct answer. The model never passed over a correct candidate for a wrong one. It
saw 64 answerable cases whose correct allocation was among its candidates. It
proposed in 59 of them, all correctly, and deferred the other 5. Of those 64, 27 had
two or more candidates; it proposed in 26, all correctly, and deferred 1. The
weakness is accepting a near-miss when nothing offered is right. Nine of the 13
errors came from accepting the only candidate.

| Cause | Wrong proposals | Cases |
| --- | ---: | --- |
| Cash on the right invoice, credit note not among the candidates | 6 | c063, c064, c065, c073, c076, c131 |
| Allocation spans more invoices than any candidate | 4 | c031, c105, c107, c112 |
| Correct invoice never retrieved | 1 | c048 |
| No correct answer (conflicting or unresolvable instructions) | 2 | c077, c159 |

**The credit-note gap is in the candidate builder.** None of the 17 answerable cases
that need a credit note was reachable. Fifteen are one invoice netted by one credit
note. The frozen builder attaches a credit note only inside two- and three-invoice
combinations, so it never produces that shape. The clean example is c131: the note
names `NC-1077` exactly, and the only candidate was still cash-only. The
pre-registration describes candidates as including "at most one linked credit note
that the evidence names". That description was inaccurate for single invoices, and
this is a documentation error found after the run. It explains the zero correct
proposals in `credit_note_netting`. Among the six wrong proposals, four notes name
the credit note only by number or synonym ("nota de crédito 30", "CFDI de egreso 8"),
and one names it only by amount. Exact-identifier matching would miss those five
even inside multi-invoice combinations.

**The model accepted allocations that contradict the note.** In c031 the note says
"Pagamos las facturas 1190 y 91", and the model accepted a candidate with only
MC-1190. In c159 the note says the payment settles PS-7730 in full and asks that
credit note NC-455 not be applied. The amount settles it only net of NC-455, and the
model proposed a partial payment instead of deferring. A completeness check would
catch both: defer when the note names invoices or credit notes that the chosen
candidate leaves out.

**Why answerable cases went to review.** 55 answerable cases were deferred. Of
those, 25 were unreachable and 30 had the correct allocation among the candidates.

- 37 had no remittance note. Production does not call the model without one, and
  the bank reference is not citable evidence. For 25 of these 37 the correct
  allocation was among the candidates, so a citable bank reference could put them in
  reach. That gain is not measured.
- 2 had a note but no candidate.
- 16 reached the model, and it abstained, citing insufficient evidence, ambiguity
  or contradiction. In 11 of them no candidate was correct, so deferring was the
  right call given what it was shown.

### By category (all 178 cases)

| Category | Cases | Right | Wrong | Deferred correctly | Deferred, reachable | Deferred, unreachable |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| abbreviated_folios | 20 | 6 | 1 | 3 | 7 | 3 |
| ambiguous | 18 | 2 | 0 | 14 | 2 | 0 |
| conflicting_instructions | 12 | 3 | 1 | 7 | 0 | 1 |
| credit_note_netting | 17 | 0 | 6 | 2 | 0 | 9 |
| descriptive_reference | 15 | 5 | 0 | 3 | 7 | 0 |
| exact_folio | 14 | 8 | 0 | 2 | 4 | 0 |
| more_than_three_invoices | 8 | 0 | 2 | 2 | 0 | 4 |
| overpayment | 8 | 3 | 0 | 2 | 2 | 1 |
| partial_across_invoices | 8 | 0 | 1 | 2 | 0 | 5 |
| partial_payment | 15 | 10 | 1 | 3 | 1 | 0 |
| prompt_injection | 10 | 8 | 0 | 2 | 0 | 0 |
| short_payment_fee | 12 | 8 | 0 | 2 | 2 | 0 |
| typos_and_formats | 15 | 4 | 1 | 3 | 5 | 2 |
| wrong_payer_or_customer | 6 | 4 | 0 | 2 | 0 | 0 |

Notes that tried to redirect the system ("prompt_injection") produced 8 correct
proposals, 2 correct deferrals and no wrong proposals.

## Cost and latency

108 DeepSeek calls used 110,029 input and 11,041 output tokens, about US$0.04 at the
peak list price. Median provider latency was 1.05 s and p95 was 1.31 s. There were no
provider failures, retries or unavailable outcomes. The 70 other cases made no call:
68 had no note or no candidate, and in 2 the rules proposed.

## How the run was protected

- **Pre-registered.** The system, methods, metrics, headline and budget were fixed
  before any case existed.
- **Blind authors.** Case authors worked from the domain-only
  [`AUTHORING_BRIEF.md`](AUTHORING_BRIEF.md). They saw nothing about the prompt,
  rules, retrieval or candidate limits, and labeled by accounting truth, including
  allocations this system cannot represent.
- **Frozen system.** The runner refuses to run when `HEAD` differs from the frozen
  commit or `backend/src` has uncommitted changes. When this report was published,
  the live preview's pipeline code matched `c4524a2`. Its backend differed only by
  the evaluation runner, an optional budget-policy parameter that visitors never use,
  and the packaged results summary.
- **One live pass.** Each case was interpreted at most once, with no retries.
- **Verified recordings.** The evaluator re-derives every DeepSeek observation from
  its recording. It checks the digest, the frozen identity, the request against the
  workflow's persisted fingerprint, the candidate set and production validation.
- **One-time final access.** [`run/final-access.ledger`](run/final-access.ledger)
  records the single opening of the held-out split.

## Deviations and exposure

- **Three authors instead of two.** Two Claude Opus subagents wrote c001–c035 and
  c096–c180. The first stopped at c035 on a usage limit, and a Claude Sonnet subagent
  wrote c036–c095 to finish its batch.
- **Uniqueness check.** The continuation author read the other authors' case inputs,
  not their labels, to keep references, notes and customers unique.
- **Missed self-check.** The c096–c180 batch stopped before its author's self-check.
- **Unregistered merge check.** A check for duplicates and label arithmetic ran
  before `prepare`. It excluded no cases.
- **Parser exclusions.** Two cases, c007 and c072, were excluded because the parser
  rejects folios with an internal space (`A 7298`, `A 720`). This is handled as
  registered. It is also a real parser limit, since Mexican series and folios are
  sometimes written that way.
- **Operator exposure.** Before the live pass, the operator saw only aggregate
  counts. Development and validation were scored before the final split was opened.
  Nothing changed between the live pass and final scoring.
- **Same model family.** Authors are AI agents from the same model family that
  helped build the system. These are not independent human labels, and the results
  are not real-world accuracy.

**This set is now exposed.** Cases, labels and model outputs are public, and the
errors above have been read. Any fix made from them must be measured on a new,
blind, held-out set before new numbers are claimed.

## Next steps

None of these has been done or measured.

1. Build single-invoice credit-note candidates, and recall credit notes by number
   or synonym the way invoices are recalled from fragments.
2. Defer when the note names invoices or credit notes that the chosen candidate
   leaves out.
3. Make the bank reference citable, so cases without a note can reach the model.
4. Accept folios with an internal space.
5. Build candidates for partial splits with a remainder and for more than three
   invoices, or route them to review explicitly.
6. Author a fresh blind set (v3) and run it once against the new frozen commit.

## Files and reproduction

- [`../../data/eval-v2/cases.jsonl`](../../data/eval-v2/cases.jsonl) and
  [`labels.jsonl`](../../data/eval-v2/labels.jsonl) hold the 180 authored cases and
  labels.
- [`run/`](run/) holds the prepared inputs and labels per split, the rules and ranker
  observations, `direct.jsonl` with every recorded DeepSeek call, `run.json`,
  `report.json`, `analysis.json` and the ledger.

Scoring reads only files. To reproduce `report.json`, copy `run/` somewhere else,
delete the copy's `final-access.ledger` and `report.json`, and run from the
repository root:

```sh
uv run python -m reconcile.ml.run_v2 score --out COPY
uv run python -m reconcile.ml.run_v2 score --out COPY --final
```

This was checked before publishing: the regenerated `report.json` and ledger match
the published ones byte for byte. Re-running `prepare` and `interpret` needs the
isolated PostgreSQL test database and a DeepSeek key, and a new live pass is not
this run.
