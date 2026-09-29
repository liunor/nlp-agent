"""Manual smoke test script for academic search without external CI dependency.

Usage:
    uv run python scripts/smoke_academic_search.py --query "Attention Is All You Need"
    uv run python scripts/smoke_academic_search.py --query "LoRA Low-Rank Adaptation"
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import sys

# Ensure repository root is on sys.path
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.tool_config import load_agent_runtime_config
from server.tools.academic.contracts import AcademicSearchInput
from server.tools.academic.runtime import create_academic_redis_store
from server.tools.academic.service import AcademicSearchService


async def run_smoke(
    query: str,
    query_en: str | None = None,
    max_results: int = 5,
    expected_arxiv_id: str | None = None,
) -> int:
    config = load_agent_runtime_config().tools.academic
    shared_store = create_academic_redis_store(config.reliability)
    service = AcademicSearchService(config, shared_store=shared_store)

    request = AcademicSearchInput(
        query=query,
        query_en=query_en,
        max_results=max_results,
    )

    print(f"=== Running Academic Search Smoke Test ===")
    print(f"Query: {query}")
    if query_en:
        print(f"Query (EN): {query_en}")
    if expected_arxiv_id:
        print(f"Expected arXiv ID: {expected_arxiv_id}")

    try:
        response = await service.search(request)
    except Exception as err:
        print(f"[ERROR] Service search raised unexpected exception: {err}", file=sys.stderr)
        return 1
    finally:
        if shared_store is not None:
            await shared_store.close()

    print(f"Query Used: {response.query_used}")
    print(f"Partial Result: {response.partial}")
    print(f"Retrieved At: {response.retrieved_at.isoformat()}")

    print("\n--- Source Statuses ---")
    any_ok = False
    for st in response.source_status:
        print(f"  [{st.source}] status={st.status} message={st.message or 'N/A'}")
        if st.status == "ok":
            any_ok = True

    print(f"\n--- Found {len(response.papers)} Paper(s) ---")
    for i, paper in enumerate(response.papers, 1):
        author_names = ", ".join(a.name for a in paper.authors[:4])
        if len(paper.authors) > 4:
            author_names += f" et al. ({len(paper.authors)} total)"
        print(f"  [{i}] {paper.title}")
        print(f"      Authors: {author_names}")
        print(f"      Status: {paper.publication_status}")
        if paper.venue:
            print(f"      Venue: {paper.venue}")
        print(f"      First Submitted: {paper.first_submitted_at or 'N/A'}")
        print(f"      Published At: {paper.published_at or 'N/A'}")
        print(f"      Landing URL: {paper.landing_url}")
        print(f"      PDF URL: {paper.pdf_url or 'N/A'}")
        if paper.identifiers.arxiv_id:
            print(f"      arXiv ID: {paper.identifiers.arxiv_id}")
        if paper.identifiers.doi:
            print(f"      DOI: {paper.identifiers.doi}")
        if paper.identifiers.acl_id:
            print(f"      ACL ID: {paper.identifiers.acl_id}")
        if paper.identifiers.semantic_scholar_id:
            print(f"      S2 ID: {paper.identifiers.semantic_scholar_id}")

    if response.warnings:
        print("\n--- Warnings ---")
        for warn in response.warnings:
            print(f"  * {warn}")

    # Check that secrets are not accidentally exposed in output
    for env_var in ["DEEPSEEK_API_KEY", "QWEN_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"]:
        val = os.environ.get(env_var)
        if val and val in str(response):
            print(f"[SECURITY ALERT] Secret {env_var} leaked in search response!", file=sys.stderr)
            return 2

    if not any_ok:
        print("[ERROR] Smoke test failed: No sources completed with 'ok' status.", file=sys.stderr)
        return 1

    if not response.papers:
        print("[ERROR] Smoke test failed: Response contains zero papers.", file=sys.stderr)
        return 1

    if expected_arxiv_id:
        found_ids = [p.identifiers.arxiv_id for p in response.papers if p.identifiers.arxiv_id]
        if expected_arxiv_id not in found_ids:
            print(
                f"[ERROR] Smoke test failed: Expected arXiv ID {expected_arxiv_id!r} not in {found_ids}",
                file=sys.stderr,
            )
            return 1

    print("\n=== Smoke Test Completed Successfully ===")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Academic search smoke test")
    parser.add_argument("--query", "-q", required=True, help="Paper title or topic to search")
    parser.add_argument("--query-en", help="Optional English query")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="Max results (1-10)")
    parser.add_argument(
        "--expected-arxiv-id",
        help="Optional expected arXiv ID (e.g. 1706.03762) that must be present in results",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(
        run_smoke(args.query, args.query_en, args.max_results, args.expected_arxiv_id)
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
