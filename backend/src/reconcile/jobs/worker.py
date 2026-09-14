from __future__ import annotations

import argparse

from reconcile.persistence.db import SessionLocal

from .queue import run_once


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one or more due Reconcile jobs")
    parser.add_argument("--loop", action="store_true", help="continue while jobs are available")
    args = parser.parse_args()
    while True:
        with SessionLocal() as session:
            job = run_once(session)
        if job is None or not args.loop:
            return


if __name__ == "__main__":
    main()
