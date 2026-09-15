# Deferred credential rotation

Rotate these credentials after the current release work is complete:

- [ ] Replace and revoke the DeepSeek API key that was pasted into the project
  conversation. Update only the supported local or deployment secret store, verify
  a minimal authenticated catalog request, and never copy the value into Git,
  frontend configuration, commands, reports, or logs.
- [x] Reset the role credential for the isolated Reconcile Neon test branch after
  destructive Phase 5 testing is finished. Update dependent local secret storage,
  verify a direct test connection, then invalidate the prior credential.

Neon rotation completed on 2026-09-15: the replacement made a direct test
connection and the prior credential failed authentication. DeepSeek revocation is
still pending because the signed-in browser was locked at the rotation step. No
secret values are recorded here.
