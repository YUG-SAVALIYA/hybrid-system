"""Offline tests for deterministic basic-industry macro scoring."""
import copy
import uuid

import pytest
from sqlalchemy import event, text

from database import DiscoverySessionLocal, source_engine
from models.discovery import GroupScore, MacroEntityImpact
from services.macro.macro_basic_industry_score import (
    CATEGORY_WEIGHTS,
    CONFIDENCE_MULTIPLIERS,
    IMPACT_NUMERIC_VALUES,
    MacroBasicIndustryScoreService,
    W_INVALID_CONFIDENCE,
    W_INVALID_IMPACT,
    W_LOW_COVERAGE,
    W_OVERALL_CONFLICT,
    W_PARTIAL,
    W_STALE,
    W_UNAVAILABLE,
    _status_from_score,
    calculate_basic_industry_macro_score,
)
from services.macro.macro_filter_summary import CATEGORIES


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
        CATEGORIES[0]: {"impact": "POSITIVE", "confidence": "LOW"},
        CATEGORIES[1]: {"impact": "NEGATIVE", "confidence": "LOW"},
        CATEGORIES[2]: {"impact": "NEUTRAL", "confidence": "HIGH"},
        CATEGORIES[3]: {"impact": "POSITIVE", "confidence": "HIGH"},
    }


def _score(category_impacts, overall="POSITIVE", relationship="MORE_POSITIVE"):
    return calculate_basic_industry_macro_score(
        category_impacts,
        {"impact": overall, "relationship_to_parent_industry": relationship},
    )


def _make_group(
    session,
    run_id,
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    horizon="SHORT",
):
    row = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name=basic,
        parent_sector=sector,
        parent_industry=industry,
        horizon=horizon,
        technical_score=77.0,
        fundamental_score=66.0,
        macro_score=None,
        final_score=55.0,
        rank=4,
        data_coverage=88.0,
        warnings=["KEEP_ME"],
        calculation_details={
            "technical": {"keep": True},
            "fundamental": {"keep": True},
        },
    )
    session.add(row)
    session.commit()
    return row


def _make_impact(
    session,
    run_id,
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    category_impacts=None,
    overall="POSITIVE",
    relationship="MORE_POSITIVE",
):
    row = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_summary_id="summary",
        source_parent_impact_id="industry-impact",
        entity_type="BASIC_INDUSTRY",
        entity_name=basic,
        parent_sector=sector,
        parent_industry=industry,
        category_impacts=category_impacts or _category_impacts(),
        overall_impact={
            "impact": overall,
            "confidence": "MEDIUM",
            "reason": "Overall basic industry impact.",
            "dominant_categories": [CATEGORIES[0]],
            "evidence_refs": [f"{CATEGORIES[0]}:DOC-001"],
            "relationship_to_parent_industry": relationship,
        },
        impact=overall,
        confidence="MEDIUM",
        reason="Overall basic industry impact.",
        evidence_refs=[f"{CATEGORIES[0]}:DOC-001"],
        relationship_to_parent_industry=relationship,
        warnings=["KEEP_IMPACT_WARNING"],
        status="COMPLETED",
        model_name="fake",
        prompt_version="test",
    )
    session.add(row)
    session.commit()
    return row


def _group(
    session,
    run_id,
    sector="Tech",
    industry="Software",
    basic="Enterprise Software",
    horizon="SHORT",
):
    return (
        session.query(GroupScore)
        .filter_by(
            run_id=run_id,
            entity_type="BASIC_INDUSTRY",
            parent_sector=sector,
            parent_industry=industry,
            entity_name=basic,
            horizon=horizon,
        )
        .first()
    )


def test_impact_conversion():
    assert IMPACT_NUMERIC_VALUES == {"POSITIVE": 100.0, "NEUTRAL": 50.0, "NEGATIVE": 0.0}


def test_confidence_multipliers():
    assert CONFIDENCE_MULTIPLIERS == {"HIGH": 1.0, "MEDIUM": 0.75, "LOW": 0.5}


def test_exact_25_category_weights():
    assert CATEGORY_WEIGHTS == {category: 25.0 for category in CATEGORIES}


def test_mixed_impact_calculation():
    details, _ = _score(_mixed_impacts(), overall="POSITIVE")

    assert details["score"] == 66.67
    assert details["status"] == "POSITIVE"
    assert details["coverage_pct"] == 100.0


