# Browser acceptance test

The Playwright spec intentionally calls the real HTTP API through the running
frontend; it does not mock transport or provide scenario answers. Start the
backend and frontend, then run:

```sh
RECONCILE_API_URL=http://localhost:8000 pnpm --dir frontend dev
E2E_BASE_URL=http://localhost:4173 pnpm --dir frontend e2e
```

For a deployed same-origin preview, set only `E2E_BASE_URL`. The test creates a
new browser session and uses the synthetic packet under `fixtures/`.
