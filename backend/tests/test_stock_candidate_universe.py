"""Offline tests for selected basic-industry stock candidate universe creation."""
import datetime
import uuid

import pytest
from sqlalchemy import event, inspect, text

from database import DiscoverySessionLocal, discovery_engine, source_engine
from models.discovery import (
    CompanyFundamentalMetric,
    CompanyTechnicalMetric,
    DiscoverySelection,
    EligibleUniverseSnapshot,
    StockCandidateSnapshot,
)
from services.stock.stock_candidate_universe import (
    STATUS_BOTH_UNAVAILABLE,
    STATUS_ELIGIBLE,
    STATUS_FUNDAMENTAL_UNAVAILABLE,
    STATUS_TECHNICAL_UNAVAILABLE,
    StockCandidateUniverseService,
    W_EMPTY,
    W_SELECTED_UNAVAILABLE,
    W_STALE_REMOVED,
)


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM stock_candidate_snapshots"))
    session.execute(text("DELETE FROM discovery_selections"))
    session.execute(text("DELETE FROM company_technical_metrics"))
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.execute(text("DELETE FROM eligible_universe_snapshots"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM stock_candidate_snapshots"))
    session.execute(text("DELETE FROM discovery_selections"))
    session.execute(text("DELETE FROM company_technical_metrics"))
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.execute(text("DELETE FROM eligible_universe_snapshots"))
    session.commit()
    session.close()


def _make_selection(
    session,
    run_id,
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    horizon="SHORT",
    selected=True,
):
    row = DiscoverySelection(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon=horizon,
        entity_type="BASIC_INDUSTRY",
        entity_name=basic,
        parent_sector=sector,
        parent_industry=industry,
        rank=1,
        final_score=90.0,
        selected=selected,
    )
    session.add(row)
    session.commit()
    return row


def _make_technical(
    session,
    run_id,
    company_id="c1",
    symbol="AAA",
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    horizon="SHORT",
    return_available=True,
    coverage=100.0,
    benchmark=True,
    status="AVAILABLE",
):
    row = CompanyTechnicalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=company_id,
        symbol=symbol,
        sector=sector,
        industry=industry,
        basic_industry=basic,
        horizon=horizon,
        as_of_date=datetime.date(2026, 7, 13),
        company_candle_date="2026-07-13",
        benchmark_candle_date="2026-07-13" if benchmark else None,
        current_close=100.0,
        start_close=90.0,
        company_return=0.11,
        benchmark_current_close=1000.0,
        benchmark_start_close=950.0,
        benchmark_return=0.05,
        relative_return=0.06,
        average_volume_current=1000.0,
        average_volume_previous=900.0,
        volume_change=0.1,
        positive_period_ratio=0.6,
        benchmark_outperformance_ratio=0.6,
        company_consistency_score=0.7,
        return_available=return_available,
        volume_available=True,
        consistency_available=True,
        data_coverage=coverage,
        warnings=[],
        calculation_details={"status": status},
    )
    session.add(row)
    session.commit()
    return row


def _make_fundamental(
    session,
    run_id,
    company_id="c1",
    symbol="AAA",
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    score=80.0,
    coverage=100.0,
    eligible=True,
):
    row = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=company_id,
        symbol=symbol,
        sector=sector,
        industry=industry,
        basic_industry=basic,
        growth_score=80.0,
        profitability_score=80.0,
        financial_strength_score=80.0,
        earnings_quality_score=80.0,
        final_fundamental_score=score,
        fundamental_status="AVAILABLE",
        fundamental_eligible_for_selection=eligible,
        benchmark_level_used="BASIC_INDUSTRY",
        data_coverage=coverage,
        unavailable_fields=[],
        calculation_details={"keep": True},
    )
    session.add(row)
    session.commit()
    return row


def _make_snapshot(
    session,
    run_id,
    company_id="c1",
    symbol="AAA",
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    horizon="SHORT",
):
    row = EligibleUniverseSnapshot(
        id=str(uuid.uuid4()),
        run_id=run_id,
        as_of_date=datetime.date(2026, 7, 13),
        horizon=horizon,
        source_company_id=company_id,
        symbol=symbol,
        sector=sector,
        industry=industry,
        basic_industry=basic,
        return_available=True,
        volume_available=True,
        consistency_available=True,
        financial_data_available=True,
        technical_data_coverage=1.0,
        fundamental_data_coverage=1.0,
        eligible_for_sector=True,
        eligible_for_industry=True,
        eligible_for_basic_industry=True,
        exclusion_reasons=[],
    )
    session.add(row)
    session.commit()
    return row


def _setup_eligible(session, run_id, horizon="SHORT", company_id="c1", symbol="AAA"):
    _make_selection(session, run_id, horizon=horizon)
    tech = _make_technical(session, run_id, company_id=company_id, symbol=symbol, horizon=horizon)
    fund = _make_fundamental(session, run_id, company_id=company_id, symbol=symbol)
    return tech, fund


def _candidates(session, run_id, horizon="SHORT"):
    return (
        session.query(StockCandidateSnapshot)
        .filter_by(run_id=run_id, horizon=horizon)
        .order_by(StockCandidateSnapshot.symbol.asc(), StockCandidateSnapshot.company_id.asc())
        .all()
    )


def test_active_basic_industry_selection_is_loaded_by_horizon(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id, sector="Tech", industry="Software", basic="Enterprise", horizon="MID")

    service = StockCandidateUniverseService(disc_session)

    assert service._selected_hierarchy(run_id, "MID") == {
        "sector": "Tech",
        "industry": "Software",
        "basic_industry": "Enterprise",
    }
    assert service._selected_hierarchy(run_id, "SHORT") is None


def test_exact_hierarchy_matching(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id)

    result = StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert result["metadata"]["company_count"] == 1
    assert _candidates(disc_session, run_id)[0].company_id == "c1"


def test_companies_outside_hierarchy_are_excluded(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id, company_id="c1", symbol="AAA")
    _make_technical(disc_session, run_id, company_id="c2", symbol="BBB", industry="Hardware")
    _make_fundamental(disc_session, run_id, company_id="c2", symbol="BBB", industry="Hardware")

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert [row.company_id for row in _candidates(disc_session, run_id)] == ["c1"]


def test_same_basic_industry_name_under_another_hierarchy_isolated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id, company_id="c1", symbol="AAA")
    _make_technical(disc_session, run_id, company_id="c2", symbol="BBB", sector="Auto", basic="Enterprise Software")
    _make_fundamental(disc_session, run_id, company_id="c2", symbol="BBB", sector="Auto", basic="Enterprise Software")

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert [row.company_id for row in _candidates(disc_session, run_id)] == ["c1"]


def test_blank_symbols_are_rejected(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_technical(disc_session, run_id, company_id="c1", symbol="")

    result = StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert result["metadata"]["invalid_company_count"] == 1
    assert result["metadata"]["company_count"] == 0
    assert _candidates(disc_session, run_id) == []


def test_duplicate_companies_are_deduplicated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id, company_id="c1", symbol="AAA")
    _make_snapshot(disc_session, run_id, company_id="c1", symbol="AAA")

    result = StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert result["metadata"]["duplicate_count"] == 2
    assert len(_candidates(disc_session, run_id)) == 1


def test_deterministic_ordering(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    for cid, symbol in [("c2", "BBB"), ("c1", "AAA"), ("c3", "AAA")]:
        _make_snapshot(disc_session, run_id, company_id=cid, symbol=symbol)

    result = StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert [(item["symbol"], item["company_id"]) for item in result["candidates"]] == [
        ("AAA", "c1"),
        ("AAA", "c3"),
        ("BBB", "c2"),
    ]


def test_technical_record_available(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    row = _candidates(disc_session, run_id)[0]
    assert row.technical_available is True
    assert row.technical_metric_id is not None


def test_technical_record_missing(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_fundamental(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    row = _candidates(disc_session, run_id)[0]
    assert row.technical_available is False
    assert row.status == STATUS_TECHNICAL_UNAVAILABLE


def test_technical_coverage_failure(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_technical(disc_session, run_id, coverage=50.0)
    _make_fundamental(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert _candidates(disc_session, run_id)[0].technical_available is False


def test_fundamental_record_available(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    row = _candidates(disc_session, run_id)[0]
    assert row.fundamental_available is True
    assert row.fundamental_metric_id is not None


def test_fundamental_record_missing(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_technical(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    row = _candidates(disc_session, run_id)[0]
    assert row.fundamental_available is False
    assert row.status == STATUS_FUNDAMENTAL_UNAVAILABLE


def test_fundamental_coverage_failure(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_technical(disc_session, run_id)
    _make_fundamental(disc_session, run_id, coverage=50.0)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert _candidates(disc_session, run_id)[0].fundamental_available is False


def test_both_components_available_makes_candidate_eligible(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id)

    result = StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    row = _candidates(disc_session, run_id)[0]
    assert row.eligible is True
    assert row.status == STATUS_ELIGIBLE
    assert result["metadata"]["eligible_candidate_count"] == 1


def test_technical_unavailable_status(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_snapshot(disc_session, run_id)
    _make_fundamental(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert _candidates(disc_session, run_id)[0].status == STATUS_TECHNICAL_UNAVAILABLE


def test_fundamental_unavailable_status(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_snapshot(disc_session, run_id)
    _make_technical(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert _candidates(disc_session, run_id)[0].status == STATUS_FUNDAMENTAL_UNAVAILABLE


def test_both_unavailable_status(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_snapshot(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert _candidates(disc_session, run_id)[0].status == STATUS_BOTH_UNAVAILABLE


def test_ineligible_companies_remain_stored_for_diagnostics(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)
    _make_snapshot(disc_session, run_id)

    result = StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert result["metadata"]["ineligible_candidate_count"] == 1
    assert len(_candidates(disc_session, run_id)) == 1
    assert _candidates(disc_session, run_id)[0].calculation_details["candidate"]["eligible"] is False


def test_short_mid_and_long_remain_independent(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    rows = [
        ("SHORT", "AAA", "Tech", "Software", "Enterprise"),
        ("MID", "BBB", "Finance", "Banks", "Private"),
        ("LONG", "CCC", "Energy", "Oil", "Drilling"),
    ]
    for horizon, symbol, sector, industry, basic in rows:
        _make_selection(
            disc_session,
            run_id,
            sector=sector,
            industry=industry,
            basic=basic,
            horizon=horizon,
        )
        _make_technical(
            disc_session,
            run_id,
            company_id=f"c-{horizon}",
            symbol=symbol,
            sector=sector,
            industry=industry,
            basic=basic,
            horizon=horizon,
        )
        _make_fundamental(
            disc_session,
            run_id,
            company_id=f"c-{horizon}",
            symbol=symbol,
            sector=sector,
            industry=industry,
            basic=basic,
        )
    service = StockCandidateUniverseService(disc_session)

    for horizon in ("SHORT", "MID", "LONG"):
        service.build_candidates(run_id, horizon)

    assert [row.symbol for row in _candidates(disc_session, run_id, "SHORT")] == ["AAA"]
    assert [row.symbol for row in _candidates(disc_session, run_id, "MID")] == ["BBB"]
    assert [row.symbol for row in _candidates(disc_session, run_id, "LONG")] == ["CCC"]


def test_selected_hierarchy_change_removes_stale_candidates(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    selection = _make_selection(disc_session, run_id, industry="Software", basic="Enterprise")
    _make_technical(disc_session, run_id, company_id="old", symbol="OLD", industry="Software", basic="Enterprise")
    _make_fundamental(disc_session, run_id, company_id="old", symbol="OLD", industry="Software", basic="Enterprise")
    service = StockCandidateUniverseService(disc_session)
    service.build_candidates(run_id, "SHORT")

    selection.selected = False
    _make_selection(disc_session, run_id, industry="Hardware", basic="Devices")
    _make_technical(disc_session, run_id, company_id="new", symbol="NEW", industry="Hardware", basic="Devices")
    _make_fundamental(disc_session, run_id, company_id="new", symbol="NEW", industry="Hardware", basic="Devices")
    disc_session.commit()
    result = service.build_candidates(run_id, "SHORT")

    assert result["metadata"]["stale_candidate_count"] == 1
    assert [row.company_id for row in _candidates(disc_session, run_id)] == ["new"]


def test_missing_selected_basic_industry_clears_stale_candidates(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    selection = _make_selection(disc_session, run_id)
    _make_technical(disc_session, run_id)
    _make_fundamental(disc_session, run_id)
    service = StockCandidateUniverseService(disc_session)
    service.build_candidates(run_id, "SHORT")

    selection.selected = False
    disc_session.commit()
    result = service.build_candidates(run_id, "SHORT")

    assert W_SELECTED_UNAVAILABLE in result["warnings"]
    assert result["metadata"]["stale_candidate_count"] == 1
    assert _candidates(disc_session, run_id) == []


def test_empty_company_universe_behavior(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id)

    result = StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")

    assert W_EMPTY in result["warnings"]
    assert result["metadata"]["company_count"] == 0


def test_idempotent_persistence(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id)
    service = StockCandidateUniverseService(disc_session)

    first = service.build_candidates(run_id, "SHORT")
    first_ids = [row.id for row in _candidates(disc_session, run_id)]
    second = service.build_candidates(run_id, "SHORT")

    assert first["metadata"] == second["metadata"]
    assert [row.id for row in _candidates(disc_session, run_id)] == first_ids


def test_technical_and_fundamental_records_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    tech, fund = _setup_eligible(disc_session, run_id)
    before_tech = {
        "return_available": tech.return_available,
        "data_coverage": tech.data_coverage,
        "calculation_details": dict(tech.calculation_details),
    }
    before_fund = {
        "score": fund.final_fundamental_score,
        "coverage": fund.data_coverage,
        "eligible": fund.fundamental_eligible_for_selection,
        "calculation_details": dict(fund.calculation_details),
    }

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")
    disc_session.expire(tech)
    disc_session.expire(fund)

    assert tech.return_available == before_tech["return_available"]
    assert tech.data_coverage == before_tech["data_coverage"]
    assert tech.calculation_details == before_tech["calculation_details"]
    assert fund.final_fundamental_score == before_fund["score"]
    assert fund.data_coverage == before_fund["coverage"]
    assert fund.fundamental_eligible_for_selection == before_fund["eligible"]
    assert fund.calculation_details == before_fund["calculation_details"]


def test_no_stock_score_rank_or_selection_is_created(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id)

    StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")
    row = _candidates(disc_session, run_id)[0]
    columns = {column["name"] for column in inspect(discovery_engine).get_columns("stock_candidate_snapshots")}

    assert "score" not in columns
    assert row.rank is None
    assert row.selected is False
    assert row.selection_reason is None
    assert row.selected_at is None
    assert row.calculation_details["candidate"]["status"] == STATUS_ELIGIBLE


def test_no_llm_or_parallel_call(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup_eligible(disc_session, run_id)
    service = StockCandidateUniverseService(disc_session)

    service.build_candidates(run_id, "SHORT")

    assert not hasattr(service, "_llm")
    assert not hasattr(service, "_parallel")
    assert not hasattr(service, "_provider")


def test_no_source_database_access(disc_session):
    accessed = []

    @event.listens_for(source_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        accessed.append(statement)

    try:
        run_id = f"run_{uuid.uuid4().hex[:6]}"
        _setup_eligible(disc_session, run_id)
        StockCandidateUniverseService(disc_session).build_candidates(run_id, "SHORT")
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []
