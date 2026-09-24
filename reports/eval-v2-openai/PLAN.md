# Model swap on the v2 cases: plan

Written on 2026-09-23, before the run. The owner switched the runtime model from
DeepSeek to OpenAI `gpt-6-luna` and asked for the same cases to be run on it, so
that published results describe the model the product uses.

## Question

With everything else unchanged, how does `gpt-6-luna` compare with `deepseek-flash`
on the same 178 cases?

## System under test

The runner freezes the commit that adds this file. Rules (`rules-v2-conservative`),
retrieval, candidate generation, prompt `reconcile-interpretation-prompt-v3`, schema
`reconcile-interpretation-schema-v1` and production validation are the ones measured
in v2 at `c4524a2`. Only the provider changes: OpenAI Chat Completions with
`gpt-6-luna`, reasoning effort `none`, temperature 0, JSON output and at most 2,048
output tokens.

## Cases

The same 178 cases from `data/eval-v2/`, with the same two parser exclusions and the
same `stable_split` assignment. These cases are public, and their v2 errors have been
read. This is a model comparison on a known set, not a new held-out evaluation.
Neither model was tuned on these cases, and nothing in the prompt, rules, retrieval
or candidates changed after reading them.

## Methods and run rules

- Rules alone, the shadow ranker (threshold chosen on validation by the same rule)
  and rules then Direct, exactly as registered for v2.
- One live pass. Recorded cases are not called again, provider failures are recorded
  as unavailable and not retried, and the evaluator verifies every recording.
- Budget cap: US$2.00 for the run, expected to use about US$0.02.

## Reporting

- The held-out split and all 178 cases are reported for both models side by side,
  with the v2 metrics and 95% Wilson intervals on precision.
- With about 75 proposals per model, small differences are within noise and are
  described that way.
- The README and landing page headline will use the measured `gpt-6-luna` numbers,
  because that is the model the product runs, even if they are lower than DeepSeek's.
