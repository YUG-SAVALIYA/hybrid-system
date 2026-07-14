"""
MacroSearchBatchService

Provider-independent ingestion of macro search results into macro_search_batches.
Responsible for URL canonicalization, deduplication, invalid-result handling,
category/query preservation, counts, batch status, and discovery-database persistence.
"""
from __future__ import annotations

import logging
import copy
import datetime
from urllib.parse import urlparse, urlunparse
from typing import Dict, Any, List
from sqlalchemy.orm import Session

from models.discovery import MacroSearchBatch

logger = logging.getLogger(__name__)

PROVIDER_PARALLEL = "PARALLEL_AI_SEARCH"

BATCH_STATUS_PENDING = "PENDING"
BATCH_STATUS_COMPLETED = "COMPLETED"
BATCH_STATUS_COMPLETED_WITH_WARNINGS = "COMPLETED_WITH_WARNINGS"
BATCH_STATUS_FAILED = "FAILED"


def _canonical_url(url: str) -> str | None:
    """Lowercase scheme/host, strip fragment, normalise trailing slash."""
    try:
        p = urlparse(url.strip())
        if not p.scheme or not p.netloc:
            return None
        path = p.path.rstrip("/") or "/"
        canonical = urlunparse((p.scheme.lower(), p.netloc.lower(), path, p.params, p.query, ""))
        return canonical
    except Exception:
        return None


def _is_valid_result(result: Dict[str, Any]) -> bool:
    url = result.get("url", "")
    title = result.get("title", "")
    snippet = result.get("snippet", "")
    return bool(url and title and (snippet or result.get("excerpts")))


class MacroSearchBatchService:
    """
    Provider-independent service for ingesting macro search results.
    Callers (providers) pass normalized result dicts; this service handles
    deduplication, validation, persistence, and status management.
    """

    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def get_or_create_batch(self, run_id: str, provider: str, batch_id: str) -> MacroSearchBatch:
        """Return existing batch record or create a fresh PENDING one."""
        batch = self._disc.query(MacroSearchBatch).filter_by(id=batch_id).first()
        if not batch:
            batch = MacroSearchBatch(
                id=batch_id,
                run_id=run_id,
                provider=provider,
                status=BATCH_STATUS_PENDING,
                total_results=0,
                failed_categories=[],
                warnings=[],
                provider_metadata={},
                results=[]
            )
            self._disc.add(batch)
            self._disc.flush()
        return batch

    def ingest_category_results(
        self,
        batch: MacroSearchBatch,
        category: str,
        raw_results: List[Dict[str, Any]],
        provider_meta: Dict[str, Any],
        existing_urls: set
    ) -> int:
        """
        Validate, deduplicate, and append results for one category.
        Returns count of accepted results.
        """
        accepted = 0
        current_results = list(batch.results or [])
        current_warnings = list(batch.warnings or [])

        for r in raw_results:
            url = r.get("url", "")
            if not _is_valid_result(r):
                current_warnings.append(f"INVALID_RESULT_SKIPPED:{url or 'no-url'}")
                continue

            canon = _canonical_url(url)
            if not canon:
                current_warnings.append(f"INVALID_RESULT_SKIPPED:{url}")
                continue

            if canon in existing_urls:
                continue  # deduplicate
            existing_urls.add(canon)

            current_results.append({
                "category": category,
                "title": r.get("title"),
                "url": url,
                "canonical_url": canon,
                "source_name": r.get("source_name"),
                "snippet": r.get("snippet"),
                "published_date": r.get("published_date"),
                "publication_precision": r.get("publication_precision", "UNKNOWN"),
                "warnings": r.get("warnings", [])
            })
            accepted += 1

        # Merge provider meta per category
        pm = dict(batch.provider_metadata or {})
        pm[category] = provider_meta
        batch.provider_metadata = pm
        batch.results = current_results
        batch.warnings = current_warnings
        batch.total_results = len(current_results)
        return accepted

    def record_category_failure(
        self,
        batch: MacroSearchBatch,
        category: str,
        warning: str,
        error_detail: str
    ) -> None:
        """Record a single-category failure without killing the entire batch."""
        fc = list(batch.failed_categories or [])
        fc.append({"category": category, "warning": warning, "detail": error_detail})
        batch.failed_categories = fc
        ws = list(batch.warnings or [])
        ws.append(f"{warning}:{category}")
        batch.warnings = ws

    def finalize_batch(
        self,
        batch: MacroSearchBatch,
        total_categories: int
    ) -> None:
        """Set final status and persist."""
        failed_count = len(batch.failed_categories or [])
        if failed_count == 0:
            batch.status = BATCH_STATUS_COMPLETED
        elif failed_count < total_categories:
            batch.status = BATCH_STATUS_COMPLETED_WITH_WARNINGS
        else:
            batch.status = BATCH_STATUS_FAILED
        self._disc.commit()
