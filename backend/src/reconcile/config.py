from __future__ import annotations

import os


def server_mode() -> str:
    mode = os.getenv("RECONCILE_MODE", "local").strip().lower()
    if mode not in {"local", "preview"}:
        raise RuntimeError("RECONCILE_MODE must be local or preview")
    return mode
