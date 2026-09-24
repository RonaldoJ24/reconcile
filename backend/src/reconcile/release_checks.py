"""Release artifact and tracked-secret checks with machine-readable output."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from reconcile.ml.artifact import load_artifact

SECRET_PATTERNS = {
    "api_key": re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "postgres_credential": re.compile(
        rb"postgres(?:ql)?(?:\+[a-z0-9]+)?://[^:/\s]+:[^@\s]+@", re.IGNORECASE
    ),
    "private_key": re.compile(rb"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "openai_assignment": re.compile(
        rb"OPENAI_API_KEY[ \t]*[:=][ \t]*['\"]?[^\s'\"#]+", re.IGNORECASE
    ),
}
REQUIRED_RUNTIME = {
    "backend/src/reconcile/api/",
    "backend/src/reconcile/domain/",
    "backend/src/reconcile/interpretation/",
    "backend/src/reconcile/persistence/",
    "artifacts/ranker-ml-v1/model.pkl",
    "artifacts/ranker-ml-v1/metadata.json",
}
FORBIDDEN_RUNTIME_PREFIXES = ("data/", "reports/", "evaluation/", "backend/tests/")


def _tracked_files(repo: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    return [repo / item.decode() for item in result.stdout.split(b"\0") if item]


def _release_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def scan_release(repo: Path) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    scanned = 0
    for path in _tracked_files(repo):
        try:
            payload = path.read_bytes()
        except OSError:
            findings.append({"kind": "unreadable", "path": str(path.relative_to(repo))})
            continue
        if b"\0" in payload[:8192]:
            continue
        scanned += 1
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(payload):
                findings.append({"kind": name, "path": str(path.relative_to(repo))})

    allowlist_path = repo / "deploy/runtime-allowlist.txt"
    entries = {
        line.strip()
        for line in allowlist_path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    missing_runtime = sorted(REQUIRED_RUNTIME - entries)
    forbidden_runtime = sorted(
        entry for entry in entries if entry.startswith(FORBIDDEN_RUNTIME_PREFIXES)
    )
    loaded = load_artifact()
    passed = not findings and not missing_runtime and not forbidden_runtime
    return {
        "schema_version": "release-scan-v1",
        "release_commit": _release_commit(repo),
        "passed": passed,
        "tracked_text_files_scanned": scanned,
        "secret_findings": findings,
        "runtime_allowlist": {
            "entries": len(entries),
            "missing_required": missing_runtime,
            "forbidden": forbidden_runtime,
        },
        "artifact": {
            "verified": True,
            "model_id": loaded.model_id,
            "model_version": loaded.model_version,
            "model_sha256": loaded.metadata["model_sha256"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan tracked release inputs")
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--report", type=Path, default=Path("reports/release-v1/scan.json"))
    args = parser.parse_args()
    payload = scan_release(args.repo.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    if not payload["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
