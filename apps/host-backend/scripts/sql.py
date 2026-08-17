"""Run ad-hoc SQL against the configured database.

Uses the app's own settings, so it always targets whatever DATABASE_URL points
at — local Docker today, a managed instance later, with no edits here.

Usage
-----
    uv run python scripts/sql.py "SELECT email FROM users LIMIT 5"
    uv run python scripts/sql.py -f query.sql
    uv run python scripts/sql.py            # interactive; blank line runs

Parameters use :name placeholders, never string interpolation:

    uv run python scripts/sql.py "SELECT * FROM users WHERE email = :e" -p e=a@b.com
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Make `app` importable when run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402

MAX_COL_WIDTH = 48


async def run(sql: str, params: dict[str, str]) -> None:
    async with SessionLocal() as session:
        result = await session.execute(text(sql), params)

        if not result.returns_rows:
            # INSERT/UPDATE/DELETE/DDL: commit, since nothing else will.
            await session.commit()
            print(f"OK. {result.rowcount} row(s) affected.")
            return

        rows = result.fetchall()
        if not rows:
            print("(0 rows)")
            return

        _print_table(list(result.keys()), rows)
        print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")


def _print_table(headers: list[str], rows: list) -> None:
    cells = [[_fmt(v) for v in row] for row in rows]
    widths = [
        min(max(len(h), *(len(r[i]) for r in cells)), MAX_COL_WIDTH)
        for i, h in enumerate(headers)
    ]

    print(" | ".join(h[:w].ljust(w) for h, w in zip(headers, widths, strict=True)))
    print("-+-".join("-" * w for w in widths))
    for row in cells:
        print(" | ".join(c[:w].ljust(w) for c, w in zip(row, widths, strict=True)))


def _fmt(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, memoryview | bytes):
        # Encrypted columns are bytea; show a marker rather than binary noise.
        return f"<{len(value)} bytes, encrypted>"
    return str(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run SQL against the app database.")
    parser.add_argument("sql", nargs="?", help="SQL to execute")
    parser.add_argument("-f", "--file", help="read SQL from a file")
    parser.add_argument(
        "-p",
        "--param",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="bind a :name parameter (repeatable)",
    )
    args = parser.parse_args()

    params = dict(p.split("=", 1) for p in args.param)

    if args.file:
        sql = Path(args.file).read_text(encoding="utf-8")
    elif args.sql:
        sql = args.sql
    else:
        host = settings.database_url.split("@")[-1]
        print(f"Connected to {host}")
        print("Enter SQL; a blank line runs it. Ctrl-C to quit.\n")
        lines: list[str] = []
        try:
            while True:
                line = input("  " if lines else "> ")
                if line.strip() == "" and lines:
                    break
                if line.strip():
                    lines.append(line)
        except (KeyboardInterrupt, EOFError):
            print()
            return
        sql = "\n".join(lines)

    if not sql.strip():
        return

    try:
        asyncio.run(_main(sql, params))
    except Exception as exc:  # noqa: BLE001
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)


async def _main(sql: str, params: dict[str, str]) -> None:
    try:
        await run(sql, params)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    main()