def test_n_a_denominator_exclusion():
    impacts = _category_impacts("N_A", "LOW")
    impacts[CATEGORIES[0]] = {"impact": "POSITIVE", "confidence": "HIGH"}

    details, _ = _score(impacts)

    assert details["applicable_weight"] == 25.0
    assert details["available_weight"] == 25.0
    assert details["score"] == 100.0


def test_uncertain_coverage_handling():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "UNCERTAIN", "confidence": "LOW"}

    details, warnings = _score(impacts)

    assert details["coverage_pct"] == 75.0
    assert details["categories"][CATEGORIES[0]]["available"] is False
    assert W_PARTIAL in warnings


def test_partial_score_normalization():
    impacts = _category_impacts("N_A", "LOW")
    impacts[CATEGORIES[0]] = {"impact": "POSITIVE", "confidence": "LOW"}
    impacts[CATEGORIES[1]] = {"impact": "NEGATIVE", "confidence": "HIGH"}

    details, _ = _score(impacts, overall="NEUTRAL")

    assert details["score"] == 33.33
    assert details["effective_available_weight"] == 37.5


def test_confidence_quality_calculation():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "POSITIVE", "confidence": "MEDIUM"}
    impacts[CATEGORIES[1]] = {"impact": "POSITIVE", "confidence": "LOW"}

    details, _ = _score(impacts)

    assert details["confidence_quality_pct"] == 81.25


def test_coverage_exactly_75():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}

    details, _ = _score(impacts)

    assert details["coverage_pct"] == 75.0
    assert details["eligible_for_selection"] is True


def test_coverage_below_75():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}
    impacts[CATEGORIES[1]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}

    details, warnings = _score(impacts)

    assert details["coverage_pct"] == 50.0
    assert details["eligible_for_selection"] is False
    assert W_LOW_COVERAGE in warnings


def test_low_coverage_preserves_score(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}
    impacts[CATEGORIES[1]] = {"impact": "UNCERTAIN", "confidence": "HIGH"}
    _make_group(disc_session, run_id)
    _make_impact(disc_session, run_id, category_impacts=impacts)

    MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)
    group = _group(disc_session, run_id)

    assert group.macro_score == 100.0
    assert group.calculation_details["macro"]["basic_industry_score"]["eligible_for_selection"] is False


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


def test_qualitative_uncertain_causes_no_conflict():
    _, warnings = _score(_category_impacts("POSITIVE", "HIGH"), overall="UNCERTAIN")

    assert W_OVERALL_CONFLICT not in warnings


def test_parent_industry_relationship_does_not_alter_score():
    details_a, _ = _score(_mixed_impacts(), relationship="MORE_POSITIVE")
    details_b, _ = _score(_mixed_impacts(), relationship="MORE_NEGATIVE")

    assert details_a["score"] == details_b["score"]
    assert details_a["relationship_to_parent_industry"] == "MORE_POSITIVE"
    assert details_b["relationship_to_parent_industry"] == "MORE_NEGATIVE"


def test_invalid_impact_handling():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "BOOSTED", "confidence": "HIGH"}

    details, warnings = _score(impacts)

    assert details["categories"][CATEGORIES[0]]["impact"] == "UNCERTAIN"
    assert details["categories"][CATEGORIES[0]]["available"] is False
    assert W_INVALID_IMPACT in warnings


def test_invalid_confidence_handling():
    impacts = _category_impacts("POSITIVE", "HIGH")
    impacts[CATEGORIES[0]] = {"impact": "POSITIVE", "confidence": "CERTAIN"}

    details, warnings = _score(impacts)

    assert details["categories"][CATEGORIES[0]]["confidence"] is None
    assert details["categories"][CATEGORIES[0]]["available"] is False
    assert W_INVALID_CONFIDENCE in warnings


def test_one_impact_updates_all_matching_horizons(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    for horizon in ("SHORT", "MID", "LONG"):
        _make_group(disc_session, run_id, horizon=horizon)
    _make_impact(disc_session, run_id)

    metadata = MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)

    assert metadata["impact_count"] == 1
    assert metadata["group_score_row_count"] == 3
    assert metadata["scored_rows_by_horizon"] == {"LONG": 1, "MID": 1, "SHORT": 1}
    assert all(_group(disc_session, run_id, horizon=h).macro_score == 100.0 for h in ("SHORT", "MID", "LONG"))


def test_same_basic_industry_name_under_another_hierarchy_isolated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, sector="Tech", industry="Software", basic="Tools")
    _make_group(disc_session, run_id, sector="Auto", industry="Software", basic="Tools")
    _make_impact(disc_session, run_id, sector="Tech", industry="Software", basic="Tools", category_impacts=_category_impacts("POSITIVE", "HIGH"))
    _make_impact(disc_session, run_id, sector="Auto", industry="Software", basic="Tools", category_impacts=_category_impacts("NEGATIVE", "HIGH"), overall="NEGATIVE")

    MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)

    assert _group(disc_session, run_id, "Tech", "Software", "Tools").macro_score == 100.0
    assert _group(disc_session, run_id, "Auto", "Software", "Tools").macro_score == 0.0


def test_complete_hierarchy_is_used_for_matching(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id, sector="Tech", industry="Software", basic="Tools")
    other = _make_group(disc_session, run_id, sector="Tech", industry="Hardware", basic="Tools")
    _make_impact(disc_session, run_id, sector="Tech", industry="Software", basic="Tools")

    MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)
    disc_session.expire(other)

    assert _group(disc_session, run_id, "Tech", "Software", "Tools").macro_score == 100.0
    assert other.macro_score is None


def test_stale_score_cleanup(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    group = _make_group(disc_session, run_id)
    group.macro_score = 75.0
    group.calculation_details = {"macro": {"basic_industry_score": {"score": 75.0, "status": "POSITIVE"}}}
    disc_session.commit()

    metadata = MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)
    disc_session.expire(group)

    assert metadata["stale_score_count"] == 1
    assert group.macro_score is None
    assert group.calculation_details["macro"]["basic_industry_score"]["reason"] == W_STALE
    assert W_STALE in group.warnings


def test_cleanup_affects_only_supplied_run(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    other_run = f"run_{uuid.uuid4().hex[:6]}"
    group = _make_group(disc_session, run_id)
    other = _make_group(disc_session, other_run)
    for row in (group, other):
        row.macro_score = 75.0
        row.calculation_details = {"macro": {"basic_industry_score": {"score": 75.0, "status": "POSITIVE"}}}
    disc_session.commit()

    MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)
    disc_session.expire(group)
    disc_session.expire(other)

    assert group.macro_score is None
    assert other.macro_score == 75.0


def test_technical_and_fundamental_data_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    group = _make_group(disc_session, run_id)
    _make_impact(disc_session, run_id)
    before = {
        "technical_score": group.technical_score,
        "fundamental_score": group.fundamental_score,
        "final_score": group.final_score,
        "rank": group.rank,
        "technical": copy.deepcopy(group.calculation_details["technical"]),
        "fundamental": copy.deepcopy(group.calculation_details["fundamental"]),
    }

    MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)
    disc_session.expire(group)

    assert group.technical_score == before["technical_score"]
    assert group.fundamental_score == before["fundamental_score"]
    assert group.final_score == before["final_score"]
    assert group.rank == before["rank"]
    assert group.calculation_details["technical"] == before["technical"]
    assert group.calculation_details["fundamental"] == before["fundamental"]


def test_existing_impact_rows_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id)
    impact = _make_impact(disc_session, run_id)
    before = copy.deepcopy(
        {
            "category_impacts": impact.category_impacts,
            "overall_impact": impact.overall_impact,
            "impact": impact.impact,
            "warnings": impact.warnings,
            "status": impact.status,
        }
    )

    MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)
    disc_session.expire(impact)

    assert impact.category_impacts == before["category_impacts"]
    assert impact.overall_impact == before["overall_impact"]
    assert impact.impact == before["impact"]
    assert impact.warnings == before["warnings"]
    assert impact.status == before["status"]


def test_idempotent_update(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id)
    _make_impact(disc_session, run_id)
    service = MacroBasicIndustryScoreService(disc_session)

    first = service.calculate_basic_industry_scores(run_id)
    group = _group(disc_session, run_id)
    first_calc = copy.deepcopy(group.calculation_details)
    first_warnings = list(group.warnings)
    second = service.calculate_basic_industry_scores(run_id)
    disc_session.expire(group)

    assert first == second
    assert group.calculation_details == first_calc
    assert group.warnings == first_warnings


def test_no_llm_or_parallel_call(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_group(disc_session, run_id)
    _make_impact(disc_session, run_id)
    service = MacroBasicIndustryScoreService(disc_session)

    service.calculate_basic_industry_scores(run_id)

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
        MacroBasicIndustryScoreService(disc_session).calculate_basic_industry_scores(run_id)
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []
