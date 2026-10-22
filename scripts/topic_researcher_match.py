"""Topic-to-Researcher Match: for every Horizon Europe topic of a cluster, find the researchers of our
laboratories whose publications fit it, and write JSON / Markdown / PDF reports.

The topics come from the OpenSearch index of the work programmes, or from topics files (``--topics-file``): the
calls of any other programme (ERC, MSCA, EIC, missions, national calls…), e.g. exported by Druid. A file is one run,
labelled by its ``subset``; ``programme_label`` names the programme in the prompts.

    uv run python scripts/topic_researcher_match.py --list-clusters
    uv run python scripts/topic_researcher_match.py --cluster CL2 [--topic ID …] [--out DIR] [--min-score 0.35] [--max-researchers 15]
    uv run python scripts/topic_researcher_match.py --all
    uv run python scripts/topic_researcher_match.py --cluster CL2 --dry-run      # list the topics, no LLM / toolbox call
    uv run python scripts/topic_researcher_match.py --topics-file calls-eic-pathfinder.json [--topics-file …] [--topic ID …]

Requires TRM_MODEL (dedicated model), the OpenSearch topic index (HORIZON_OS_*, not with --topics-file), the embedding service
(EMBEDDING_*) and the MCP toolbox (CRISALID_MCP_TOOLBOX_*). Exit code 1 if any topic failed.
"""

import argparse
import asyncio
import logging
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from common.embedding import get_embedding_provider  # noqa: E402
from common.horizon.client import HorizonSettings, build_client  # noqa: E402
from common.horizon.search import HorizonSearch  # noqa: E402
from common.llm import build_chat_model  # noqa: E402
from trm.config import TRMSettings  # noqa: E402
from trm.llm import StructuredLLM  # noqa: E402
from trm.runner import load_topics_file, run_cluster, select_topics  # noqa: E402
from trm.sources import ToolboxResearcherSource  # noqa: E402


def _progress(index: int, total: int, result) -> None:
    status = f"FAILED: {result.error}" if result.error else f"{len(result.researchers)} researcher(s)"
    print(f"[{index}/{total}] {result.topic_id} — {status} ({result.duration_seconds}s)", flush=True)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list-clusters", action="store_true", help="list the clusters of the topic index and exit")
    parser.add_argument("--cluster", help="cluster to process, e.g. CL2")
    parser.add_argument("--all", action="store_true", help="process every cluster of the index")
    parser.add_argument("--topic", action="append", help="restrict to this topic id (repeatable)")
    parser.add_argument("--topics-file", action="append", type=Path,
                        help="JSON file of topics to process instead of the index, one run per file (repeatable)")
    parser.add_argument("--programme-label", help="programme named in the prompts (default: the file's, else TRM_PROGRAMME_LABEL)")
    parser.add_argument("--out", type=Path, help="report directory (default: TRM_OUTPUT_DIR or ./reports)")
    parser.add_argument("--min-score", type=float, help="normalised score threshold (default: TRM_MIN_SCORE)")
    parser.add_argument("--max-researchers", type=int, help="max researchers per topic (default: TRM_MAX_RESEARCHERS)")
    parser.add_argument("--max-candidates", type=int, help="candidates sent to verification (default: TRM_MAX_CANDIDATES)")
    parser.add_argument("--model", help="LLM for expansion and verification (default: TRM_MODEL)")
    parser.add_argument("--no-pdf", action="store_true", help="skip the PDF report")
    parser.add_argument("--dry-run", action="store_true", help="list the selected topics without running the pipeline")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("trm").setLevel(logging.INFO)

    os_settings = HorizonSettings.from_env()
    client = build_client(os_settings)
    embedder = get_embedding_provider()
    search = HorizonSearch(client, embedder, os_settings)
    try:
        if args.list_clusters:
            for row in await search.list_clusters():
                print(f"{row['cluster']:5s} {row['topics']:4d}  {row['label']}")
            return 0

        # One batch per cluster of the index, or per topics file: (label, topics or None to read from the index,
        # programme named in the prompts).
        batches: list[tuple[str | None, list[dict] | None, str | None]]
        if args.topics_file:
            if args.cluster or args.all:
                parser.error("--topics-file replaces --cluster / --all")
            batches = []
            for path in args.topics_file:
                file = load_topics_file(path)
                topics = [t for t in file.topics if not args.topic or t["topic_id"] in args.topic]
                batches.append((file.label, topics, file.programme_label))
        elif args.all:
            batches = [(row["cluster"], None, None) for row in await search.list_clusters()]
        elif args.cluster:
            batches = [(args.cluster, None, None)]
        elif args.topic:
            batches = [(None, None, None)]
        else:
            parser.error("give --cluster, --all, --topic, --topics-file or --list-clusters")

        settings = TRMSettings.from_env(
            model=args.model, min_score=args.min_score, max_researchers=args.max_researchers,
            max_candidates=args.max_candidates, output_dir=args.out,
        )
        if not settings.model and not args.dry_run:
            print("error: TRM_MODEL is not set (or pass --model)", file=sys.stderr)
            return 2

        failed = 0
        source = None
        for cluster, topics, programme_label in batches:
            if topics is None:
                topics = await select_topics(search, cluster, args.topic)
            label = cluster or "selected topics"
            batch_settings = replace(settings, programme_label=args.programme_label or programme_label or settings.programme_label)
            if args.dry_run:
                print(f"{label}: {len(topics)} topic(s)")
                for topic in topics:
                    print(f"  {topic['topic_id']}  {topic['title'][:90]}")
                continue
            if not topics:
                print(f"{label}: no topic")
                continue
            if source is None:
                source = ToolboxResearcherSource()
                llm = StructuredLLM(build_chat_model(settings.model), retries=settings.llm_retries)
            print(f"== {label}: {len(topics)} topic(s), model {settings.model}, programme {batch_settings.programme_label}")
            run, paths = await run_cluster(
                cluster or topics[0].get("cluster", "topics"), topics,
                llm=llm, embedder=embedder, source=source, settings=batch_settings, pdf=not args.no_pdf, progress=_progress,
            )
            failed += len(run.failed_topics)
            print(f"   done in {run.duration_seconds}s, {len(run.failed_topics)} failed; reports: "
                  + ", ".join(str(p) for p in paths.values()))
        if source is not None:
            await source.aclose()
        return 1 if failed else 0
    finally:
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
