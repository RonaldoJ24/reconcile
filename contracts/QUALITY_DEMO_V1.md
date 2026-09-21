# Reconcile quality/demo correction contract

Frozen from reference commit `c19034dd9e3644c4151805cfa257a3085f37cd97`.
This contract refines the v1 transport without changing authoritative financial
semantics or the provider budget boundary.

## Money and review

- API allocation amounts remain JSON integers in centavos. Editable form amounts
  are decimal MXN strings and convert exactly once at submission. The UI never
  infers a unit from a JavaScript value's type or decimal point.
- A review draft is separate from the persisted proposal revision. Applying is
  unavailable while the draft differs from that revision or is invalid.
- One logical apply attempt keeps one idempotency key across an uncertain network
  result. A changed payload starts a new attempt after validation.
- Confirmation names the persisted revision, cash, credit, and projected invoice
  balances and states that Reconcile records an allocation rather than moving
  money.

## Import snapshot and matching

- A validation response belongs to an input-generation identifier. Changing any
  file or context immediately clears the result, and an older response cannot
  restore it.
- Rules propose only for bounded, unambiguous positive invoice instructions.
  Negation, contradiction, prompt-like instructions, or uncertain scope abstain
  with an explicit reason. Evidence quotes retain exact contextual source spans.
- The corrected rules identity is `rules-v2-conservative`; historical rules-v1
  and ranker metrics remain labeled historical and are not reinterpreted.

## Demo, capabilities, and compatibility

- `Open sample workspace` invokes backend-owned, versioned synthetic cases through
  the same validation, commit, job, matching, and query services as manual import.
  It is workspace-isolated and idempotent; React contains no proposal answers.
- Session responses expose server-derived `interpret`, `correct`, `apply`, and
  `reverse` capabilities. Backend authorization and state validation remain
  authoritative.
- Existing v1 endpoints and centavo payloads remain compatible. New response
  fields/endpoints are additive. No schema migration or provider call is required.
- Evaluation presentation reads a sanitized, versioned summary. It labels active
  rules, historical measurements, the experimental shadow ranker, and disabled
  provider status without claiming new model quality.
