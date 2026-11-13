"""Index the Horizon Europe work programme topics of HORIZON_WP_DIR into OpenSearch.

    uv run python scripts/index_horizon_topics.py [--wp-dir DIR] [--file PDF …] [--recreate] [--dry-run] [--report out.json]

The index mirrors the directory: after a complete run, topics that are no longer in any file are removed.
Exit code 0 only if no error occurred (warnings are allowed).
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from common.embedding import get_embedding_provider  # noqa: E402
from common.horizon.client import HorizonSettings, build_client  # noqa: E402
from common.horizon.ingest import HorizonIngester  # noqa: E402
from common.horizon.mapping import embedding_dimensions  # noqa: E402


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--wp-dir", type=Path, default=Path(os.environ.get("HORIZON_WP_DIR", "data/cff/he/2026-27")))
    parser.add_argument("--file", type=Path, action="append", help="index only this PDF (repeatable); no stale sweep")
    parser.add_argument("--recreate", action="store_true", help="drop and rebuild the index and its pipelines")
    parser.add_argument("--dry-run", action="store_true", help="parse and report without touching OpenSearch")
    parser.add_argument("--report", type=Path, help="write the JSON report to this file")
    parser.add_argument("--issues", action="store_true", help="print every parse warning and error")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    if not args.wp_dir.is_dir() and not args.file:
        print(f"error: {args.wp_dir} is not a directory (set HORIZON_WP_DIR or --wp-dir)", file=sys.stderr)
        return 2

    settings = HorizonSettings.from_env()
    client = None if args.dry_run else build_client(settings)
    embedder = None if args.dry_run else get_embedding_provider()
    ingester = HorizonIngester(
        client,
        embedder,
        settings.index,
        embedding_model=getattr(embedder, "model_name", "") if embedder else "",
        dimensions=embedding_dimensions(),
    )
    try:
        report = await ingester.run(args.wp_dir, files=args.file, recreate=args.recreate, dry_run=args.dry_run)
    finally:
        if client is not None:
            await client.close()

    print(report.summary())
    if args.issues:
        for issue in report.issues:
            print(f"  [{issue['level']}] {issue['source_file']} p{issue['page']} {issue['topic_id'] or ''}: {issue['message']}")
    if args.report:
        args.report.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"report written to {args.report}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
