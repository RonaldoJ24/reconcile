# ML v1 development result

These results use deterministic, agent-generated synthetic labels. They are not
human-reviewed, permissioned customer data, or evidence of real-world model
quality. The final and sealed challenge targets were not evaluated in Phase 3.

The selected artifact is `ranker-ml-v1-logistic` version `1`. Logistic
regression and the bounded histogram gradient-boosting challenger tied on the
validation selection metrics, so the simpler logistic model won the fixed tie
break. The model SHA-256 is
`bb9774c99870bfa0d20759c6ceb37aafd6fbf65619b28c71947bdb993c16135e`.

| Path | Groups proposed | Correct proposals | Precision | Coverage | Exact allocation on answer-bearing groups |
| --- | ---: | ---: | ---: | ---: | ---: |
| Learned ranker, validation | 1,000 / 1,000 | 500 | 0.500 | 1.000 | 500 / 500 |
| Rules v1, validation | 500 / 1,000 | 400 | 0.800 | 0.500 | 400 / 500 |
| Learned ranker, development challenge | 30 / 30 | 15 | 0.500 | 1.000 | 15 / 15 |
| Rules v1, development challenge | 18 / 30 | 13 | 0.722 | 0.600 | 13 / 15 |
| Learned ranker, calibration diagnostic | 500 / 500 | 250 | 0.500 | 1.000 | 250 / 250 |

The learned ranker correctly orders the acceptable candidate whenever this
synthetic data names one, but it has no abstention threshold and proposes on all
underdetermined groups. Its validation incorrectly-allocated-value rate is
0.5354, versus 0.0656 for rules v1, using all payment value in each denominator.
It is therefore **not promoted** and remains opt-in shadow telemetry; rules v1
continues to own every reviewable proposal and all financial operations.

`training.json` and `development.json` contain complete denominators and
per-family diagnostics. Their evaluator latency fields measure post-score metric
overhead, not end-to-end inference. One observed local development-case runtime
prediction recorded model ID/version and took 10.406 ms; that single cold
observation is serving evidence, not a performance benchmark.
