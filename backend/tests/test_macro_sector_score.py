"""Offline tests for deterministic sector macro scoring."""
import copy
import uuid

import pytest
from sqlalchemy import event, text

from database import DiscoverySessionLocal, source_engine
from models.discovery import GroupScore, MacroEntityImpact
from services.macro.macro_filter_summary import CATEGORIES
from services.macro.macro_sector_score import (
    CATEGORY_WEIGHTS,
    CONFIDENCE_MULTIPLIERS,
    IMPACT_NUMERIC_VALUES,
    MacroSectorScoreService,
    W_INVALID_CONFIDENCE,
    W_INVALID_IMPACT,
    W_LOW_COVERAGE,
    W_OVERALL_CONFLICT,
    W_PARTIAL,
    W_UNAVAILABLE,
    calculate_sector_macro_score,
    _status_from_score,
)


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM macro_entity_impacts"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM macro_entity_impacts"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _category_impacts(impact="POSITIVE", confidence="HIGH"):
    return {
        category: {
            "impact": impact,
            "confidence": confidence,
            "reason": f"{category} reason.",
            "evidence_refs": [f"{category}:DOC-001"],
        }
        for category in CATEGORIES
    }


def _mixed_impacts():
    return {
        CATEGORIES[0]: {"impact": "NEGATIVE", "confidence": "HIGH"},
        CATEGORIES[1]: {"impact": "NEGATIVE", "confidence": "MEDIUM"},
        CATEGORIES[2]: {"impact": "POSITIVE", "confidence": "LOW"},
        CATEGORIES[3]: {"impact": "NEUTRAL", "confidence": "MEDIUM"},
    }


def _make_group(session, run_id, sector="Auto", horizon="SHORT"):
    row = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="SECTOR",
        entity_name=sector,
        parent_sector="",
        parent_industry="",
        horizon=horizon,
        technical_return_score=11.0,
        technical_breadth_score=22.0,
        technical_volume_score=33.0,
        technical_consistency_score=44.0,
        technical_score=55.0,
        fundamental_growth_score=66.0,
        fundamental_profitability_score=67.0,
        fundamental_financial_strength_score=68.0,
        fundamental_earnings_quality_score=69.0,
        fundamental_score=70.0,
        macro_score=None,
        final_score=71.0,
        rank=3,
        data_coverage=88.0,
        warnings=["KEEP_ME"],
        calculation_details={
            "technical": {"status": "STRONG"},
            "fundamental": {"status": "POSITIVE"},
        },
    )
    session.add(row)
    session.commit()
    return row


def _make_impact(
    session,
    run_id,
    sector="Auto",
    category_impacts=None,
    overall="NEUTRAL",
):
    row = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_summary_id="summary-1",
        entity_type="SECTOR",
        entity_name=sector,
        parent_sector="",
        parent_industry="",
        category_impacts=category_impacts or _category_impacts(),
        overall_impact={
            "impact": overall,
            "confidence": "MEDIUM",
            "reason": "Overall.",
            "dominant_categories": [CATEGORIES[0]],
            "evidence_refs": [f"{CATEGORIES[0]}:DOC-001"],
        },
        impact=overall,
        confidence="MEDIUM",
        reason="Overall.",
        evidence_refs=[f"{CATEGORIES[0]}:DOC-001"],
        warnings=["ORIGINAL_IMPACT_WARNING"],
        status="COMPLETED",
        model_name="fake",
        prompt_version="test",
    )
    session.add(row)
    session.commit()
    return row


def _score(category_impacts, overall="NEUTRAL"):
    details, warnings = calculate_sector_macro_score(
        category_impacts,
        {"impact": overall},
    )
    return details, warnings


def test_impact_enum_conversion():
    assert IMPACT_NUMERIC_VALUES == {"POSITIVE": 100.0, "NEUTRAL": 50.0, "NEGATIVE": 0.0}


def test_equal_25_category_weights():
    assert CATEGORY_WEIGHTS == {category: 25.0 for category in CATEGORIES}


def test_high_confidence_multiplier():
    assert CONFIDENCE_MULTIPLIERS["HIGH"] == 1.0


def test_medium_confidence_multiplier():
    assert CONFIDENCE_MULTIPLIERS["MEDIUM"] == 0.75


def test_low_confidence_multiplier():
    assert CONFIDENCE_MULTIPLIERS["LOW"] == 0.5


def test_mixed_impact_score():
    details, _ = _score(_mixed_impacts(), overall="NEGATIVE")

    assert details["score"] == 29.17
    assert details["status"] == "NEGATIVE"
    assert details["coverage_pct"] == 100.0


def test_n_a_exclusion_from_denominator():
    impacts = _category_impacts("N_A", "LOW")
    impacts[CATEGORIES[0]] = {"impact": "POSITIVE", "confidence": "HIGH"}

    details, _ = _score(impacts, overall="POSITIVE")

    assert details["applicable_weight"] == 25.0
    assert details["available_weight"] == 25.0
    assert details["coverage_pct"] == 100.0
    assert details["score"] == 100.0


def test_uncertain_lowers_coverage_but_is_not_scored():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "UNCERTAIN", "confidence": "LOW"}

    details, warnings = _score(impacts, overall="POSITIVE")

    assert details["coverage_pct"] == 75.0
    assert details["categories"][CATEGORIES[0]]["available"] is False
    assert details["score"] == 100.0
    assert W_PARTIAL in warnings


def test_partial_score_weight_normalization():
    impacts = _category_impacts("N_A", "LOW")
    impacts[CATEGORIES[0]] = {"impact": "POSITIVE", "confidence": "LOW"}
    impacts[CATEGORIES[1]] = {"impact": "NEGATIVE", "confidence": "HIGH"}

    details, _ = _score(impacts, overall="NEUTRAL")

    assert details["score"] == 33.33
    assert details["effective_available_weight"] == 37.5


def test_all_categories_available():
    details, warnings = _score(_category_impacts("NEUTRAL", "HIGH"), overall="NEUTRAL")

    assert details["coverage_pct"] == 100.0
    assert details["confidence_quality_pct"] == 100.0
    assert W_PARTIAL not in warnings


def test_coverage_exactly_75_remains_eligible():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}

    details, _ = _score(impacts, overall="POSITIVE")

    assert details["coverage_pct"] == 75.0
    assert details["eligible_for_selection"] is True


def test_coverage_below_75_becomes_ineligible():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}
    impacts[CATEGORIES[1]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}

    details, warnings = _score(impacts, overall="POSITIVE")

    assert details["coverage_pct"] == 50.0
    assert details["eligible_for_selection"] is False
    assert W_LOW_COVERAGE in warnings


def test_low_coverage_preserves_score(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}
    impacts[CATEGORIES[1]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}
    _make_group(disc_session, run_id)
    _make_impact(disc_session, run_id, category_impacts=impacts, overall="POSITIVE")

    metadata = MacroSectorScoreService(disc_session).calculate_sector_scores(run_id)
    group = disc_session.query(GroupScore).filter_by(run_id=run_id).first()

    assert group.macro_score == 100.0
    assert group.calculation_details["macro"]["sector_score"]["eligible_for_selection"] is False
    assert metadata["sector_count"] == 1
    assert metadata["scored_sector_count"] == 1
    assert metadata["eligible_sector_count"] == 0
    assert metadata["ineligible_sector_count"] == 1
    assert metadata["very_positive_count"] == 1


def test_all_categories_n_a():
    details, warnings = _score(_category_impacts("N_A", "LOW"), overall="N_A")

    assert details["score"] is None
    assert details["coverage_pct"] is None
    assert details["status"] == "N_A"
    assert details["eligible_for_selection"] is True
    assert "ALL_MACRO_CATEGORIES_NOT_APPLICABLE" in warnings


def test_all_applicable_categories_uncertain():
    details, warnings = _score(_category_impacts("UNCERTAIN", "LOW"), overall="UNCERTAIN")

    assert details["score"] is None
    assert details["coverage_pct"] == 0.0
    assert details["status"] == "UNAVAILABLE"
    assert details["eligible_for_selection"] is False
    assert W_UNAVAILABLE in warnings


def test_confidence_quality_calculation():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "POSITIVE", "confidence": "MEDIUM"}
    impacts[CATEGORIES[1]] = {"impact": "POSITIVE", "confidence": "LOW"}

    details, _ = _score(impacts, overall="POSITIVE")

    assert details["confidence_quality_pct"] == 81.25


def test_every_macro_status_boundary():
    assert _status_from_score(100.0, 100.0, 100.0) == "VERY_POSITIVE"
    assert _status_from_score(80.0, 100.0, 100.0) == "VERY_POSITIVE"
    assert _status_from_score(79.99, 100.0, 100.0) == "POSITIVE"
    assert _status_from_score(60.0, 100.0, 100.0) == "POSITIVE"
    assert _status_from_score(59.99, 100.0, 100.0) == "NEUTRAL"
    assert _status_from_score(40.0, 100.0, 100.0) == "NEUTRAL"
    assert _status_from_score(39.99, 100.0, 100.0) == "NEGATIVE"
    assert _status_from_score(20.0, 100.0, 100.0) == "NEGATIVE"
    assert _status_from_score(19.99, 100.0, 100.0) == "VERY_NEGATIVE"
    assert _status_from_score(0.0, 100.0, 100.0) == "VERY_NEGATIVE"


def test_overall_impact_conflict_warning():
    details, warnings = _score(_category_impacts("POSITIVE", "HIGH"), overall="NEGATIVE")

    assert details["derived_broad_impact"] == "POSITIVE"
    assert W_OVERALL_CONFLICT in warnings


def test_uncertain_overall_impact_causes_no_conflict():
    _, warnings = _score(_category_impacts("POSITIVE", "HIGH"), overall="UNCERTAIN")

    assert W_OVERALL_CONFLICT not in warnings


def test_invalid_impact_enum_handling():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "BOOSTED", "confidence": "HIGH"}

    details, warnings = _score(impacts, overall="POSITIVE")

    assert details["categories"][CATEGORIES[0]]["impact"] == "UNCERTAIN"
    assert details["categories"][CATEGORIES[0]]["available"] is False
    assert W_INVALID_IMPACT in warnings


def test_invalid_confidence_enum_handling():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "POSITIVE", "confidence": "CERTAIN"}

    details, warnings = _score(impacts, overall="POSITIVE")

    assert details["categories"][CATEGORIES[0]]["confidence"] is None
    assert details["categories"][CATEGORIES[0]]["available"] is False
    assert W_INVALID_CONFIDENCE in warnings


def test_existing_technical_and_fundamental_data_remains_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    group = _make_group(disc_session, run_id)
    _make_impact(disc_session, run_id)
    before = {
        "technical_return_score": group.technical_return_score,
        "technical_breadth_score": group.technical_breadth_score,
        "technical_volume_score": group.technical_volume_score,
        "technical_consistency_score": group.technical_consistency_score,
        "technical_score": group.technical_score,
        "fundamental_growth_score": group.fundamental_growth_score,
        "fundamental_profitability_score": group.fundamental_profitability_score,
        "fundamental_financial_strength_score": group.fundamental_financial_strength_score,
        "fundamental_earnings_quality_score": group.fundamental_earnings_quality_score,
        "fundamental_score": group.fundamental_score,
        "final_score": group.final_score,
        "rank": group.rank,
        "data_coverage": group.data_coverage,
        "technical_calc": copy.deepcopy(group.calculation_details["technical"]),
        "fundamental_calc": copy.deepcopy(group.calculation_details["fundamental"]),
    }

    MacroSectorScoreService(disc_session).calculate_sector_scores(run_id)
    disc_session.expire(group)

    for key, expected in before.items():
        if key == "technical_calc":
            assert group.calculation_details["technical"] == expected
        elif key == "fundamental_calc":
            assert group.calculation_details["fundamental"] == expected
        else:
            assert getattr(group, key) == expected


def test_existing_macro_impact_rows_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id)
    impact = _make_impact(disc_session, run_id)
    before = copy.deepcopy(
        {
            "category_impacts": impact.category_impacts,
            "overall_impact": impact.overall_impact,
            "impact": impact.impact,
            "confidence": impact.confidence,
            "warnings": impact.warnings,
            "status": impact.status,
        }
    )

    MacroSectorScoreService(disc_session).calculate_sector_scores(run_id)
    disc_session.expire(impact)

    assert impact.category_impacts == before["category_impacts"]
    assert impact.overall_impact == before["overall_impact"]
    assert impact.impact == before["impact"]
    assert impact.confidence == before["confidence"]
    assert impact.warnings == before["warnings"]
    assert impact.status == before["status"]


def test_idempotent_update(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id)
    _make_impact(disc_session, run_id)
    service = MacroSectorScoreService(disc_session)

    first = service.calculate_sector_scores(run_id)
    group = disc_session.query(GroupScore).filter_by(run_id=run_id).first()
    first_calc = copy.deepcopy(group.calculation_details)
    first_warnings = list(group.warnings)
    second = service.calculate_sector_scores(run_id)
    disc_session.expire(group)

    assert first == second
    assert group.calculation_details == first_calc
    assert group.warnings == first_warnings


def test_no_llm_or_parallel_call(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id)
    _make_impact(disc_session, run_id)
    service = MacroSectorScoreService(disc_session)

    service.calculate_sector_scores(run_id)

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
        _make_impact(disc_session, run_id)
        MacroSectorScoreService(disc_session).calculate_sector_scores(run_id)
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []
