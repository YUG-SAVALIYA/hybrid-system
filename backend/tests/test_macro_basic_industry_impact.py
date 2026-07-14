"""Offline tests for MacroBasicIndustryImpactService."""
import copy
import datetime
import json
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, text

from database import DiscoverySessionLocal, source_engine
from models.discovery import (
    DiscoverySelection,
    GroupScore,
    MacroEntityImpact,
    MacroSummary,
)
from services.macro.macro_basic_industry_impact import (
    MAX_BASIC_INDUSTRIES_PER_BATCH,
    MacroBasicIndustryImpactService,
    VALID_CONFIDENCES,
    VALID_IMPACTS,
    VALID_RELATIONSHIPS,
    W_DUPLICATE_BASIC,
    W_EXTRA_BASIC,
    W_INVALID_CATEGORY,
    W_INVALID_OVERALL,
    W_INVALID_RELATIONSHIP,
    W_LLM_INVALID,
    W_MISSING_BASIC,
    W_MISSING_EVIDENCE,
    W_UNKNOWN_EVIDENCE,
    _fallback_basic_industry,
    _validate_batch_response,
)
from services.macro.macro_filter_summary import CATEGORIES
from services.macro.macro_sector_impact import _build_allowed_evidence_refs


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM macro_entity_impacts"))
    session.execute(text("DELETE FROM macro_summaries"))
    session.execute(text("DELETE FROM discovery_selections"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM macro_entity_impacts"))
    session.execute(text("DELETE FROM macro_summaries"))
    session.execute(text("DELETE FROM discovery_selections"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _llm(responses):
    mock = MagicMock()
    mock.call.side_effect = responses
    return mock


def _summary_payload():
    category_summaries = {}
    for category in CATEGORIES:
        category_summaries[category] = {
            "category": category,
            "condition": "STABLE",
            "summary": f"Summary for {category}",
            "summary_source_ids": ["DOC-001"],
            "key_developments": [
                {"development": "Development", "direction": "POSITIVE", "source_ids": ["DOC-001"]}
            ],
            "contradictions": [],
            "missing_information": [],
            "used_source_ids": ["DOC-001"],
            "ignored_source_ids": ["DOC-999"],
        }
    overall = {
        "overall_summary": "Mixed macro environment.",
        "dominant_condition": "MIXED",
        "dominant_themes": [],
        "cross_category_conflicts": [],
        "category_conditions": {category: "STABLE" for category in CATEGORIES},
        "missing_categories": [],
    }
    return category_summaries, overall


def _make_summary(session, run_id):
    category_summaries, overall = _summary_payload()
    row = MacroSummary(
        id=f"summary-{uuid.uuid4().hex[:6]}",
        run_id=run_id,
        source_batch_id="batch",
        summary_type="MACRO_FILTER",
        status="COMPLETED",
        model_name="fake",
        prompt_version="test",
        category_summaries=category_summaries,
        overall_synthesis=overall,
        document_statistics={},
        warnings=[],
        created_at=datetime.datetime.utcnow(),
        updated_at=datetime.datetime.utcnow(),
    )
    session.add(row)
    session.commit()
    return row


def _make_selection(session, run_id, sector, industry, horizon="SHORT", selected=True):
    row = DiscoverySelection(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon=horizon,
        entity_type="INDUSTRY",
        entity_name=industry,
        parent_sector=sector,
        parent_industry="",
        rank=1,
        final_score=80.0,
        technical_score=80.0,
        fundamental_score=80.0,
        macro_score=80.0,
        selected=selected,
        selection_reason="test",
        calculation_details={},
    )
    session.add(row)
    session.commit()
    return row


def _make_basic_group(session, run_id, sector, industry, basic, horizon="SHORT", technical=77.0, fundamental=66.0):
    row = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name=basic,
        parent_sector=sector,
        parent_industry=industry,
        horizon=horizon,
        technical_score=technical,
        fundamental_score=fundamental,
        macro_score=None,
        final_score=None,
        rank=None,
        warnings=["KEEP_GROUP_WARNING"],
        calculation_details={"technical": {"keep": True}, "fundamental": {"keep": True}},
    )
    session.add(row)
    session.commit()
    return row


def _make_sector_impact(session, run_id, sector, impact="NEUTRAL"):
    row = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_summary_id="summary",
        entity_type="SECTOR",
        entity_name=sector,
        parent_sector="",
        parent_industry="",
        category_impacts={},
        overall_impact={"impact": impact, "confidence": "MEDIUM", "reason": "Sector reason."},
        impact=impact,
        confidence="MEDIUM",
        reason="Sector reason.",
        evidence_refs=[],
        warnings=["KEEP_SECTOR_IMPACT_WARNING"],
        status="COMPLETED",
        model_name="fake",
        prompt_version="test",
    )
    session.add(row)
    session.commit()
    return row


def _make_parent_industry_impact(session, run_id, sector, industry, impact="NEGATIVE"):
    row = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_summary_id="summary",
        entity_type="INDUSTRY",
        entity_name=industry,
        parent_sector=sector,
        parent_industry="",
        category_impacts={
            category: {
                "impact": impact,
                "confidence": "MEDIUM",
                "reason": f"{industry} parent reason.",
                "evidence_refs": [f"{category}:DOC-001"],
            }
            for category in CATEGORIES
        },
        overall_impact={
            "impact": impact,
            "confidence": "MEDIUM",
            "reason": f"{industry} parent industry impact.",
            "dominant_categories": [CATEGORIES[0]],
            "evidence_refs": [f"{CATEGORIES[0]}:DOC-001"],
            "relationship_to_parent_sector": "SIMILAR",
        },
        impact=impact,
        confidence="MEDIUM",
        reason=f"{industry} parent industry impact.",
        evidence_refs=[f"{CATEGORIES[0]}:DOC-001"],
        relationship_to_parent_sector="SIMILAR",
        warnings=["KEEP_INDUSTRY_IMPACT_WARNING"],
        status="COMPLETED",
        model_name="fake",
        prompt_version="test",
    )
    session.add(row)
    session.commit()
    return row


def _basic_item(basic, impact="POSITIVE", confidence="MEDIUM", relationship="MORE_POSITIVE", include_all=True):
    category_impacts = {
        category: {
            "impact": impact,
            "confidence": confidence,
            "reason": f"{basic} reason for {category}.",
            "evidence_refs": [f"{category}:DOC-001"],
        }
        for category in CATEGORIES
    }
    if not include_all:
        category_impacts.pop(CATEGORIES[-1])
    return {
        "basic_industry": basic,
        "category_impacts": category_impacts,
        "overall_impact": {
            "impact": impact,
            "confidence": confidence,
            "reason": f"{basic} net basic industry macro effect.",
            "dominant_categories": [CATEGORIES[0]],
            "evidence_refs": [f"{CATEGORIES[0]}:DOC-001"],
            "relationship_to_parent_industry": relationship,
        },
    }


def _batch_response(sector, industry, basics, impact="POSITIVE"):
    return json.dumps({
        "parent_sector": sector,
        "parent_industry": industry,
        "basic_industries": [_basic_item(basic, impact=impact) for basic in basics],
    })


def _allowed_refs():
    category_summaries, _ = _summary_payload()
    return _build_allowed_evidence_refs(category_summaries)


def _setup(session, run_id, sector="Technology", industry="Software", basics=None, responses=None):
    basics = basics or ["Enterprise Software"]
    _make_summary(session, run_id)
    _make_selection(session, run_id, sector, industry, "SHORT")
    _make_sector_impact(session, run_id, sector)
    _make_parent_industry_impact(session, run_id, sector, industry)
    for basic in basics:
        _make_basic_group(session, run_id, sector, industry, basic)
    return MacroBasicIndustryImpactService(
        session,
        llm_caller=_llm(responses or [_batch_response(sector, industry, basics)]),
    )


def _basic_impacts(session, run_id):
    return (
        session.query(MacroEntityImpact)
        .filter_by(run_id=run_id, entity_type="BASIC_INDUSTRY")
        .order_by(
            MacroEntityImpact.parent_sector.asc(),
            MacroEntityImpact.parent_industry.asc(),
            MacroEntityImpact.entity_name.asc(),
        )
        .all()
    )


def test_active_selected_industries_are_loaded(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id, "Technology", "Software")

    svc = MacroBasicIndustryImpactService(disc_session, llm_caller=_llm([]))

    assert svc._selected_industries_by_horizon(run_id) == {"SHORT": [("Technology", "Software")]}


def test_short_mid_long_selections_are_read_independently(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id, "Tech", "Software", "SHORT")
    _make_selection(disc_session, run_id, "Finance", "Banks", "MID")
    _make_selection(disc_session, run_id, "Energy", "Oil", "LONG")

    selected = MacroBasicIndustryImpactService(disc_session, llm_caller=_llm([]))._selected_industries_by_horizon(run_id)

    assert selected == {
        "LONG": [("Energy", "Oil")],
        "MID": [("Finance", "Banks")],
        "SHORT": [("Tech", "Software")],
    }


def test_duplicate_hierarchy_selected_across_horizons_processed_once(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    _make_selection(disc_session, run_id, "Technology", "Software", "SHORT")
    _make_selection(disc_session, run_id, "Technology", "Software", "LONG")
    _make_parent_industry_impact(disc_session, run_id, "Technology", "Software")
    _make_basic_group(disc_session, run_id, "Technology", "Software", "Enterprise Software")
    llm = _llm([_batch_response("Technology", "Software", ["Enterprise Software"])])
    svc = MacroBasicIndustryImpactService(disc_session, llm_caller=llm)

    result = svc.generate_basic_industry_impacts(run_id)

    assert result["metadata"]["selected_industry_count"] == 2
    assert result["metadata"]["unique_selected_hierarchy_count"] == 1
    assert llm.call.call_count == 1


def test_same_industry_name_under_different_sectors_remains_separate(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    for sector in ["Auto", "Tech"]:
        _make_selection(disc_session, run_id, sector, "Software")
        _make_parent_industry_impact(disc_session, run_id, sector, "Software")
        _make_basic_group(disc_session, run_id, sector, "Software", "Tools")
    svc = MacroBasicIndustryImpactService(
        disc_session,
        llm_caller=_llm([
            _batch_response("Auto", "Software", ["Tools"]),
            _batch_response("Tech", "Software", ["Tools"]),
        ]),
    )

    svc.generate_basic_industry_impacts(run_id)

    assert [(row.parent_sector, row.parent_industry, row.entity_name) for row in _basic_impacts(disc_session, run_id)] == [
        ("Auto", "Software", "Tools"),
        ("Tech", "Software", "Tools"),
    ]


def test_unselected_industries_are_ignored(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    _make_selection(disc_session, run_id, "Selected", "A", selected=True)
    _make_selection(disc_session, run_id, "Selected", "B", selected=False)
    _make_parent_industry_impact(disc_session, run_id, "Selected", "A")
    _make_parent_industry_impact(disc_session, run_id, "Selected", "B")
    _make_basic_group(disc_session, run_id, "Selected", "A", "A1")
    _make_basic_group(disc_session, run_id, "Selected", "B", "B1")

    MacroBasicIndustryImpactService(
        disc_session,
        llm_caller=_llm([_batch_response("Selected", "A", ["A1"])]),
    ).generate_basic_industry_impacts(run_id)

    assert [(row.parent_industry, row.entity_name) for row in _basic_impacts(disc_session, run_id)] == [("A", "A1")]


def test_basic_industries_load_only_from_selected_hierarchy(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_group(disc_session, run_id, "Selected", "A", "A1")
    _make_basic_group(disc_session, run_id, "Selected", "B", "B1")

    svc = MacroBasicIndustryImpactService(disc_session, llm_caller=_llm([]))

    assert svc._load_basic_industry_universe(run_id, "Selected", "A") == ["A1"]


def test_same_basic_industry_name_under_another_hierarchy_remains_isolated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    for industry in ["A", "B"]:
        _make_selection(disc_session, run_id, "Tech", industry)
        _make_parent_industry_impact(disc_session, run_id, "Tech", industry)
        _make_basic_group(disc_session, run_id, "Tech", industry, "Tools")
    svc = MacroBasicIndustryImpactService(
        disc_session,
        llm_caller=_llm([
            _batch_response("Tech", "A", ["Tools"]),
            _batch_response("Tech", "B", ["Tools"]),
        ]),
    )

    svc.generate_basic_industry_impacts(run_id)

    assert [(row.parent_industry, row.entity_name) for row in _basic_impacts(disc_session, run_id)] == [("A", "Tools"), ("B", "Tools")]


def test_empty_and_duplicate_names_are_removed(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_basic_group(disc_session, run_id, "Tech", "Software", "Tools", "SHORT")
    _make_basic_group(disc_session, run_id, "Tech", "Software", "Tools", "MID")
    _make_basic_group(disc_session, run_id, "Tech", "Software", "", "LONG")

    assert MacroBasicIndustryImpactService(disc_session, llm_caller=_llm([]))._load_basic_industry_universe(run_id, "Tech", "Software") == ["Tools"]


def test_names_are_sorted_alphabetically(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    for basic in ["Zeta", "Alpha", "Beta"]:
        _make_basic_group(disc_session, run_id, "Tech", "Software", basic)

    assert MacroBasicIndustryImpactService(disc_session, llm_caller=_llm([]))._load_basic_industry_universe(run_id, "Tech", "Software") == ["Alpha", "Beta", "Zeta"]


def test_maximum_eight_basic_industries_per_batch(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    basics = [f"Basic {index:02d}" for index in range(9)]
    svc = _setup(
        disc_session,
        run_id,
        basics=basics,
        responses=[
            _batch_response("Technology", "Software", basics[:8]),
            _batch_response("Technology", "Software", basics[8:]),
        ],
    )

    svc.generate_basic_industry_impacts(run_id)

    assert MAX_BASIC_INDUSTRIES_PER_BATCH == 8
    assert [len(batch) for batch in svc.last_batches[("Technology", "Software")]] == [8, 1]


def test_multiple_selected_hierarchies_create_separate_batch_groups(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    for sector, industry, basic in [("A", "A1", "A-basic"), ("B", "B1", "B-basic")]:
        _make_selection(disc_session, run_id, sector, industry)
        _make_parent_industry_impact(disc_session, run_id, sector, industry)
        _make_basic_group(disc_session, run_id, sector, industry, basic)
    svc = MacroBasicIndustryImpactService(
        disc_session,
        llm_caller=_llm([
            _batch_response("A", "A1", ["A-basic"]),
            _batch_response("B", "B1", ["B-basic"]),
        ]),
    )

    result = svc.generate_basic_industry_impacts(run_id)

    assert result["metadata"]["parent_industry_count_processed"] == 2
    assert result["metadata"]["llm_call_count"] == 2


def test_parent_industry_impact_supplied_as_context(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)

    svc.generate_basic_industry_impacts(run_id)

    prompt = svc._llm.call.call_args_list[0][0][0]
    assert "parent_industry_impact" in prompt
    assert "Software parent industry impact." in prompt


def test_parent_industry_impact_not_blindly_copied(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id, responses=[_batch_response("Technology", "Software", ["Enterprise Software"], impact="POSITIVE")])

    svc.generate_basic_industry_impacts(run_id)
    row = _basic_impacts(disc_session, run_id)[0]

    assert row.impact == "POSITIVE"
    assert row.impact != "NEGATIVE"


def test_all_four_category_impacts_required():
    raw = {
        "parent_sector": "Tech",
        "parent_industry": "Software",
        "basic_industries": [_basic_item("Tools", include_all=False)],
    }

    outputs, warnings_by_item, _ = _validate_batch_response(raw, "Tech", "Software", ["Tools"], _allowed_refs())

    assert outputs == {}
    assert W_INVALID_CATEGORY in warnings_by_item["Tools"]


def test_valid_impact_and_confidence_enums():
    assert VALID_IMPACTS == {"POSITIVE", "NEGATIVE", "NEUTRAL", "N_A", "UNCERTAIN"}
    assert VALID_CONFIDENCES == {"HIGH", "MEDIUM", "LOW"}


def test_parent_relationship_validation():
    item = _basic_item("Tools")
    item["overall_impact"]["relationship_to_parent_industry"] = "COPIED"

    outputs, warnings_by_item, _ = _validate_batch_response(
        {"parent_sector": "Tech", "parent_industry": "Software", "basic_industries": [item]},
        "Tech",
        "Software",
        ["Tools"],
        _allowed_refs(),
    )

    assert outputs["Tools"]["overall_impact"]["relationship_to_parent_industry"] == "UNCERTAIN"
    assert W_INVALID_RELATIONSHIP in warnings_by_item["Tools"]
    assert "SIMILAR" in VALID_RELATIONSHIPS


def test_missing_requested_basic_industry_detection():
    outputs, warnings_by_item, batch_warnings = _validate_batch_response(
        {"parent_sector": "Tech", "parent_industry": "Software", "basic_industries": [_basic_item("A")]},
        "Tech",
        "Software",
        ["A", "B"],
        _allowed_refs(),
    )

    assert "A" in outputs
    assert W_MISSING_BASIC in batch_warnings
    assert W_MISSING_BASIC in warnings_by_item["B"]


def test_extra_entity_removal():
    outputs, _, batch_warnings = _validate_batch_response(
        {"parent_sector": "Tech", "parent_industry": "Software", "basic_industries": [_basic_item("A"), _basic_item("Extra")]},
        "Tech",
        "Software",
        ["A"],
        _allowed_refs(),
    )

    assert list(outputs) == ["A"]
    assert W_EXTRA_BASIC in batch_warnings


def test_duplicate_entity_removal():
    outputs, _, batch_warnings = _validate_batch_response(
        {"parent_sector": "Tech", "parent_industry": "Software", "basic_industries": [_basic_item("A"), _basic_item("A")]},
        "Tech",
        "Software",
        ["A"],
        _allowed_refs(),
    )

    assert list(outputs) == ["A"]
    assert W_DUPLICATE_BASIC in batch_warnings


def test_namespaced_evidence_validation():
    outputs, _, _ = _validate_batch_response(
        {"parent_sector": "Tech", "parent_industry": "Software", "basic_industries": [_basic_item("A")]},
        "Tech",
        "Software",
        ["A"],
        _allowed_refs(),
    )

    assert outputs["A"]["category_impacts"][CATEGORIES[0]]["evidence_refs"] == [f"{CATEGORIES[0]}:DOC-001"]


def test_same_doc_id_in_different_categories_remains_distinct():
    refs = _allowed_refs()

    assert f"{CATEGORIES[0]}:DOC-001" in refs[CATEGORIES[0]]
    assert f"{CATEGORIES[1]}:DOC-001" in refs[CATEGORIES[1]]
    assert f"{CATEGORIES[0]}:DOC-001" != f"{CATEGORIES[1]}:DOC-001"


def test_unknown_references_are_removed():
    item = _basic_item("A")
    item["category_impacts"][CATEGORIES[0]]["evidence_refs"] = [f"{CATEGORIES[0]}:DOC-999"]

    outputs, warnings_by_item, _ = _validate_batch_response(
        {"parent_sector": "Tech", "parent_industry": "Software", "basic_industries": [item]},
        "Tech",
        "Software",
        ["A"],
        _allowed_refs(),
    )

    assert outputs["A"]["category_impacts"][CATEGORIES[0]]["evidence_refs"] == []
    assert W_UNKNOWN_EVIDENCE in warnings_by_item["A"]


def test_missing_evidence_warning():
    item = _basic_item("A")
    item["category_impacts"][CATEGORIES[0]]["evidence_refs"] = []

    _, warnings_by_item, _ = _validate_batch_response(
        {"parent_sector": "Tech", "parent_industry": "Software", "basic_industries": [item]},
        "Tech",
        "Software",
        ["A"],
        _allowed_refs(),
    )

    assert W_MISSING_EVIDENCE in warnings_by_item["A"]


def test_forbidden_score_rank_stock_recommendation_selection_fields_removed():
    item = _basic_item("A")
    item["score"] = 99
    item["stock_recommendation"] = "BUY ABC"
    item["selected"] = True

    outputs, _, batch_warnings = _validate_batch_response(
        {"parent_sector": "Tech", "parent_industry": "Software", "basic_industries": [item]},
        "Tech",
        "Software",
        ["A"],
        _allowed_refs(),
    )

    serialized = json.dumps(outputs)
    assert "stock_recommendation" not in serialized
    assert "\"score\"" not in serialized
    assert "\"selected\"" not in serialized
    assert W_INVALID_OVERALL in batch_warnings


def test_one_repair_attempt(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id, responses=["not json", _batch_response("Technology", "Software", ["Enterprise Software"])])

    svc.generate_basic_industry_impacts(run_id)

    assert svc._llm.call.call_count == 2
    assert _basic_impacts(disc_session, run_id)[0].impact == "POSITIVE"


def test_invalid_repaired_response_uses_deterministic_fallback(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id, responses=["not json", "still not json"])

    svc.generate_basic_industry_impacts(run_id)
    row = _basic_impacts(disc_session, run_id)[0]

    assert row.status == "FALLBACK"
    assert row.overall_impact == _fallback_basic_industry("Enterprise Software")["overall_impact"]
    assert W_LLM_INVALID in row.warnings


def test_one_failed_batch_preserves_successful_batches(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    basics = [f"Basic {index:02d}" for index in range(9)]
    svc = _setup(
        disc_session,
        run_id,
        basics=basics,
        responses=[_batch_response("Technology", "Software", basics[:8]), "bad", "bad again"],
    )

    svc.generate_basic_industry_impacts(run_id)
    rows = _basic_impacts(disc_session, run_id)

    assert len(rows) == 9
    assert sum(1 for row in rows if row.status == "FALLBACK") == 1
    assert sum(1 for row in rows if row.status != "FALLBACK") == 8


def test_idempotent_persistence(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)
    first = svc.generate_basic_industry_impacts(run_id)["impact_ids"]
    svc._llm = _llm([_batch_response("Technology", "Software", ["Enterprise Software"], impact="NEGATIVE")])
    second = svc.generate_basic_industry_impacts(run_id)["impact_ids"]

    assert first == second
    assert len(_basic_impacts(disc_session, run_id)) == 1
    assert _basic_impacts(disc_session, run_id)[0].impact == "NEGATIVE"


def test_stale_cleanup_affects_only_supplied_run(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    other_run = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)
    stale = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="BASIC_INDUSTRY",
        entity_name="Stale",
        parent_sector="OldSector",
        parent_industry="OldIndustry",
        status="COMPLETED",
    )
    other = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=other_run,
        entity_type="BASIC_INDUSTRY",
        entity_name="OtherRunStale",
        parent_sector="OldSector",
        parent_industry="OldIndustry",
        status="COMPLETED",
    )
    disc_session.add_all([stale, other])
    disc_session.commit()

    result = svc.generate_basic_industry_impacts(run_id)

    assert result["metadata"]["stale_impact_count"] == 1
    assert disc_session.query(MacroEntityImpact).filter_by(id=stale.id).first() is None
    assert disc_session.query(MacroEntityImpact).filter_by(id=other.id).first() is not None


def test_sector_and_industry_impact_rows_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)
    sector_row = disc_session.query(MacroEntityImpact).filter_by(run_id=run_id, entity_type="SECTOR").first()
    industry_row = disc_session.query(MacroEntityImpact).filter_by(run_id=run_id, entity_type="INDUSTRY").first()
    before_sector = {"impact": sector_row.impact, "warnings": list(sector_row.warnings), "overall": copy.deepcopy(sector_row.overall_impact)}
    before_industry = {"impact": industry_row.impact, "warnings": list(industry_row.warnings), "overall": copy.deepcopy(industry_row.overall_impact)}

    svc.generate_basic_industry_impacts(run_id)
    disc_session.expire(sector_row)
    disc_session.expire(industry_row)

    assert sector_row.impact == before_sector["impact"]
    assert sector_row.warnings == before_sector["warnings"]
    assert sector_row.overall_impact == before_sector["overall"]
    assert industry_row.impact == before_industry["impact"]
    assert industry_row.warnings == before_industry["warnings"]
    assert industry_row.overall_impact == before_industry["overall"]


def test_basic_industry_technical_and_fundamental_scores_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)
    group = disc_session.query(GroupScore).filter_by(run_id=run_id, entity_type="BASIC_INDUSTRY").first()
    before = {
        "technical_score": group.technical_score,
        "fundamental_score": group.fundamental_score,
        "macro_score": group.macro_score,
        "warnings": list(group.warnings),
        "calculation_details": copy.deepcopy(group.calculation_details),
    }

    svc.generate_basic_industry_impacts(run_id)
    disc_session.expire(group)

    assert group.technical_score == before["technical_score"]
    assert group.fundamental_score == before["fundamental_score"]
    assert group.macro_score == before["macro_score"]
    assert group.warnings == before["warnings"]
    assert group.calculation_details == before["calculation_details"]


def test_parallel_ai_is_not_called(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)

    svc.generate_basic_industry_impacts(run_id)

    assert not hasattr(svc, "_parallel")
    assert not hasattr(svc, "_provider")


def test_source_financial_database_is_not_accessed(disc_session):
    accessed = []

    @event.listens_for(source_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        accessed.append(statement)

    try:
        run_id = f"run_{uuid.uuid4().hex[:6]}"
        svc = _setup(disc_session, run_id)
        svc.generate_basic_industry_impacts(run_id)
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []
