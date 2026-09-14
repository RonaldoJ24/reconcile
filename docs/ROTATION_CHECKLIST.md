# Deferred credential rotation

Rotate these credentials after the current release work is complete:

- [ ] Replace and revoke the DeepSeek API key that was pasted into the project
  conversation. Update only the supported local or deployment secret store, verify
  a minimal authenticated catalog request, and never copy the value into Git,
  frontend configuration, commands, reports, or logs.
- [ ] Reset the role credential for the isolated Reconcile Neon test branch after
  destructive Phase 5 testing is finished. Update dependent local secret storage,
  verify a direct test connection, then invalidate the prior credential.

Status: deliberately deferred at the owner's request. No secret values are recorded
here.
