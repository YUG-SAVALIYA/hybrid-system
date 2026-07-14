"""Offline tests for deterministic stock candidate scoring."""
import copy
import datetime
import uuid

import pytest
from sqlalchemy import event, text

from database import DiscoverySessionLocal, source_engine
from models.discovery import (
    CompanyFundamentalMetric,
    CompanyTechnicalMetric,
    GroupScore,
    StockCandidateSnapshot,
)
from services.stock.stock_candidate_score import (
    StockCandidateScoreService,
    W_LOW_COVERAGE,
    W_MACRO_INELIGIBLE,
    W_MACRO_UNAVAILABLE,
    W_PARTIAL,
    W_FUNDAMENTAL_UNAVAILABLE,
    W_TECHNICAL_UNAVAILABLE,
    _status_from_score,
)
from services.stock.stock_candidate_universe import STATUS_ELIGIBLE, STATUS_TECHNICAL_UNAVAILABLE


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM stock_candidate_snapshots"))
    session.execute(text("DELETE FROM company_technical_metrics"))
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM stock_candidate_snapshots"))
    session.execute(text("DELETE FROM company_technical_metrics"))
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _make_technical(
    session,
    run_id,
    company_id="c1",
    symbol="AAA",
    horizon="SHORT",
    score=80.0,
):
    row = CompanyTechnicalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=company_id,
        symbol=symbol,
        sector="Tech",
        industry="Software",
        basic_industry="Enterprise Software",
        horizon=horizon,
        as_of_date=datetime.date(2026, 7, 13),
        company_candle_date="2026-07-13",
        benchmark_candle_date="2026-07-13",
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
        return_available=True,
        volume_available=True,
        consistency_available=True,
        data_coverage=100.0,
        warnings=["KEEP_TECH"],
        calculation_details={"technical_score": score, "keep": True},
    )
    session.add(row)
    session.commit()
    return row


def _make_fundamental(
    session,
    run_id,
    company_id="c1",
    symbol="AAA",
    score=70.0,
):
    row = CompanyFundamentalMetric(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_company_id=company_id,
        symbol=symbol,
        sector="Tech",
        industry="Software",
        basic_industry="Enterprise Software",
        growth_score=70.0,
        profitability_score=70.0,
        financial_strength_score=70.0,
        earnings_quality_score=70.0,
        final_fundamental_score=score,
        fundamental_status="STRONG",
        fundamental_eligible_for_selection=True,
        benchmark_level_used="BASIC_INDUSTRY",
        data_coverage=100.0,
        unavailable_fields=[],
        calculation_details={"keep": True},
    )
    session.add(row)
    session.commit()
    return row


def _make_macro_group(
    session,
    run_id,
    horizon="SHORT",
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    score=60.0,
    status="POSITIVE",
    eligible=True,
):
    row = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name=basic,
        parent_sector=sector,
        parent_industry=industry,
        horizon=horizon,
        macro_score=score,
        warnings=["KEEP_GROUP"],
        calculation_details={
            "macro": {
                "basic_industry_score": {
                    "status": status,
                    "eligible_for_selection": eligible,
                }
            }
        },
    )
    session.add(row)
    session.commit()
    return row


def _make_candidate(
    session,
    run_id,
    technical_id,
    fundamental_id,
    company_id="c1",
    symbol="AAA",
    horizon="SHORT",
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    eligible=True,
    status=STATUS_ELIGIBLE,
    technical_available=True,
    fundamental_available=True,
):
    row = StockCandidateSnapshot(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon=horizon,
        company_id=company_id,
        symbol=symbol,
        sector=sector,
        industry=industry,
        basic_industry=basic,
        technical_metric_id=technical_id,
        fundamental_metric_id=fundamental_id,
        technical_available=technical_available,
        fundamental_available=fundamental_available,
        eligible=eligible,
        status=status,
        warnings=["KEEP_UNIVERSE"],
        calculation_details={"candidate": {"eligible": eligible}},
    )
    session.add(row)
    session.commit()
    return row


def _setup(session, run_id, horizon="SHORT", tech=80.0, fund=70.0, macro=60.0):
    technical = _make_technical(session, run_id, horizon=horizon, score=tech)
    fundamental = _make_fundamental(session, run_id, score=fund)
    _make_macro_group(session, run_id, horizon=horizon, score=macro)
    candidate = _make_candidate(session, run_id, technical.id, fundamental.id, horizon=horizon)
    return candidate, technical, fundamental


def _candidate(session, run_id, horizon="SHORT", company_id="c1"):
    return (
        session.query(StockCandidateSnapshot)
        .filter_by(run_id=run_id, horizon=horizon, company_id=company_id)
        .first()
    )


def test_exact_40_40_20_score(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.final_score == 72.0
    assert row.score_details["components"]["technical"]["weighted_contribution"] == 3200.0
    assert row.score_details["components"]["fundamental"]["weighted_contribution"] == 2800.0
    assert row.score_details["components"]["macro"]["weighted_contribution"] == 1200.0


def test_macro_n_a_exclusion(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    candidate, _, _ = _setup(disc_session, run_id, macro=None)
    group = disc_session.query(GroupScore).filter_by(run_id=run_id).first()
    group.calculation_details = {"macro": {"basic_industry_score": {"status": "N_A", "eligible_for_selection": True}}}
    disc_session.commit()

    result = StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.final_score == 75.0
    assert row.score_details["applicable_weight"] == 80.0
    assert row.score_details["components"]["macro"]["applicable"] is False
    assert result["macro_n_a_count"] == 1


def test_missing_applicable_macro_creates_partial_score_but_ineligible(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, macro=None)

    result = StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.final_score == 75.0
    assert row.score_eligible is False
    assert W_PARTIAL in row.score_warnings
    assert W_MACRO_UNAVAILABLE in row.score_warnings
    assert result["partial_score_count"] == 1


def test_technical_metric_identity_validation(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    other_run = f"run_{uuid.uuid4().hex[:6]}"
    technical = _make_technical(disc_session, other_run)
    fundamental = _make_fundamental(disc_session, run_id)
    _make_macro_group(disc_session, run_id)
    candidate = _make_candidate(disc_session, run_id, technical.id, fundamental.id)
    candidate.final_score = 80.0
    candidate.score_status = "VERY_STRONG"
    disc_session.commit()

    result = StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")
    disc_session.expire(candidate)

    assert result["stale_score_count"] == 1
    assert candidate.final_score is None
    assert candidate.score_warnings == ["STOCK_SCORE_STALE"]


def test_fundamental_metric_identity_validation(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    other_run = f"run_{uuid.uuid4().hex[:6]}"
    technical = _make_technical(disc_session, run_id)
    fundamental = _make_fundamental(disc_session, other_run)
    _make_macro_group(disc_session, run_id)
    candidate = _make_candidate(disc_session, run_id, technical.id, fundamental.id)
    candidate.final_score = 80.0
    candidate.score_status = "VERY_STRONG"
    disc_session.commit()

    result = StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")
    disc_session.expire(candidate)

    assert result["stale_score_count"] == 1
    assert candidate.final_score is None
    assert candidate.score_warnings == ["STOCK_SCORE_STALE"]


def test_technical_score_unavailable(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, tech=None)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.technical_score is None
    assert W_TECHNICAL_UNAVAILABLE in row.score_warnings


def test_fundamental_score_unavailable(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, fund=None)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.fundamental_score is None
    assert W_FUNDAMENTAL_UNAVAILABLE in row.score_warnings


def test_macro_score_unavailable(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, macro=None)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    assert W_MACRO_UNAVAILABLE in _candidate(disc_session, run_id).score_warnings


def test_coverage_exactly_80(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, macro=None)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.score_coverage_pct == 80.0
    assert W_LOW_COVERAGE not in row.score_warnings


def test_coverage_below_80(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, fund=None, macro=None)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.score_coverage_pct == 40.0
    assert W_LOW_COVERAGE in row.score_warnings


def test_low_coverage_preserves_score(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, fund=None, macro=None)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    assert _candidate(disc_session, run_id).final_score == 80.0


def test_universe_eligibility_remains_separate_from_score_eligibility(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, macro=None)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.eligible is True
    assert row.score_eligible is False


def test_every_score_status_boundary():
    assert _status_from_score(100.0) == "VERY_STRONG"
    assert _status_from_score(80.0) == "VERY_STRONG"
    assert _status_from_score(79.99) == "STRONG"
    assert _status_from_score(65.0) == "STRONG"
    assert _status_from_score(64.99) == "NEUTRAL"
    assert _status_from_score(50.0) == "NEUTRAL"
    assert _status_from_score(49.99) == "WEAK"
    assert _status_from_score(35.0) == "WEAK"
    assert _status_from_score(34.99) == "VERY_WEAK"
    assert _status_from_score(0.0) == "VERY_WEAK"
    assert _status_from_score(None) == "UNAVAILABLE"


def test_same_symbol_in_another_run_remains_isolated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    other_run = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, tech=80)
    _setup(disc_session, other_run, tech=10)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    assert _candidate(disc_session, run_id).technical_score == 80.0
    assert _candidate(disc_session, other_run).final_score is None


def test_same_company_across_horizons_remains_isolated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, horizon="SHORT", tech=80)
    tech_mid = _make_technical(disc_session, run_id, horizon="MID", score=10)
    fund = disc_session.query(CompanyFundamentalMetric).filter_by(run_id=run_id).first()
    _make_macro_group(disc_session, run_id, horizon="MID", score=60)
    _make_candidate(disc_session, run_id, tech_mid.id, fund.id, horizon="MID")

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    assert _candidate(disc_session, run_id, "SHORT").technical_score == 80.0
    assert _candidate(disc_session, run_id, "MID").final_score is None


def test_macro_is_inherited_from_exact_selected_hierarchy(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id, macro=40)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    row = _candidate(disc_session, run_id)
    assert row.inherited_macro_score == 40.0
    assert row.score_details["components"]["macro"]["source_entity_name"] == "Enterprise Software"


def test_same_basic_industry_name_under_another_hierarchy_isolated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    candidate, technical, fundamental = _setup(disc_session, run_id, macro=60)
    other = _make_macro_group(
        disc_session,
        run_id,
        sector="Auto",
        industry="Software",
        basic="Enterprise Software",
        score=0,
    )

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")

    assert _candidate(disc_session, run_id).inherited_macro_score == 60.0


def test_existing_metric_records_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _, technical, fundamental = _setup(disc_session, run_id)
    before_technical = copy.deepcopy(technical.calculation_details)
    before_fundamental = copy.deepcopy(fundamental.calculation_details)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")
    disc_session.expire(technical)
    disc_session.expire(fundamental)

    assert technical.calculation_details == before_technical
    assert fundamental.calculation_details == before_fundamental


def test_universe_fields_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    candidate, _, _ = _setup(disc_session, run_id)
    before = {
        "eligible": candidate.eligible,
        "status": candidate.status,
        "warnings": list(candidate.warnings),
        "calculation_details": copy.deepcopy(candidate.calculation_details),
    }

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")
    disc_session.expire(candidate)

    assert candidate.eligible == before["eligible"]
    assert candidate.status == before["status"]
    assert candidate.warnings == before["warnings"]
    assert candidate.calculation_details == before["calculation_details"]


def test_ineligible_universe_candidates_are_not_scored(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    technical = _make_technical(disc_session, run_id)
    fundamental = _make_fundamental(disc_session, run_id)
    _make_macro_group(disc_session, run_id)
    candidate = _make_candidate(
        disc_session,
        run_id,
        technical.id,
        fundamental.id,
        eligible=False,
        status=STATUS_TECHNICAL_UNAVAILABLE,
    )
    candidate.final_score = 88.0
    candidate.score_status = "VERY_STRONG"
    disc_session.commit()

    result = StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")
    disc_session.expire(candidate)

    assert result["stale_score_count"] == 1
    assert candidate.final_score is None


def test_stale_scoring_fields_are_cleared(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    technical = _make_technical(disc_session, run_id)
    fundamental = _make_fundamental(disc_session, run_id)
    candidate = _make_candidate(disc_session, run_id, technical.id, fundamental.id)
    candidate.final_score = 88.0
    candidate.score_status = "VERY_STRONG"
    candidate.score_details = {"old": True}
    disc_session.commit()

    result = StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")
    disc_session.expire(candidate)

    assert result["stale_score_count"] == 1
    assert candidate.final_score is None
    assert candidate.score_details is None
    assert candidate.score_warnings == ["STOCK_SCORE_STALE"]


def test_idempotent_persistence(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id)
    service = StockCandidateScoreService(disc_session)

    first = service.score_candidates(run_id, "SHORT")
    row = _candidate(disc_session, run_id)
    first_details = copy.deepcopy(row.score_details)
    second = service.score_candidates(run_id, "SHORT")
    disc_session.expire(row)

    assert first == second
    assert row.score_details == first_details


def test_no_stock_rank_or_selection_is_created(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id)

    StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")
    row = _candidate(disc_session, run_id)

    assert row.rank is None
    assert row.selected is False
    assert row.selection_reason is None
    assert row.selected_at is None


def test_no_llm_or_parallel_call(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _setup(disc_session, run_id)
    service = StockCandidateScoreService(disc_session)

    service.score_candidates(run_id, "SHORT")

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
        _setup(disc_session, run_id)
        StockCandidateScoreService(disc_session).score_candidates(run_id, "SHORT")
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []
