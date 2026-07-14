"""
Tests for ParallelMacroSearchProvider and MacroSearchBatchService.

All tests are fully offline — no real Parallel API calls are made.
"""
import os
import uuid
import pytest
from unittest.mock import MagicMock, patch, call
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import MacroSearchBatch
from services.macro.macro_search_batch import MacroSearchBatchService, PROVIDER_PARALLEL
from services.macro.parallel_macro_search import (
    ParallelMacroSearchProvider,
    MACRO_CATEGORIES,
    TOTAL_CATEGORIES,
    _map_result,
    _source_name_from_url,
    W_CATEGORY_FAILED,
    W_RATE_LIMITED,
    W_AUTH_FAILED,
    W_TIMEOUT,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM macro_search_batches"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM macro_search_batches"))
    session.commit()
    session.close()


def _fake_result(url="https://rbi.org.in/report", title="RBI Report",
                 publish_date="2025-01-15", excerpts=None):
    r = MagicMock()
    r.url = url
    r.title = title
    r.publish_date = publish_date
    r.excerpts = excerpts or ["India RBI raised rates.", "Liquidity remained tight."]
    return r


def _fake_search_result(results=None, search_id="sid-123", session_id="sess-abc"):
    sr = MagicMock()
    sr.results = results or [_fake_result()]
    sr.search_id = search_id
    sr.session_id = session_id
    sr.usage = None
    sr.warnings = []
    return sr


def _make_provider(disc_session, env_key="test-key-xyz"):
    with patch.dict(os.environ, {"PARALLEL_API_KEY": env_key}):
        return ParallelMacroSearchProvider(disc_session)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_parallel_client_initialization(disc_session):
    """1. Correct Parallel client initialization."""
    provider = _make_provider(disc_session)
    with patch.dict(os.environ, {"PARALLEL_API_KEY": "my-key-abc"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            MockParallel.return_value = MagicMock()
            client = provider._get_client()
            MockParallel.assert_called_once_with(api_key="my-key-abc", max_retries=0)


def test_api_key_from_environment_only(disc_session):
    """2. API key is read only from environment; missing key raises ValueError."""
    provider = _make_provider(disc_session, env_key="")
    with patch.dict(os.environ, {}, clear=True):
        # Ensure PARALLEL_API_KEY is absent
        os.environ.pop("PARALLEL_API_KEY", None)
        with pytest.raises(ValueError, match="PARALLEL_API_KEY"):
            provider._get_client()


def test_exactly_four_category_requests(disc_session):
    """3. Exactly four category requests are made."""
    assert TOTAL_CATEGORIES == 4
    assert len(MACRO_CATEGORIES) == 4


def test_correct_category_objectives_and_queries(disc_session):
    """4. Correct category objectives and queries are set."""
    cats = {c["category"] for c in MACRO_CATEGORIES}
    assert "INTEREST_RATES_AND_LIQUIDITY" in cats
    assert "COMMODITY_AND_INPUT_COSTS" in cats
    assert "GOVERNMENT_POLICY_AND_SPENDING" in cats
    assert "DEMAND_CONDITIONS" in cats
    for cat in MACRO_CATEGORIES:
        assert len(cat["search_queries"]) >= 2
        assert len(cat["objective"]) > 20


def test_result_mapping_title_url_date_excerpts(disc_session):
    """5. Correct mapping of title, URL, date, and excerpts."""
    raw = _fake_result(
        url="https://rbi.org.in/report",
        title="RBI Monetary Report",
        publish_date="2025-06-01",
        excerpts=["Excerpt one.", "Excerpt two."]
    )
    mapped = _map_result(raw, "INTEREST_RATES_AND_LIQUIDITY")
    assert mapped["title"] == "RBI Monetary Report"
    assert mapped["url"] == "https://rbi.org.in/report"
    assert mapped["published_date"] == "2025-06-01"
    assert mapped["publication_precision"] == "DATE"
    assert mapped["category"] == "INTEREST_RATES_AND_LIQUIDITY"


def test_multiple_excerpts_joined(disc_session):
    """6. Multiple excerpts are joined with two newlines."""
    raw = _fake_result(excerpts=["First excerpt.", "Second excerpt.", "Third excerpt."])
    mapped = _map_result(raw, "DEMAND_CONDITIONS")
    assert mapped["snippet"] == "First excerpt.\n\nSecond excerpt.\n\nThird excerpt."


def test_missing_publication_date(disc_session):
    """7. Missing publication date → null published_date, UNKNOWN precision, warning."""
    raw = _fake_result(publish_date=None)
    mapped = _map_result(raw, "DEMAND_CONDITIONS")
    assert mapped["published_date"] is None
    assert mapped["publication_precision"] == "UNKNOWN"
    assert "PUBLICATION_DATE_UNAVAILABLE" in mapped["warnings"]


def test_hostname_derived_source_name(disc_session):
    """8. Hostname-derived source name is lowercase."""
    assert _source_name_from_url("https://RBI.org.in/path") == "rbi.org.in"
    assert _source_name_from_url("https://Mint.com/article") == "mint.com"
    assert _source_name_from_url("not-a-url") is None


def test_search_and_session_ids_preserved(disc_session):
    """9. Provider search_id and session_id are preserved in metadata."""
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)
    fake_sr = _fake_search_result(search_id="search-999", session_id="sess-abc")

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            mock_client = MagicMock()
            mock_client.search.return_value = fake_sr
            MockParallel.return_value = mock_client
            provider.fetch_macro_data(run_id)

    batch = disc_session.query(MacroSearchBatch).filter_by(run_id=run_id).first()
    assert batch is not None
    # Check one category meta
    first_cat = MACRO_CATEGORIES[0]["category"]
    meta = batch.provider_metadata.get(first_cat, {})
    assert meta["search_id"] == "search-999"
    assert meta["session_id"] == "sess-abc"


def test_one_category_failure_does_not_stop_others(disc_session):
    """10. One category failure does not stop other categories."""
    import httpx
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)

    call_count = 0
    def mock_search(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Network error on first call")
        return _fake_search_result()

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            mock_client = MagicMock()
            mock_client.search.side_effect = mock_search
            MockParallel.return_value = mock_client
            provider.fetch_macro_data(run_id)

    batch = disc_session.query(MacroSearchBatch).filter_by(run_id=run_id).first()
    assert batch.status == "COMPLETED_WITH_WARNINGS"
    assert len(batch.failed_categories) == 1
    assert batch.total_results > 0  # other categories succeeded


def test_every_category_failure_marks_failed(disc_session):
    """11. Every category failure → FAILED status."""
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            mock_client = MagicMock()
            mock_client.search.side_effect = Exception("All failed")
            MockParallel.return_value = mock_client
            provider.fetch_macro_data(run_id)

    batch = disc_session.query(MacroSearchBatch).filter_by(run_id=run_id).first()
    assert batch.status == "FAILED"
    assert len(batch.failed_categories) == TOTAL_CATEGORIES


def test_temporary_failures_use_bounded_retries(disc_session):
    """12. Temporary timeout/rate-limit errors use up to MAX_RETRIES retries."""
    import httpx
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)

    call_count = [0]
    def mock_search(**kwargs):
        call_count[0] += 1
        raise httpx.TimeoutException("Timeout")

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            with patch("services.macro.parallel_macro_search.time.sleep"):
                mock_client = MagicMock()
                mock_client.search.side_effect = mock_search
                MockParallel.return_value = mock_client
                provider.fetch_macro_data(run_id)

    # Each category gets 1 attempt + up to 2 retries = 3 calls per category
    assert call_count[0] == TOTAL_CATEGORIES * 3


def test_auth_failures_not_retried(disc_session):
    """13. Authentication failures (401/403) are not retried."""
    import httpx
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)

    call_count = [0]
    def mock_search(**kwargs):
        call_count[0] += 1
        response = MagicMock()
        response.status_code = 401
        raise httpx.HTTPStatusError("Unauthorized", request=MagicMock(), response=response)

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            with patch("services.macro.parallel_macro_search.time.sleep"):
                mock_client = MagicMock()
                mock_client.search.side_effect = mock_search
                MockParallel.return_value = mock_client
                provider.fetch_macro_data(run_id)

    # 401 should not retry — exactly 1 call then auth failure propagated
    assert call_count[0] == 1


def test_results_passed_into_batch_service(disc_session):
    """14. Results from Parallel are passed into MacroSearchBatchService."""
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            mock_client = MagicMock()
            mock_client.search.return_value = _fake_search_result(
                results=[_fake_result(), _fake_result(url="https://livemint.com/news")]
            )
            MockParallel.return_value = mock_client
            provider.fetch_macro_data(run_id)

    batch = disc_session.query(MacroSearchBatch).filter_by(run_id=run_id).first()
    assert batch is not None
    assert batch.total_results > 0


def test_repeated_runs_are_idempotent(disc_session):
    """15. Repeated execution for the same run_id updates existing batch, not duplicates."""
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            mock_client = MagicMock()
            mock_client.search.return_value = _fake_search_result()
            MockParallel.return_value = mock_client
            provider.fetch_macro_data(run_id)
            provider.fetch_macro_data(run_id)  # second run

    count = disc_session.query(MacroSearchBatch).filter_by(run_id=run_id).count()
    assert count == 1  # still one row


def test_no_llm_or_task_api_call(disc_session):
    """16. No LLM or Task API call occurs during provider execution."""
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            mock_client = MagicMock()
            mock_client.search.return_value = _fake_search_result()
            MockParallel.return_value = mock_client
            provider.fetch_macro_data(run_id)
            # Ensure only .search was called — no .task_run, .extract, etc.
            assert mock_client.search.called
            assert not mock_client.task_run.called
            assert not mock_client.extract.called


def test_api_key_never_persisted(disc_session):
    """17. API key is never stored in the database record."""
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)

    with patch.dict(os.environ, {"PARALLEL_API_KEY": "super-secret-key-999"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            mock_client = MagicMock()
            mock_client.search.return_value = _fake_search_result()
            MockParallel.return_value = mock_client
            provider.fetch_macro_data(run_id)

    batch = disc_session.query(MacroSearchBatch).filter_by(run_id=run_id).first()
    import json
    serialized = json.dumps({
        "provider_metadata": batch.provider_metadata,
        "results": batch.results,
        "warnings": batch.warnings,
    })
    assert "super-secret-key-999" not in serialized


def test_no_source_database_access(disc_session):
    """18. Provider never accesses the source database."""
    from database import source_engine
    accessed = []

    from sqlalchemy import event
    @event.listens_for(source_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        accessed.append(statement)

    run_id = f"run_{uuid.uuid4().hex[:6]}"
    provider = _make_provider(disc_session)
    with patch.dict(os.environ, {"PARALLEL_API_KEY": "k"}):
        with patch("services.macro.parallel_macro_search.Parallel") as MockParallel:
            mock_client = MagicMock()
            mock_client.search.return_value = _fake_search_result()
            MockParallel.return_value = mock_client
            provider.fetch_macro_data(run_id)

    # Remove listener
    from sqlalchemy import event as saevent
    saevent.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == [], f"Unexpected source DB queries: {accessed}"
