"""Offline tests for deterministic final sector ranking and selection."""
import copy
import uuid

import pytest
from sqlalchemy import event, text

from database import DiscoverySessionLocal, source_engine
from models.discovery import DiscoverySelection, GroupScore
from services.ranking.sector_discovery_ranking import (
    W_FUNDAMENTAL_INELIGIBLE,
    W_LOW_COVERAGE,
    W_MACRO_INELIGIBLE,
    W_NO_ELIGIBLE,
    W_TECHNICAL_INELIGIBLE,
    SectorDiscoveryRankingService,
    _status_from_score,
    calculate_final_sector_score,
)


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM discovery_selections"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM discovery_selections"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _calc_details(
    return_count=5,
    technical_coverage=100.0,
    fundamental_eligible=True,
    macro_status="NEUTRAL",
    macro_eligible=True,
):
    return {
        "technical": {
            "coverage_pct": technical_coverage,
            "return": {"return_eligible_count": return_count},
            "status": "STRONG",
        },
        "fundamental": {
            "raw_aggregation": {"unchanged": True},
            "final_score": {
                "eligible_for_selection": fundamental_eligible,
                "status": "STRONG",
            },
        },
        "macro": {
            "sector_score": {
                "status": macro_status,
                "eligible_for_selection": macro_eligible,
            }
        },
    }


def _make_group(
    session,
    run_id,
    name="Auto",
    horizon="SHORT",
    technical=80.0,
    fundamental=70.0,
    macro=60.0,
    return_count=5,
    technical_coverage=100.0,
    fundamental_eligible=True,
    macro_status="NEUTRAL",
    macro_eligible=True,
    warnings=None,
):
    row = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="SECTOR",
        entity_name=name,
        parent_sector="",
        parent_industry="",
        horizon=horizon,
        constituent_count=10,
        eligible_constituent_count=return_count,
        technical_return_score=81.0,
        technical_breadth_score=82.0,
        technical_volume_score=83.0,
        technical_consistency_score=84.0,
        technical_score=technical,
        fundamental_growth_score=71.0,
        fundamental_profitability_score=72.0,
        fundamental_financial_strength_score=73.0,
        fundamental_earnings_quality_score=74.0,
        fundamental_score=fundamental,
        macro_score=macro,
        final_score=None,
        rank=99,
        data_coverage=technical_coverage,
        warnings=warnings or ["KEEP_ME"],
        calculation_details=_calc_details(
            return_count=return_count,
            technical_coverage=technical_coverage,
            fundamental_eligible=fundamental_eligible,
            macro_status=macro_status,
            macro_eligible=macro_eligible,
        ),
    )
    session.add(row)
    session.commit()
    return row


def _get_group(session, run_id, name="Auto", horizon="SHORT"):
    return (
        session.query(GroupScore)
        .filter_by(run_id=run_id, entity_name=name, horizon=horizon)
        .first()
    )


def _selection_rows(session, run_id, horizon="SHORT"):
    return (
        session.query(DiscoverySelection)
        .filter_by(run_id=run_id, horizon=horizon, entity_type="SECTOR")
        .order_by(DiscoverySelection.entity_name.asc())
        .all()
    )


def test_exact_40_40_20_score():
    group = _make_group_obj(technical=80, fundamental=70, macro=60)

    details, _ = calculate_final_sector_score(group)

    assert details["score"] == 72.0
    assert details["components"]["technical"]["weighted_contribution"] == 3200.0
    assert details["components"]["fundamental"]["weighted_contribution"] == 2800.0
    assert details["components"]["macro"]["weighted_contribution"] == 1200.0


def test_macro_n_a_exclusion():
    group = _make_group_obj(technical=80, fundamental=70, macro=None, macro_status="N_A")

    details, warnings = calculate_final_sector_score(group)

    assert details["applicable_weight"] == 80.0
    assert details["available_weight"] == 80.0
    assert details["score"] == 75.0
    assert details["components"]["macro"]["applicable"] is False
    assert "SECTOR_MACRO_INELIGIBLE" not in warnings


def test_missing_applicable_macro_score_renormalizes_but_ineligible():
    group = _make_group_obj(technical=80, fundamental=70, macro=None, macro_eligible=False)

    details, warnings = calculate_final_sector_score(group)

    assert details["score"] == 75.0
    assert details["coverage_pct"] == 80.0
    assert details["eligible_for_selection"] is False
    assert W_MACRO_INELIGIBLE in warnings


def test_combined_coverage():
    group = _make_group_obj(technical=80, fundamental=70, macro=None, macro_eligible=False)

    details, _ = calculate_final_sector_score(group)

    assert details["applicable_weight"] == 100.0
    assert details["available_weight"] == 80.0
    assert details["coverage_pct"] == 80.0


def test_technical_eligibility():
    group = _make_group_obj(return_count=4)

    details, warnings = calculate_final_sector_score(group)

    assert details["components"]["technical"]["eligible"] is False
    assert details["eligible_for_selection"] is False
    assert W_TECHNICAL_INELIGIBLE in warnings


def test_fundamental_eligibility():
    group = _make_group_obj(fundamental_eligible=False)

    details, warnings = calculate_final_sector_score(group)

    assert details["components"]["fundamental"]["eligible"] is False
    assert details["eligible_for_selection"] is False
    assert W_FUNDAMENTAL_INELIGIBLE in warnings


def test_macro_eligibility():
    group = _make_group_obj(macro_eligible=False)

    details, warnings = calculate_final_sector_score(group)

    assert details["components"]["macro"]["eligible"] is False
    assert details["eligible_for_selection"] is False
    assert W_MACRO_INELIGIBLE in warnings


def test_coverage_exactly_80():
    group = _make_group_obj(macro=None, macro_eligible=False)

    details, warnings = calculate_final_sector_score(group)

    assert details["coverage_pct"] == 80.0
    assert W_LOW_COVERAGE not in warnings


def test_coverage_below_80():
    group = _make_group_obj(fundamental=None, macro=None, macro_eligible=False)

    details, warnings = calculate_final_sector_score(group)

    assert details["coverage_pct"] == 40.0
    assert details["eligible_for_selection"] is False
    assert W_LOW_COVERAGE in warnings


def test_low_coverage_preserves_score(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, fundamental=None, macro=None, macro_eligible=False)

    SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    group = _get_group(disc_session, run_id)

    assert group.final_score == 80.0
    assert group.calculation_details["discovery"]["final_sector_score"]["eligible_for_selection"] is False


def test_final_status_boundaries():
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


def test_eligible_sectors_only_are_ranked(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, "Eligible", technical=90)
    _make_group(disc_session, run_id, "Ineligible", technical=95, fundamental_eligible=False)

    SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert _get_group(disc_session, run_id, "Eligible").rank == 1
    assert _get_group(disc_session, run_id, "Ineligible").rank is None


def test_final_score_descending_order(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, "Low", technical=70, fundamental=70, macro=70)
    _make_group(disc_session, run_id, "High", technical=90, fundamental=90, macro=90)

    result = SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["entity_name"] for item in result["ranked_sectors"]] == ["High", "Low"]
    assert _get_group(disc_session, run_id, "High").rank == 1
    assert _get_group(disc_session, run_id, "Low").rank == 2


def test_alphabetical_tie_break(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, "Beta", technical=80, fundamental=80, macro=80)
    _make_group(disc_session, run_id, "Alpha", technical=80, fundamental=80, macro=80)

    result = SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["entity_name"] for item in result["ranked_sectors"]] == ["Alpha", "Beta"]


def test_ineligible_rank_remains_null(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, "Nope", technical=None)

    SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert _get_group(disc_session, run_id, "Nope").rank is None


def test_top_one_selection(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, "Winner", technical=95, fundamental=95, macro=95)
    _make_group(disc_session, run_id, "Runner", technical=90, fundamental=90, macro=90)

    result = SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    selected = [row for row in _selection_rows(disc_session, run_id) if row.selected]

    assert result["selected_sectors"] == ["Winner"]
    assert len(selected) == 1
    assert selected[0].entity_name == "Winner"
    assert selected[0].rank == 1


def test_no_eligible_sector_behavior(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, "Bad", technical=None, fundamental=None, macro=None)

    result = SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert W_NO_ELIGIBLE in result["warnings"]
    assert result["metadata"]["selected_sector_count"] == 0
    assert _selection_rows(disc_session, run_id) == []


def test_previous_selection_replaced_on_rerun(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    first = _make_group(disc_session, run_id, "First", technical=95, fundamental=95, macro=95)
    second = _make_group(disc_session, run_id, "Second", technical=80, fundamental=80, macro=80)
    service = SectorDiscoveryRankingService(disc_session)
    service.rank_and_select(run_id, "SHORT")

    first.technical_score = 70.0
    first.fundamental_score = 70.0
    first.macro_score = 70.0
    second.technical_score = 99.0
    second.fundamental_score = 99.0
    second.macro_score = 99.0
    disc_session.commit()
    service.rank_and_select(run_id, "SHORT")

    rows = _selection_rows(disc_session, run_id)
    selected = [row for row in rows if row.selected]
    stale = [row for row in rows if row.entity_name == "First"][0]
    assert selected[0].entity_name == "Second"
    assert stale.selected is False


def test_short_mid_long_selections_remain_independent(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, "ShortWinner", horizon="SHORT", technical=95, fundamental=95, macro=95)
    _make_group(disc_session, run_id, "MidWinner", horizon="MID", technical=95, fundamental=95, macro=95)
    _make_group(disc_session, run_id, "LongWinner", horizon="LONG", technical=95, fundamental=95, macro=95)
    service = SectorDiscoveryRankingService(disc_session)

    service.rank_and_select(run_id, "SHORT")
    service.rank_and_select(run_id, "MID")
    service.rank_and_select(run_id, "LONG")

    assert [row.entity_name for row in _selection_rows(disc_session, run_id, "SHORT") if row.selected] == ["ShortWinner"]
    assert [row.entity_name for row in _selection_rows(disc_session, run_id, "MID") if row.selected] == ["MidWinner"]
    assert [row.entity_name for row in _selection_rows(disc_session, run_id, "LONG") if row.selected] == ["LongWinner"]


def test_existing_technical_fundamental_macro_data_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    group = _make_group(disc_session, run_id)
    before = {
        "technical_score": group.technical_score,
        "fundamental_score": group.fundamental_score,
        "macro_score": group.macro_score,
        "technical": copy.deepcopy(group.calculation_details["technical"]),
        "fundamental": copy.deepcopy(group.calculation_details["fundamental"]),
        "macro": copy.deepcopy(group.calculation_details["macro"]),
    }

    SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    disc_session.expire(group)

    assert group.technical_score == before["technical_score"]
    assert group.fundamental_score == before["fundamental_score"]
    assert group.macro_score == before["macro_score"]
    assert group.calculation_details["technical"] == before["technical"]
    assert group.calculation_details["fundamental"] == before["fundamental"]
    assert group.calculation_details["macro"] == before["macro"]


def test_idempotent_persistence(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id)
    service = SectorDiscoveryRankingService(disc_session)

    first = service.rank_and_select(run_id, "SHORT")
    group = _get_group(disc_session, run_id)
    first_calc = copy.deepcopy(group.calculation_details)
    first_selection_ids = [row.id for row in _selection_rows(disc_session, run_id)]
    second = service.rank_and_select(run_id, "SHORT")
    disc_session.expire(group)

    assert first["metadata"] == second["metadata"]
    assert group.calculation_details == first_calc
    assert [row.id for row in _selection_rows(disc_session, run_id)] == first_selection_ids


def test_no_llm_or_parallel_call(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id)
    service = SectorDiscoveryRankingService(disc_session)

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
        _make_group(disc_session, run_id)
        SectorDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []


def _make_group_obj(**kwargs):
    class Obj:
        pass

    obj = Obj()
    obj.technical_score = kwargs.get("technical", 80.0)
    obj.fundamental_score = kwargs.get("fundamental", 70.0)
    obj.macro_score = kwargs.get("macro", 60.0)
    obj.eligible_constituent_count = kwargs.get("return_count", 5)
    obj.data_coverage = kwargs.get("technical_coverage", 100.0)
    obj.warnings = kwargs.get("warnings", [])
    obj.calculation_details = _calc_details(
        return_count=kwargs.get("return_count", 5),
        technical_coverage=kwargs.get("technical_coverage", 100.0),
        fundamental_eligible=kwargs.get("fundamental_eligible", True),
        macro_status=kwargs.get("macro_status", "NEUTRAL"),
        macro_eligible=kwargs.get("macro_eligible", True),
    )
    return obj
