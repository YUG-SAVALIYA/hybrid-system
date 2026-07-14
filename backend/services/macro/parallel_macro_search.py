"""
ParallelMacroSearchProvider

Fetches raw macro evidence from the Parallel Search API across four categories
and delegates normalized ingestion to MacroSearchBatchService.
"""
from __future__ import annotations

import logging
import os
import time
import datetime
from typing import Dict, Any, List
from urllib.parse import urlparse
from sqlalchemy.orm import Session

from parallel import Parallel  # top-level for testability
import config
from services.macro.macro_search_batch import MacroSearchBatchService, PROVIDER_PARALLEL

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────
PARALLEL_SEARCH_MODE = getattr(config, "PARALLEL_SEARCH_MODE", "advanced")
PARALLEL_MACRO_MAX_CHARS = int(getattr(config, "PARALLEL_MACRO_MAX_CHARS", 50000))

MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 2.0   # seconds
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# ── Macro categories ─────────────────────────────────────────────────────────
MACRO_CATEGORIES: List[Dict[str, Any]] = [
    {
        "category": "INTEREST_RATES_AND_LIQUIDITY",
        "objective": (
            "Find current and reliable information about Indian interest rates, banking liquidity, "
            "RBI policy, credit conditions, bond yields and monetary conditions. "
            "Prefer recent official, institutional and reputable financial sources."
        ),
        "search_queries": [
            "India RBI rates liquidity",
            "India monetary policy conditions",
            "India bond yields credit"
        ]
    },
    {
        "category": "COMMODITY_AND_INPUT_COSTS",
        "objective": (
            "Find current and reliable information about commodity prices and input cost pressures "
            "affecting Indian industries, including crude oil, metals, agricultural commodities, "
            "and global supply chain conditions. "
            "Prefer recent official, institutional and reputable financial sources."
        ),
        "search_queries": [
            "India commodity prices crude oil metals",
            "India input costs supply chain pressures",
            "Global commodity outlook India impact"
        ]
    },
    {
        "category": "GOVERNMENT_POLICY_AND_SPENDING",
        "objective": (
            "Find current and reliable information about Indian government fiscal policy, "
            "budget spending, sector subsidies, infrastructure investment, and regulatory changes "
            "that affect industry conditions. "
            "Prefer recent official, institutional and reputable financial sources."
        ),
        "search_queries": [
            "India government budget fiscal policy spending",
            "India sector policy regulation reform",
            "India infrastructure investment capex"
        ]
    },
    {
        "category": "DEMAND_CONDITIONS",
        "objective": (
            "Find current and reliable information about demand conditions in India including "
            "consumer sentiment, retail spending, corporate earnings trends, exports, "
            "and economic growth indicators. "
            "Prefer recent official, institutional and reputable financial sources."
        ),
        "search_queries": [
            "India consumer demand retail spending sentiment",
            "India GDP growth economic conditions",
            "India exports corporate earnings outlook"
        ]
    }
]

TOTAL_CATEGORIES = len(MACRO_CATEGORIES)

# ── Warning codes ─────────────────────────────────────────────────────────────
W_CATEGORY_FAILED    = "PARALLEL_CATEGORY_SEARCH_FAILED"
W_INVALID_RESPONSE   = "PARALLEL_RESPONSE_INVALID"
W_RATE_LIMITED       = "PARALLEL_RATE_LIMITED"
W_AUTH_FAILED        = "PARALLEL_AUTHENTICATION_FAILED"
W_TIMEOUT            = "PARALLEL_REQUEST_TIMEOUT"


def _source_name_from_url(url: str) -> str | None:
    """Extract lowercase hostname as source name."""
    try:
        return urlparse(url).hostname.lower() if urlparse(url).hostname else None
    except Exception:
        return None


def _map_result(raw, category: str) -> Dict[str, Any]:
    """Map one WebSearchResult to the normalized dict expected by MacroSearchBatchService."""
    url = getattr(raw, "url", None) or ""
    title = getattr(raw, "title", None) or ""
    publish_date = getattr(raw, "publish_date", None)
    excerpts = getattr(raw, "excerpts", None) or []

    snippet = "\n\n".join(e for e in excerpts if e) if excerpts else ""

    warnings = []
    if publish_date:
        published_date = str(publish_date)
        pub_precision = "DATE"
    else:
        published_date = None
        pub_precision = "UNKNOWN"
        warnings.append("PUBLICATION_DATE_UNAVAILABLE")

    return {
        "category": category,
        "title": title,
        "url": url,
        "source_name": _source_name_from_url(url),
        "snippet": snippet,
        "excerpts": list(excerpts),
        "published_date": published_date,
        "publication_precision": pub_precision,
        "warnings": warnings
    }


class ParallelMacroSearchProvider:
    """
    Fetches macro evidence via the Parallel Search API.
    Delegates normalization, deduplication, and persistence to MacroSearchBatchService.
    Never reads from the source database.
    """

    def __init__(self, discovery_session: Session):
        self._disc = discovery_session
        self._batch_svc = MacroSearchBatchService(discovery_session)

    def _get_client(self) -> Parallel:
        """Instantiate Parallel client with API key from environment only."""
        api_key = os.environ.get("PARALLEL_API_KEY")
        if not api_key:
            raise ValueError("PARALLEL_API_KEY environment variable is not set")
        return Parallel(api_key=api_key, max_retries=0)  # retries handled manually

    def _call_with_retry(self, client, session_id: str, cat_cfg: Dict[str, Any]) -> Any:
        """Execute one category search with bounded retry for transient errors."""
        import httpx

        category = cat_cfg["category"]
        last_exc = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                result = client.search(
                    objective=cat_cfg["objective"],
                    search_queries=cat_cfg["search_queries"],
                    mode=PARALLEL_SEARCH_MODE,
                    max_chars_total=PARALLEL_MACRO_MAX_CHARS,
                    session_id=session_id
                )
                return result
            except httpx.TimeoutException as exc:
                last_exc = (W_TIMEOUT, str(exc))
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_BASE ** (attempt + 1))
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 401 or code == 403:
                    raise  # Authentication: do not retry
                if code == 429:
                    last_exc = (W_RATE_LIMITED, f"HTTP {code}")
                else:
                    last_exc = (W_CATEGORY_FAILED, f"HTTP {code}")
                if code in RETRYABLE_STATUS_CODES and attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_BASE ** (attempt + 1))
                else:
                    break
            except Exception as exc:
                last_exc = (W_CATEGORY_FAILED, str(exc))
                break

        return last_exc  # Return (warning_code, detail) tuple on final failure

    def fetch_macro_data(self, run_id: str) -> str:
        """
        Execute all four category searches, ingest results, and return the batch ID.
        Idempotent: repeated calls for the same run_id update the existing batch.
        """
        batch_id = f"macro-parallel-{run_id}"
        session_id = f"macro-{run_id}"

        batch = self._batch_svc.get_or_create_batch(run_id, PROVIDER_PARALLEL, batch_id)

        # Reset for idempotent re-run
        batch.results = []
        batch.failed_categories = []
        batch.warnings = []
        batch.total_results = 0
        batch.provider_metadata = {}
        self._disc.flush()

        existing_urls: set = set()

        try:
            client = self._get_client()
        except ValueError as exc:
            # API key not set
            for cat_cfg in MACRO_CATEGORIES:
                self._batch_svc.record_category_failure(
                    batch, cat_cfg["category"], W_AUTH_FAILED, str(exc)
                )
            self._batch_svc.finalize_batch(batch, TOTAL_CATEGORIES)
            logger.error("Parallel API key not configured: %s", exc)
            return batch_id

        for cat_cfg in MACRO_CATEGORIES:
            category = cat_cfg["category"]
            retrieved_at = datetime.datetime.utcnow().isoformat()

            try:
                outcome = self._call_with_retry(client, session_id, cat_cfg)
            except Exception as exc:
                # Auth errors bubble up here
                status_code = None
                try:
                    import httpx
                    if isinstance(exc, httpx.HTTPStatusError):
                        status_code = exc.response.status_code
                except Exception:
                    pass
                warning = W_AUTH_FAILED if status_code in (401, 403) else W_CATEGORY_FAILED
                self._batch_svc.record_category_failure(batch, category, warning, str(exc))
                if status_code in (401, 403):
                    # Auth failure: fail remaining categories immediately
                    for remaining in MACRO_CATEGORIES:
                        if remaining["category"] != category and not any(
                            f["category"] == remaining["category"]
                            for f in (batch.failed_categories or [])
                        ):
                            self._batch_svc.record_category_failure(
                                batch, remaining["category"], W_AUTH_FAILED, "Skipped due to auth failure"
                            )
                    break
                continue

            # outcome is either a SearchResult or a (warning, detail) tuple
            if isinstance(outcome, tuple):
                warning_code, detail = outcome
                self._batch_svc.record_category_failure(batch, category, warning_code, detail)
                continue

            # Valid SearchResult
            search_result = outcome
            try:
                raw_items = getattr(search_result, "results", []) or []
                mapped = [_map_result(r, category) for r in raw_items]
            except Exception as exc:
                self._batch_svc.record_category_failure(batch, category, W_INVALID_RESPONSE, str(exc))
                continue

            usage = getattr(search_result, "usage", None)
            provider_warnings = getattr(search_result, "warnings", None)
            meta = {
                "category": category,
                "objective": cat_cfg["objective"],
                "search_queries": cat_cfg["search_queries"],
                "search_id": getattr(search_result, "search_id", None),
                "session_id": getattr(search_result, "session_id", None),
                "retrieved_at": retrieved_at,
                "usage": usage.model_dump() if hasattr(usage, "model_dump") else dict(usage) if usage else None,
                "provider_warnings": list(provider_warnings) if provider_warnings else []
            }

            self._batch_svc.ingest_category_results(
                batch, category, mapped, meta, existing_urls
            )

        self._batch_svc.finalize_batch(batch, TOTAL_CATEGORIES)
        return batch_id
