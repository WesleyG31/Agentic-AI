"""Run tenant-scoped action reconciliation locally or from a cron/systemd job."""

from __future__ import annotations

import argparse
import json

from kompass.actions.executor import SQLiteReceiptStore
from kompass.actions.postgres import PostgresReceiptStore
from kompass.actions.reconciliation import reconcile_receipts
from kompass.config import ROOT, settings
from kompass.runtime import RuntimeContext, runtime_scope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--older-than-seconds", type=float, default=60.0)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    store = (
        PostgresReceiptStore(settings.database_url)
        if settings.database_url
        else SQLiteReceiptStore(str(ROOT / settings.action_receipts_db))
    )
    context = RuntimeContext.create(
        tenant_id=args.tenant,
        user_id="local-reconciliation-job",
        principal_kind="service",
        scopes={"refunds:create", "tickets:write"},
    )
    with runtime_scope(context):
        report = reconcile_receipts(
            store,
            args.tenant,
            older_than_seconds=args.older_than_seconds,
            repair=not args.verify_only,
        )
    print(json.dumps(report.__dict__, indent=2))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
