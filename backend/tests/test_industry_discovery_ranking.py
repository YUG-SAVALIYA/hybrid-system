"""Offline tests for deterministic final industry ranking and selection."""
import copy
import uuid

import pytest
from sqlalchemy import event, text

from database import DiscoverySessionLocal, source_engine
from models.discovery import DiscoverySelection, GroupScore
from services.ranking.industry_discovery_ranking import (
    W_FUNDAMENTAL_INELIGIBLE,
    W_LOW_COVERAGE,
    W_MACRO_INELIGIBLE,
    W_NO_ELIGIBLE,
    W_SELECTED_SECTOR_UNAVAILABLE,
    W_STALE_SELECTION_REMOVED,
    W_TECHNICAL_INELIGIBLE,
    IndustryDiscoveryRankingService,
    _status_from_score,
    calculate_final_industry_score,
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
    return_count=3,
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
            "industry_score": {
                "status": macro_status,
                "eligible_for_selection": macro_eligible,
            }
        },
    }


def _make_sector_selection(session, run_id, sector="Tech", horizon="SHORT", selected=True):
    row = DiscoverySelection(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon=horizon,
        entity_type="SECTOR",
        entity_name=sector,
        parent_sector="",
        parent_industry="",
        rank=1,
        final_score=90.0,
        selected=selected,
        calculation_details={"discovery": {"final_sector_score": {"rank": 1}}},
    )
    session.add(row)
    session.commit()
    return row


def _make_industry(
    session,
    run_id,
    sector="Tech",
    industry="Software",
    horizon="SHORT",
    technical=80.0,
    fundamental=70.0,
    macro=60.0,
    return_count=3,
    technical_coverage=100.0,
    fundamental_eligible=True,
    macro_status="NEUTRAL",
    macro_eligible=True,
    rank=99,
    final_score=None,
    warnings=None,
):
    row = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="INDUSTRY",
        entity_name=industry,
        parent_sector=sector,
        parent_industry="",
        horizon=horizon,
        constituent_count=6,
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
        final_score=final_score,
        rank=rank,
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


def _make_industry_obj(**kwargs):
    class Obj:
        pass

    obj = Obj()
    obj.parent_sector = kwargs.get("sector", "Tech")
    obj.technical_score = kwargs.get("technical", 80.0)
    obj.fundamental_score = kwargs.get("fundamental", 70.0)
    obj.macro_score = kwargs.get("macro", 60.0)
    obj.eligible_constituent_count = kwargs.get("return_count", 3)
    obj.data_coverage = kwargs.get("technical_coverage", 100.0)
    obj.warnings = kwargs.get("warnings", [])
    obj.calculation_details = _calc_details(
        return_count=kwargs.get("return_count", 3),
        technical_coverage=kwargs.get("technical_coverage", 100.0),
        fundamental_eligible=kwargs.get("fundamental_eligible", True),
        macro_status=kwargs.get("macro_status", "NEUTRAL"),
        macro_eligible=kwargs.get("macro_eligible", True),
    )
    return obj


def _get_industry(session, run_id, sector="Tech", industry="Software", horizon="SHORT"):
    return (
        session.query(GroupScore)
        .filter_by(
            run_id=run_id,
            entity_type="INDUSTRY",
            parent_sector=sector,
            entity_name=industry,
            horizon=horizon,
        )
        .first()
    )


def _industry_selections(session, run_id, horizon="SHORT"):
    return (
        session.query(DiscoverySelection)
        .filter_by(run_id=run_id, horizon=horizon, entity_type="INDUSTRY")
        .order_by(DiscoverySelection.parent_sector.asc(), DiscoverySelection.entity_name.asc())
        .all()
    )


def test_exact_40_40_20_score():
    details, _ = calculate_final_industry_score(_make_industry_obj())

    assert details["score"] == 72.0
    assert details["components"]["technical"]["weighted_contribution"] == 3200.0
    assert details["components"]["fundamental"]["weighted_contribution"] == 2800.0
    assert details["components"]["macro"]["weighted_contribution"] == 1200.0


