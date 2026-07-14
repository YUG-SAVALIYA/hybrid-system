"""Offline tests for deterministic stock ranking and final selection."""
import copy
import uuid

import pytest
from sqlalchemy import event, inspect, text

from database import DiscoverySessionLocal, discovery_engine, source_engine
from models.discovery import DiscoverySelection, StockCandidateSnapshot
from services.stock.stock_discovery_ranking import (
    SELECTION_REASON,
    StockDiscoveryRankingService,
    W_NO_ELIGIBLE,
    W_SELECTED_UNAVAILABLE,
    W_STALE_SELECTION_REMOVED,
)


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM discovery_selections"))
    session.execute(text("DELETE FROM stock_candidate_snapshots"))
    session.commit()
    yield session
    session.rollback()
    session.execute(text("DELETE FROM discovery_selections"))
    session.execute(text("DELETE FROM stock_candidate_snapshots"))
    session.commit()
    session.close()


def _make_basic_selection(
    session,
    run_id,
    horizon="SHORT",
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
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
        selected=selected,
    )
    session.add(row)
    session.commit()
    return row


def _make_candidate(
    session,
    run_id,
    company_id="c1",
    symbol="AAA",
    horizon="SHORT",
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    final=80.0,
    technical=80.0,
    fundamental=70.0,
    macro=60.0,
    eligible=True,
    score_eligible=True,
    rank=None,
    selected=False,
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
        technical_metric_id=f"tech-{company_id}",
        fundamental_metric_id=f"fund-{company_id}",
        technical_available=True,
        fundamental_available=True,
        eligible=eligible,
        status="ELIGIBLE" if eligible else "TECHNICAL_UNAVAILABLE",
        warnings=["KEEP_UNIVERSE"],
        calculation_details={"universe": {"keep": True}},
        technical_score=technical,
        fundamental_score=fundamental,
        inherited_macro_score=macro,
        final_score=final,
        score_coverage_pct=100.0,
        score_status="VERY_STRONG",
        score_eligible=score_eligible,
        score_warnings=["KEEP_SCORE"],
        score_details={"score": {"keep": True}},
        rank=rank,
        selected=selected,
        selection_reason=SELECTION_REASON if selected else None,
    )
    session.add(row)
    session.commit()
    return row


def _candidates(session, run_id, horizon="SHORT"):
    return (
        session.query(StockCandidateSnapshot)
        .filter_by(run_id=run_id, horizon=horizon)
        .order_by(StockCandidateSnapshot.symbol.asc(), StockCandidateSnapshot.company_id.asc())
        .all()
    )


def _stock_selections(session, run_id, horizon="SHORT"):
    return (
        session.query(DiscoverySelection)
        .filter_by(run_id=run_id, horizon=horizon, entity_type="STOCK")
        .order_by(DiscoverySelection.rank.asc(), DiscoverySelection.symbol.asc())
        .all()
    )


def test_only_score_eligible_candidates_are_ranked(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id, company_id="c1", symbol="AAA", score_eligible=True)
    _make_candidate(disc_session, run_id, company_id="c2", symbol="BBB", score_eligible=False)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["symbol"] for item in result["ranked_candidates"]] == ["AAA"]
    assert _candidates(disc_session, run_id)[1].rank is None


def test_candidate_hierarchy_must_match_active_basic_selection(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id, sector="Tech", industry="Software", basic="Enterprise")
    _make_candidate(disc_session, run_id, company_id="c1", sector="Tech", industry="Software", basic="Enterprise", symbol="AAA")
    _make_candidate(disc_session, run_id, company_id="c2", sector="Tech", industry="Hardware", basic="Devices", symbol="BBB")

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["symbol"] for item in result["ranked_candidates"]] == ["AAA"]
    assert result["metadata"]["hierarchy_mismatch_count"] == 1


def test_final_score_descending_ordering(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id, company_id="low", symbol="LOW", final=70)
    _make_candidate(disc_session, run_id, company_id="high", symbol="HIGH", final=90)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["symbol"] for item in result["ranked_candidates"]] == ["HIGH", "LOW"]


def test_technical_score_tie_break(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id, company_id="c1", symbol="AAA", final=80, technical=70)
    _make_candidate(disc_session, run_id, company_id="c2", symbol="BBB", final=80, technical=90)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["symbol"] for item in result["ranked_candidates"]] == ["BBB", "AAA"]


def test_fundamental_score_tie_break(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id, company_id="c1", symbol="AAA", final=80, technical=80, fundamental=70)
    _make_candidate(disc_session, run_id, company_id="c2", symbol="BBB", final=80, technical=80, fundamental=90)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["symbol"] for item in result["ranked_candidates"]] == ["BBB", "AAA"]


def test_symbol_alphabetical_tie_break(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id, company_id="c1", symbol="BBB", final=80, technical=80, fundamental=80)
    _make_candidate(disc_session, run_id, company_id="c2", symbol="AAA", final=80, technical=80, fundamental=80)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["symbol"] for item in result["ranked_candidates"]] == ["AAA", "BBB"]


def test_company_id_final_tie_break(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    for idx in range(5):
        _make_candidate(disc_session, run_id, company_id=f"top-{idx}", symbol=f"TOP{idx}", final=90 - idx)
    _make_candidate(disc_session, run_id, company_id="c2", symbol="ZZZ", final=80, technical=80, fundamental=80)
    _make_candidate(disc_session, run_id, company_id="c1", symbol="ZZZ", final=80, technical=80, fundamental=80)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    tied_ids = [
        item["company_id"]
        for item in result["ranked_candidates"]
        if item["symbol"] == "ZZZ"
    ]
    assert tied_ids == ["c1", "c2"]


def test_sequential_ranks_without_tied_ranks(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    for idx in range(3):
        _make_candidate(disc_session, run_id, company_id=f"c{idx}", symbol=f"S{idx}", final=80)

    StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [row.rank for row in _candidates(disc_session, run_id)] == [1, 2, 3]


def test_ineligible_candidate_rank_remains_null(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id, eligible=False, score_eligible=True)

    StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert _candidates(disc_session, run_id)[0].rank is None


def test_top_five_selection(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    for idx in range(6):
        _make_candidate(disc_session, run_id, company_id=f"c{idx}", symbol=f"S{idx}", final=100 - idx)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert result["metadata"]["selected_stock_count"] == 5
    assert result["selected_symbols"] == ["S0", "S1", "S2", "S3", "S4"]


def test_fewer_than_five_eligible_candidates(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    for idx in range(3):
        _make_candidate(disc_session, run_id, company_id=f"c{idx}", symbol=f"S{idx}", final=100 - idx)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert result["metadata"]["selected_stock_count"] == 3


def test_no_eligible_stock_behavior(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id, score_eligible=False, rank=1, selected=True)

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert W_NO_ELIGIBLE in result["warnings"]
    assert _candidates(disc_session, run_id)[0].selected is False


def test_no_selected_basic_industry_behavior(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_candidate(disc_session, run_id, rank=1, selected=True)
    disc_session.add(
        DiscoverySelection(
            id=str(uuid.uuid4()),
            run_id=run_id,
            horizon="SHORT",
            entity_type="STOCK",
            entity_name="AAA",
            parent_sector="Tech",
            parent_industry="Software",
            selected=True,
        )
    )
    disc_session.commit()

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert W_SELECTED_UNAVAILABLE in result["warnings"]
    assert result["metadata"]["stale_selection_count"] == 1
    assert _candidates(disc_session, run_id)[0].rank is None


def test_selected_stock_hierarchy_is_persisted_correctly(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id, sector="Tech", industry="Software", basic="Enterprise")
    _make_candidate(disc_session, run_id, company_id="c1", symbol="AAA", sector="Tech", industry="Software", basic="Enterprise")

    StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    row = _stock_selections(disc_session, run_id)[0]

    assert row.entity_type == "STOCK"
    assert row.company_id == "c1"
    assert row.symbol == "AAA"
    assert row.entity_name == "AAA"
    assert row.parent_sector == "Tech"
    assert row.parent_industry == "Software"
    assert row.basic_industry == "Enterprise"
    assert row.calculation_details["selected_hierarchy"]["basic_industry"] == "Enterprise"


def test_previous_selections_are_replaced_on_rerun(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    first = _make_candidate(disc_session, run_id, company_id="first", symbol="FIRST", final=99)
    second = _make_candidate(disc_session, run_id, company_id="second", symbol="SECOND", final=80)
    for idx, score in enumerate([95, 94, 93, 92]):
        _make_candidate(disc_session, run_id, company_id=f"filler-{idx}", symbol=f"FILL{idx}", final=score)
    service = StockDiscoveryRankingService(disc_session)
    service.rank_and_select(run_id, "SHORT")

    first.final_score = 70.0
    second.final_score = 100.0
    disc_session.commit()
    service.rank_and_select(run_id, "SHORT")

    selected = {row.symbol for row in _stock_selections(disc_session, run_id) if row.selected}
    assert "SECOND" in selected
    assert "FIRST" not in selected


def test_selected_basic_industry_change_clears_stale_stock_selections(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    basic = _make_basic_selection(disc_session, run_id, basic="Enterprise")
    _make_candidate(disc_session, run_id, company_id="old", symbol="OLD", basic="Enterprise")
    service = StockDiscoveryRankingService(disc_session)
    service.rank_and_select(run_id, "SHORT")

    basic.selected = False
    _make_basic_selection(disc_session, run_id, basic="Devices")
    _make_candidate(disc_session, run_id, company_id="new", symbol="NEW", basic="Devices")
    disc_session.commit()
    result = service.rank_and_select(run_id, "SHORT")

    assert W_STALE_SELECTION_REMOVED in result["warnings"]
    assert [row.symbol for row in _stock_selections(disc_session, run_id) if row.selected] == ["NEW"]


def test_candidates_outside_hierarchy_have_ranks_cleared(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id, basic="Enterprise")
    _make_candidate(disc_session, run_id, company_id="old", symbol="OLD", basic="Devices", rank=4, selected=True)
    _make_candidate(disc_session, run_id, company_id="new", symbol="NEW", basic="Enterprise")

    result = StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    old = [row for row in _candidates(disc_session, run_id) if row.company_id == "old"][0]
    assert result["metadata"]["stale_rank_count"] >= 1
    assert old.rank is None
    assert old.selected is False


def test_candidates_becoming_score_ineligible_have_ranks_cleared(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    candidate = _make_candidate(disc_session, run_id, rank=1, selected=True, score_eligible=False)

    StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    disc_session.expire(candidate)

    assert candidate.rank is None
    assert candidate.selected is False


def test_short_mid_and_long_remain_independent(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    for horizon, symbol in [("SHORT", "AAA"), ("MID", "BBB"), ("LONG", "CCC")]:
        _make_basic_selection(disc_session, run_id, horizon=horizon)
        _make_candidate(disc_session, run_id, company_id=f"c-{horizon}", symbol=symbol, horizon=horizon)
    service = StockDiscoveryRankingService(disc_session)

    for horizon in ("SHORT", "MID", "LONG"):
        service.rank_and_select(run_id, horizon)

    assert [row.symbol for row in _stock_selections(disc_session, run_id, "SHORT") if row.selected] == ["AAA"]
    assert [row.symbol for row in _stock_selections(disc_session, run_id, "MID") if row.selected] == ["BBB"]
    assert [row.symbol for row in _stock_selections(disc_session, run_id, "LONG") if row.selected] == ["CCC"]


def test_another_run_remains_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    other_run = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id, symbol="AAA")
    _make_candidate(disc_session, other_run, symbol="OTHER", rank=9, selected=True)

    StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    other = _candidates(disc_session, other_run)[0]
    assert other.rank == 9
    assert other.selected is True


def test_existing_universe_and_score_details_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    candidate = _make_candidate(disc_session, run_id)
    universe = copy.deepcopy(candidate.calculation_details)
    score = copy.deepcopy(candidate.score_details)
    warnings = list(candidate.warnings)
    score_warnings = list(candidate.score_warnings)

    StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    disc_session.expire(candidate)

    assert candidate.calculation_details == universe
    assert candidate.score_details == score
    assert candidate.warnings == warnings
    assert candidate.score_warnings == score_warnings


def test_idempotent_persistence(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id)
    service = StockDiscoveryRankingService(disc_session)

    first = service.rank_and_select(run_id, "SHORT")
    first_selection_ids = [row.id for row in _stock_selections(disc_session, run_id)]
    second = service.rank_and_select(run_id, "SHORT")

    assert first["metadata"] == second["metadata"]
    assert [row.id for row in _stock_selections(disc_session, run_id)] == first_selection_ids


def test_no_recommendation_or_trading_fields_are_created(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id)

    StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    columns = {column["name"] for column in inspect(discovery_engine).get_columns("stock_candidate_snapshots")}

    for forbidden in {"recommendation", "quantity", "entry_price", "target", "stop_loss", "order"}:
        assert forbidden not in columns


def test_no_llm_or_parallel_call(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_selection(disc_session, run_id)
    _make_candidate(disc_session, run_id)
    service = StockDiscoveryRankingService(disc_session)

    service.rank_and_select(run_id, "SHORT")

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
        _make_basic_selection(disc_session, run_id)
        _make_candidate(disc_session, run_id)
        StockDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []
