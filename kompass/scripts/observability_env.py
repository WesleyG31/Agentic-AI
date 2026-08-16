"""Generate local Langfuse secrets and connect the Kompass SDK configuration."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path

from kompass.config import ROOT


def _upsert(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pending = dict(values)
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in pending and not line.lstrip().startswith("#"):
            output.append(f"{key}={pending.pop(key)}")
        else:
            output.append(line)
    if pending:
        if output and output[-1]:
            output.append("")
        output.append("# Langfuse observability (generated locally)")
        output.extend(f"{key}={value}" for key, value in pending.items())
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _read_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return {
        key.strip(): value.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line and not line.lstrip().startswith("#") and "=" in line
        for key, value in [line.split("=", 1)]
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--force",
        action="store_true",
        help="rotate credentials (only after deleting the previous local stack data)",
    )
    args = parser.parse_args()
    stack_path = ROOT / ".env.observability"
    current = {} if args.force else _read_values(stack_path)
    generated = {
        "LANGFUSE_NEXTAUTH_SECRET": secrets.token_urlsafe(32),
        "LANGFUSE_SALT": secrets.token_urlsafe(24),
        "LANGFUSE_ENCRYPTION_KEY": secrets.token_hex(32),
        "LANGFUSE_POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "LANGFUSE_CLICKHOUSE_PASSWORD": secrets.token_urlsafe(24),
        "LANGFUSE_REDIS_PASSWORD": secrets.token_urlsafe(24),
        "LANGFUSE_MINIO_PASSWORD": secrets.token_urlsafe(24),
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "lf_pk_" + secrets.token_hex(16),
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "lf_sk_" + secrets.token_hex(24),
        "LANGFUSE_INIT_USER_EMAIL": "demo@kompass.local",
        "LANGFUSE_INIT_USER_NAME": "Kompass Developer",
        "LANGFUSE_INIT_USER_PASSWORD": secrets.token_urlsafe(18),
    }
    stack = {key: current.get(key) or value for key, value in generated.items()}
    public_key = stack["LANGFUSE_INIT_PROJECT_PUBLIC_KEY"]
    secret_key = stack["LANGFUSE_INIT_PROJECT_SECRET_KEY"]
    login_password = stack["LANGFUSE_INIT_USER_PASSWORD"]
    _upsert(stack_path, stack)
    _upsert(
        ROOT / ".env",
        {
            "LANGFUSE_ENABLED": "true",
            "LANGFUSE_BASE_URL": "http://localhost:3000",
            "LANGFUSE_PUBLIC_KEY": public_key,
            "LANGFUSE_SECRET_KEY": secret_key,
            "LANGFUSE_TRACING_ENVIRONMENT": "development",
            "LANGFUSE_RELEASE": "local",
            "LANGFUSE_MASK_PII": "true",
        },
    )
    action = "Rotated" if args.force else "Created/reused"
    print(f"{action} .env.observability and updated .env.")
    print("Langfuse login: demo@kompass.local")
    print(f"Langfuse password: {login_password}")
    print("Keep that password local; both files are ignored by Git.")


if __name__ == "__main__":
    main()