def test_macro_n_a_exclusion():
    details, warnings = calculate_final_industry_score(
        _make_industry_obj(macro=None, macro_status="N_A")
    )

    assert details["applicable_weight"] == 80.0
    assert details["available_weight"] == 80.0
    assert details["score"] == 75.0
    assert details["components"]["macro"]["applicable"] is False
    assert W_MACRO_INELIGIBLE not in warnings


def test_missing_applicable_macro_renormalizes_but_remains_ineligible():
    details, warnings = calculate_final_industry_score(
        _make_industry_obj(macro=None, macro_eligible=False)
    )

    assert details["score"] == 75.0
    assert details["coverage_pct"] == 80.0
    assert details["eligible_for_selection"] is False
    assert W_MACRO_INELIGIBLE in warnings


def test_combined_coverage_calculation():
    details, _ = calculate_final_industry_score(
        _make_industry_obj(macro=None, macro_eligible=False)
    )

    assert details["applicable_weight"] == 100.0
    assert details["available_weight"] == 80.0
    assert details["coverage_pct"] == 80.0


def test_technical_eligibility():
    details, warnings = calculate_final_industry_score(_make_industry_obj(return_count=2))

    assert details["components"]["technical"]["eligible"] is False
    assert details["eligible_for_selection"] is False
    assert W_TECHNICAL_INELIGIBLE in warnings


def test_fundamental_eligibility():
    details, warnings = calculate_final_industry_score(
        _make_industry_obj(fundamental_eligible=False)
    )

    assert details["components"]["fundamental"]["eligible"] is False
    assert details["eligible_for_selection"] is False
    assert W_FUNDAMENTAL_INELIGIBLE in warnings


def test_macro_eligibility():
    details, warnings = calculate_final_industry_score(
        _make_industry_obj(macro_eligible=False)
    )

    assert details["components"]["macro"]["eligible"] is False
    assert details["eligible_for_selection"] is False
    assert W_MACRO_INELIGIBLE in warnings


def test_coverage_exactly_80_remains_eligible():
    details, warnings = calculate_final_industry_score(_make_industry_obj(macro=None))

    assert details["coverage_pct"] == 80.0
    assert details["eligible_for_selection"] is True
    assert W_LOW_COVERAGE not in warnings


def test_coverage_below_80_becomes_ineligible():
    details, warnings = calculate_final_industry_score(
        _make_industry_obj(fundamental=None, fundamental_eligible=False, macro=None)
    )

    assert details["coverage_pct"] == 40.0
    assert details["eligible_for_selection"] is False
    assert W_LOW_COVERAGE in warnings


def test_low_coverage_preserves_final_score(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(
        disc_session,
        run_id,
        fundamental=None,
        fundamental_eligible=False,
        macro=None,
    )

    IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    group = _get_industry(disc_session, run_id)

    assert group.final_score == 80.0
    assert group.calculation_details["discovery"]["final_industry_score"]["eligible_for_selection"] is False


def test_every_final_status_boundary():
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


def test_only_industries_inside_selected_sector_are_ranked(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id, sector="Tech")
    _make_industry(disc_session, run_id, sector="Tech", industry="Software")
    _make_industry(disc_session, run_id, sector="Finance", industry="Banking")

    result = IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["entity_name"] for item in result["ranked_industries"]] == ["Software"]
    assert _get_industry(disc_session, run_id, "Finance", "Banking").rank is None


def test_same_industry_name_under_another_sector_remains_isolated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id, sector="Tech")
    _make_industry(disc_session, run_id, sector="Tech", industry="Software", technical=90)
    other = _make_industry(
        disc_session,
        run_id,
        sector="Auto",
        industry="Software",
        technical=10,
        final_score=12.0,
    )

    IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    disc_session.expire(other)

    assert _get_industry(disc_session, run_id, "Tech", "Software").rank == 1
    assert other.final_score == 12.0
    assert other.rank is None


def test_eligible_industries_only_receive_ranks(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(disc_session, run_id, industry="Eligible", technical=90)
    _make_industry(disc_session, run_id, industry="Ineligible", technical=95, fundamental_eligible=False)

    IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert _get_industry(disc_session, run_id, industry="Eligible").rank == 1
    assert _get_industry(disc_session, run_id, industry="Ineligible").rank is None


def test_final_score_descending_ordering(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(disc_session, run_id, industry="Low", technical=70, fundamental=70, macro=70)
    _make_industry(disc_session, run_id, industry="High", technical=90, fundamental=90, macro=90)

    result = IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["entity_name"] for item in result["ranked_industries"]] == ["High", "Low"]


def test_alphabetical_tie_break(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(disc_session, run_id, industry="Beta", technical=80, fundamental=80, macro=80)
    _make_industry(disc_session, run_id, industry="Alpha", technical=80, fundamental=80, macro=80)

    result = IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert [item["entity_name"] for item in result["ranked_industries"]] == ["Alpha", "Beta"]


def test_ineligible_industry_rank_remains_null(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(disc_session, run_id, technical=None)

    IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert _get_industry(disc_session, run_id).rank is None


def test_top_one_winner_selection(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(disc_session, run_id, industry="Winner", technical=95, fundamental=95, macro=95)
    _make_industry(disc_session, run_id, industry="Runner", technical=90, fundamental=90, macro=90)

    result = IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    selected = [row for row in _industry_selections(disc_session, run_id) if row.selected]

    assert result["selected_industries"] == ["Winner"]
    assert len(selected) == 1
    assert selected[0].entity_name == "Winner"
    assert selected[0].rank == 1


def test_selected_industry_belongs_to_selected_sector(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id, sector="Finance")
    _make_industry(disc_session, run_id, sector="Tech", industry="Software", technical=99)
    _make_industry(disc_session, run_id, sector="Finance", industry="Banking", technical=80)

    IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    selected = [row for row in _industry_selections(disc_session, run_id) if row.selected]

    assert selected[0].entity_name == "Banking"
    assert selected[0].parent_sector == "Finance"


def test_no_selected_sector_behavior(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_industry(disc_session, run_id)
    stale = DiscoverySelection(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon="SHORT",
        entity_type="INDUSTRY",
        entity_name="Software",
        parent_sector="Tech",
        parent_industry="",
        selected=True,
    )
    disc_session.add(stale)
    disc_session.commit()

    result = IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert W_SELECTED_SECTOR_UNAVAILABLE in result["warnings"]
    assert result["metadata"]["selected_industry_count"] == 0
    assert result["metadata"]["stale_selection_count"] == 1
    assert [row for row in _industry_selections(disc_session, run_id) if row.selected] == []


def test_no_eligible_industry_behavior(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(disc_session, run_id, technical=None, fundamental=None, macro=None)
    disc_session.add(
        DiscoverySelection(
            id=str(uuid.uuid4()),
            run_id=run_id,
            horizon="SHORT",
            entity_type="INDUSTRY",
            entity_name="Old",
            parent_sector="Tech",
            parent_industry="",
            selected=True,
        )
    )
    disc_session.commit()

    result = IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")

    assert W_NO_ELIGIBLE in result["warnings"]
    assert W_STALE_SELECTION_REMOVED in result["warnings"]
    assert [row for row in _industry_selections(disc_session, run_id) if row.selected] == []


def test_previous_industry_selection_is_replaced(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    first = _make_industry(disc_session, run_id, industry="First", technical=95, fundamental=95, macro=95)
    second = _make_industry(disc_session, run_id, industry="Second", technical=80, fundamental=80, macro=80)
    service = IndustryDiscoveryRankingService(disc_session)
    service.rank_and_select(run_id, "SHORT")

    first.technical_score = 70.0
    first.fundamental_score = 70.0
    first.macro_score = 70.0
    second.technical_score = 99.0
    second.fundamental_score = 99.0
    second.macro_score = 99.0
    disc_session.commit()
    service.rank_and_select(run_id, "SHORT")

    selected = [row for row in _industry_selections(disc_session, run_id) if row.selected]
    stale = [row for row in _industry_selections(disc_session, run_id) if row.entity_name == "First"][0]
    assert selected[0].entity_name == "Second"
    assert stale.selected is False


def test_parent_sector_change_removes_stale_industry_selection(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    old_sector = _make_sector_selection(disc_session, run_id, sector="Tech")
    _make_industry(disc_session, run_id, sector="Tech", industry="Software")
    service = IndustryDiscoveryRankingService(disc_session)
    service.rank_and_select(run_id, "SHORT")

    old_sector.selected = False
    _make_sector_selection(disc_session, run_id, sector="Finance")
    _make_industry(disc_session, run_id, sector="Finance", industry="Banking")
    disc_session.commit()
    result = service.rank_and_select(run_id, "SHORT")

    active = [row for row in _industry_selections(disc_session, run_id) if row.selected]
    assert result["metadata"]["stale_selection_count"] == 1
    assert active[0].entity_name == "Banking"
    assert active[0].parent_sector == "Finance"


def test_short_mid_long_selections_remain_independent(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id, sector="Tech", horizon="SHORT")
    _make_sector_selection(disc_session, run_id, sector="Finance", horizon="MID")
    _make_sector_selection(disc_session, run_id, sector="Tech", horizon="LONG")
    _make_industry(disc_session, run_id, sector="Tech", industry="ShortSoft", horizon="SHORT")
    _make_industry(disc_session, run_id, sector="Finance", industry="MidBank", horizon="MID")
    _make_industry(disc_session, run_id, sector="Tech", industry="LongCloud", horizon="LONG")
    service = IndustryDiscoveryRankingService(disc_session)

    service.rank_and_select(run_id, "SHORT")
    service.rank_and_select(run_id, "MID")
    service.rank_and_select(run_id, "LONG")

    assert [row.entity_name for row in _industry_selections(disc_session, run_id, "SHORT") if row.selected] == ["ShortSoft"]
    assert [row.entity_name for row in _industry_selections(disc_session, run_id, "MID") if row.selected] == ["MidBank"]
    assert [row.entity_name for row in _industry_selections(disc_session, run_id, "LONG") if row.selected] == ["LongCloud"]


def test_industries_outside_selected_sector_have_stale_ranks_cleared(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id, sector="Tech")
    _make_industry(disc_session, run_id, sector="Tech", industry="Software")
    old = _make_industry(disc_session, run_id, sector="Finance", industry="Banking", rank=7)
    old_calc = copy.deepcopy(old.calculation_details)
    old_calc["discovery"] = {"final_industry_score": {"rank": 7, "score": 91.0}}
    old.calculation_details = old_calc
    disc_session.commit()

    result = IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    disc_session.expire(old)

    assert result["metadata"]["unselected_sector_rank_cleanup_count"] == 1
    assert old.rank is None
    assert old.calculation_details["discovery"]["final_industry_score"]["rank"] is None


def test_existing_technical_fundamental_and_macro_details_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    group = _make_industry(disc_session, run_id)
    before = {
        "technical": copy.deepcopy(group.calculation_details["technical"]),
        "fundamental": copy.deepcopy(group.calculation_details["fundamental"]),
        "macro": copy.deepcopy(group.calculation_details["macro"]),
    }

    IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    disc_session.expire(group)

    assert group.calculation_details["technical"] == before["technical"]
    assert group.calculation_details["fundamental"] == before["fundamental"]
    assert group.calculation_details["macro"] == before["macro"]


def test_idempotent_persistence(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(disc_session, run_id)
    service = IndustryDiscoveryRankingService(disc_session)

    first = service.rank_and_select(run_id, "SHORT")
    group = _get_industry(disc_session, run_id)
    first_calc = copy.deepcopy(group.calculation_details)
    first_selection_ids = [row.id for row in _industry_selections(disc_session, run_id)]
    second = service.rank_and_select(run_id, "SHORT")
    disc_session.expire(group)

    assert first["metadata"] == second["metadata"]
    assert group.calculation_details == first_calc
    assert [row.id for row in _industry_selections(disc_session, run_id)] == first_selection_ids


def test_no_llm_or_parallel_call(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_sector_selection(disc_session, run_id)
    _make_industry(disc_session, run_id)
    service = IndustryDiscoveryRankingService(disc_session)

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
        _make_sector_selection(disc_session, run_id)
        _make_industry(disc_session, run_id)
        IndustryDiscoveryRankingService(disc_session).rank_and_select(run_id, "SHORT")
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []
