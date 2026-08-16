"""Small dependency-free local load scenario for health, OIDC, and PostgreSQL reads."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from collections import Counter
from pathlib import Path
from statistics import mean

import httpx


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


async def _token(oidc_base: str, username: str, password: str) -> str:
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{oidc_base.rstrip('/')}/realms/kompass/protocol/openid-connect/token",
            data={
                "client_id": "kompass-api",
                "username": username,
                "password": password,
                "grant_type": "password",
            },
        )
        response.raise_for_status()
        return str(response.json()["access_token"])


async def run(args: argparse.Namespace) -> dict:
    token = await _token(args.oidc_base, args.username, args.password)
    semaphore = asyncio.Semaphore(args.concurrency)
    timings: list[float] = []
    statuses: Counter[int] = Counter()
    endpoints: Counter[str] = Counter()
    failures: list[str] = []
    started = time.monotonic()

    async with httpx.AsyncClient(base_url=args.base_url, timeout=20) as client:

        async def one(index: int) -> None:
            endpoint = "health" if index % 5 == 0 else "run_inspection"
            path = "/health" if endpoint == "health" else f"/runs/load-{index}"
            headers = {} if endpoint == "health" else {"Authorization": f"Bearer {token}"}
            async with semaphore:
                before = time.monotonic()
                try:
                    response = await client.get(path, headers=headers)
                    elapsed = (time.monotonic() - before) * 1_000
                    timings.append(elapsed)
                    statuses[response.status_code] += 1
                    endpoints[endpoint] += 1
                    if response.status_code != 200:
                        failures.append(f"{endpoint}:{response.status_code}")
                    elif endpoint == "run_inspection" and response.json()["status"] != "not_found":
                        failures.append(f"{endpoint}:unexpected-state")
                except httpx.HTTPError as exc:
                    timings.append((time.monotonic() - before) * 1_000)
                    failures.append(f"{endpoint}:{type(exc).__name__}")

        await asyncio.gather(*(one(index) for index in range(args.requests)))
    duration = time.monotonic() - started
    return {
        "scenario": "local_oidc_postgres_read_path",
        "requests": args.requests,
        "concurrency": args.concurrency,
        "completed": args.requests - len(failures),
        "errors": len(failures),
        "status_codes": dict(sorted(statuses.items())),
        "endpoints": dict(endpoints),
        "latency_ms": {
            "mean": round(mean(timings), 3),
            "p50": round(_percentile(timings, 0.50), 3),
            "p95": round(_percentile(timings, 0.95), 3),
            "max": round(max(timings), 3),
        },
        "throughput_rps": round(args.requests / duration, 3),
        "duplicate_effects": 0,
        "duplicate_check": "read-only scenario; action concurrency is covered separately",
        "failure_samples": failures[:10],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--oidc-base", default="http://127.0.0.1:8081")
    parser.add_argument("--username", default="alice")
    parser.add_argument(
        "--password", default=os.getenv("KOMPASS_LOAD_PASSWORD", "local-alice-password")
    )
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1 or args.concurrency > 64:
        parser.error("requests must be positive and concurrency must be between 1 and 64")
    report = asyncio.run(run(args))
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
