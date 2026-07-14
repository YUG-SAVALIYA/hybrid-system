"""Offline tests for MacroIndustryImpactService."""
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
from services.macro.macro_filter_summary import CATEGORIES
from services.macro.macro_industry_impact import (
    MAX_INDUSTRIES_PER_BATCH,
    MacroIndustryImpactService,
    VALID_CONFIDENCES,
    VALID_IMPACTS,
    VALID_RELATIONSHIPS,
    W_DUPLICATE_INDUSTRY,
    W_EXTRA_INDUSTRY,
    W_INVALID_CATEGORY,
    W_INVALID_OVERALL,
    W_INVALID_RELATIONSHIP,
    W_LLM_INVALID,
    W_MISSING_EVIDENCE,
    W_MISSING_INDUSTRY,
    W_UNKNOWN_EVIDENCE,
    _fallback_industry,
    _validate_batch_response,
)
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


def _make_selection(session, run_id, sector, horizon="SHORT", selected=True):
    row = DiscoverySelection(
        id=str(uuid.uuid4()),
        run_id=run_id,
        horizon=horizon,
        entity_type="SECTOR",
        entity_name=sector,
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


def _make_industry_group(session, run_id, sector, industry, horizon="SHORT", technical=77.0, fundamental=66.0):
    row = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="INDUSTRY",
        entity_name=industry,
        parent_sector=sector,
        parent_industry="",
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


def _make_sector_impact(session, run_id, sector, impact="NEGATIVE"):
    row = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=run_id,
        source_summary_id="summary",
        entity_type="SECTOR",
        entity_name=sector,
        parent_sector="",
        parent_industry="",
        category_impacts={
            category: {
                "impact": impact,
                "confidence": "MEDIUM",
                "reason": f"{sector} sector reason.",
                "evidence_refs": [f"{category}:DOC-001"],
            }
            for category in CATEGORIES
        },
        overall_impact={
            "impact": impact,
            "confidence": "MEDIUM",
            "reason": f"{sector} parent sector impact.",
            "dominant_categories": [CATEGORIES[0]],
            "evidence_refs": [f"{CATEGORIES[0]}:DOC-001"],
        },
        impact=impact,
        confidence="MEDIUM",
        reason=f"{sector} parent sector impact.",
        evidence_refs=[f"{CATEGORIES[0]}:DOC-001"],
        warnings=["KEEP_SECTOR_IMPACT_WARNING"],
        status="COMPLETED",
        model_name="fake",
        prompt_version="test",
    )
    session.add(row)
    session.commit()
    return row


def _industry_item(industry, impact="POSITIVE", confidence="MEDIUM", relationship="MORE_POSITIVE", include_all=True):
    category_impacts = {
        category: {
            "impact": impact,
            "confidence": confidence,
            "reason": f"{industry} reason for {category}.",
            "evidence_refs": [f"{category}:DOC-001"],
        }
        for category in CATEGORIES
    }
    if not include_all:
        category_impacts.pop(CATEGORIES[-1])
    return {
        "industry": industry,
        "category_impacts": category_impacts,
        "overall_impact": {
            "impact": impact,
            "confidence": confidence,
            "reason": f"{industry} net industry macro effect.",
            "dominant_categories": [CATEGORIES[0]],
            "evidence_refs": [f"{CATEGORIES[0]}:DOC-001"],
            "relationship_to_parent_sector": relationship,
        },
    }


def _batch_response(parent_sector, industries, impact="POSITIVE"):
    return json.dumps({
        "parent_sector": parent_sector,
        "industries": [_industry_item(industry, impact=impact) for industry in industries],
    })


def _allowed_refs():
    category_summaries, _ = _summary_payload()
    return _build_allowed_evidence_refs(category_summaries)


def _setup(session, run_id, sector="Technology", industries=None, responses=None):
    industries = industries or ["Software"]
    _make_summary(session, run_id)
    _make_selection(session, run_id, sector, "SHORT")
    _make_sector_impact(session, run_id, sector)
    for industry in industries:
        _make_industry_group(session, run_id, sector, industry)
    return MacroIndustryImpactService(session, llm_caller=_llm(responses or [_batch_response(sector, industries)]))


def _industry_impacts(session, run_id):
    return (
        session.query(MacroEntityImpact)
        .filter_by(run_id=run_id, entity_type="INDUSTRY")
        .order_by(MacroEntityImpact.parent_sector.asc(), MacroEntityImpact.entity_name.asc())
        .all()
    )


def test_active_selected_sectors_are_loaded(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id, "Technology")

    svc = MacroIndustryImpactService(disc_session, llm_caller=_llm([]))

    assert svc._selected_sectors_by_horizon(run_id) == {"SHORT": ["Technology"]}


def test_short_mid_long_selections_are_read_independently(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_selection(disc_session, run_id, "Tech", "SHORT")
    _make_selection(disc_session, run_id, "Finance", "MID")
    _make_selection(disc_session, run_id, "Energy", "LONG")

    selected = MacroIndustryImpactService(disc_session, llm_caller=_llm([]))._selected_sectors_by_horizon(run_id)

    assert selected == {"LONG": ["Energy"], "MID": ["Finance"], "SHORT": ["Tech"]}


def test_duplicate_selected_sector_across_horizons_processed_once(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    _make_selection(disc_session, run_id, "Technology", "SHORT")
    _make_selection(disc_session, run_id, "Technology", "LONG")
    _make_sector_impact(disc_session, run_id, "Technology")
    _make_industry_group(disc_session, run_id, "Technology", "Software")
    llm = _llm([_batch_response("Technology", ["Software"])])
    svc = MacroIndustryImpactService(disc_session, llm_caller=llm)

    result = svc.generate_industry_impacts(run_id)

    assert result["metadata"]["selected_sector_count"] == 2
    assert result["metadata"]["unique_selected_sector_count"] == 1
    assert llm.call.call_count == 1


def test_unselected_sectors_are_ignored(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    _make_selection(disc_session, run_id, "Selected", selected=True)
    _make_selection(disc_session, run_id, "Ignored", selected=False)
    _make_sector_impact(disc_session, run_id, "Selected")
    _make_sector_impact(disc_session, run_id, "Ignored")
    _make_industry_group(disc_session, run_id, "Selected", "A")
    _make_industry_group(disc_session, run_id, "Ignored", "B")

    MacroIndustryImpactService(disc_session, llm_caller=_llm([_batch_response("Selected", ["A"])])).generate_industry_impacts(run_id)

    assert [(row.parent_sector, row.entity_name) for row in _industry_impacts(disc_session, run_id)] == [("Selected", "A")]


def test_industries_load_only_from_selected_parent_sector(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_industry_group(disc_session, run_id, "Selected", "A")
    _make_industry_group(disc_session, run_id, "Other", "B")

    svc = MacroIndustryImpactService(disc_session, llm_caller=_llm([]))

    assert svc._load_industry_universe(run_id, "Selected") == ["A"]


def test_same_industry_name_under_different_sectors_remains_isolated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    for sector in ["Auto", "Tech"]:
        _make_selection(disc_session, run_id, sector)
        _make_sector_impact(disc_session, run_id, sector)
        _make_industry_group(disc_session, run_id, sector, "Software")
    svc = MacroIndustryImpactService(
        disc_session,
        llm_caller=_llm([_batch_response("Auto", ["Software"]), _batch_response("Tech", ["Software"])]),
    )

    svc.generate_industry_impacts(run_id)

    assert [(row.parent_sector, row.entity_name) for row in _industry_impacts(disc_session, run_id)] == [
        ("Auto", "Software"),
        ("Tech", "Software"),
    ]


def test_empty_and_duplicate_industries_are_removed(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_industry_group(disc_session, run_id, "Tech", "Software", "SHORT")
    _make_industry_group(disc_session, run_id, "Tech", "Software", "MID")
    _make_industry_group(disc_session, run_id, "Tech", "", "LONG")

    assert MacroIndustryImpactService(disc_session, llm_caller=_llm([]))._load_industry_universe(run_id, "Tech") == ["Software"]


def test_industries_are_sorted_alphabetically(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    for industry in ["Zeta", "Alpha", "Beta"]:
        _make_industry_group(disc_session, run_id, "Tech", industry)

    assert MacroIndustryImpactService(disc_session, llm_caller=_llm([]))._load_industry_universe(run_id, "Tech") == ["Alpha", "Beta", "Zeta"]


def test_maximum_eight_industries_per_batch(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    industries = [f"Industry {index:02d}" for index in range(9)]
    svc = _setup(
        disc_session,
        run_id,
        industries=industries,
        responses=[_batch_response("Technology", industries[:8]), _batch_response("Technology", industries[8:])],
    )

    svc.generate_industry_impacts(run_id)

    assert MAX_INDUSTRIES_PER_BATCH == 8
    assert [len(batch) for batch in svc.last_batches["Technology"]] == [8, 1]


def test_multiple_selected_sectors_create_separate_batch_groups(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    for sector, industry in [("A", "A1"), ("B", "B1")]:
        _make_selection(disc_session, run_id, sector)
        _make_sector_impact(disc_session, run_id, sector)
        _make_industry_group(disc_session, run_id, sector, industry)
    svc = MacroIndustryImpactService(disc_session, llm_caller=_llm([_batch_response("A", ["A1"]), _batch_response("B", ["B1"])]))

    result = svc.generate_industry_impacts(run_id)

    assert result["metadata"]["parent_sector_count_processed"] == 2
    assert result["metadata"]["llm_call_count"] == 2


def test_parent_sector_impact_included_as_context(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id, responses=[_batch_response("Technology", ["Software"])])

    svc.generate_industry_impacts(run_id)

    prompt = svc._llm.call.call_args_list[0][0][0]
    assert "parent_sector_impact" in prompt
    assert "Technology parent sector impact." in prompt


def test_parent_sector_impact_not_blindly_copied(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id, responses=[_batch_response("Technology", ["Software"], impact="POSITIVE")])

    svc.generate_industry_impacts(run_id)
    row = _industry_impacts(disc_session, run_id)[0]

    assert row.impact == "POSITIVE"
    assert row.impact != "NEGATIVE"


def test_all_four_category_impacts_required():
    raw = {"parent_sector": "Tech", "industries": [_industry_item("Software", include_all=False)]}

    outputs, warnings_by_industry, _ = _validate_batch_response(raw, "Tech", ["Software"], _allowed_refs())

    assert outputs == {}
    assert W_INVALID_CATEGORY in warnings_by_industry["Software"]


def test_valid_impact_and_confidence_enums():
    assert VALID_IMPACTS == {"POSITIVE", "NEGATIVE", "NEUTRAL", "N_A", "UNCERTAIN"}
    assert VALID_CONFIDENCES == {"HIGH", "MEDIUM", "LOW"}


def test_relationship_to_parent_sector_validation():
    item = _industry_item("Software")
    item["overall_impact"]["relationship_to_parent_sector"] = "COPIED"

    outputs, warnings_by_industry, _ = _validate_batch_response({"parent_sector": "Tech", "industries": [item]}, "Tech", ["Software"], _allowed_refs())

    assert outputs["Software"]["overall_impact"]["relationship_to_parent_sector"] == "UNCERTAIN"
    assert W_INVALID_RELATIONSHIP in warnings_by_industry["Software"]
    assert "SIMILAR" in VALID_RELATIONSHIPS


def test_missing_industry_detection():
    outputs, warnings_by_industry, batch_warnings = _validate_batch_response(
        {"parent_sector": "Tech", "industries": [_industry_item("A")]},
        "Tech",
        ["A", "B"],
        _allowed_refs(),
    )

    assert "A" in outputs
    assert W_MISSING_INDUSTRY in batch_warnings
    assert W_MISSING_INDUSTRY in warnings_by_industry["B"]


def test_extra_industry_removal():
    outputs, _, batch_warnings = _validate_batch_response(
        {"parent_sector": "Tech", "industries": [_industry_item("A"), _industry_item("Extra")]},
        "Tech",
        ["A"],
        _allowed_refs(),
    )

    assert list(outputs) == ["A"]
    assert W_EXTRA_INDUSTRY in batch_warnings


def test_duplicate_industry_removal():
    outputs, _, batch_warnings = _validate_batch_response(
        {"parent_sector": "Tech", "industries": [_industry_item("A"), _industry_item("A")]},
        "Tech",
        ["A"],
        _allowed_refs(),
    )

    assert list(outputs) == ["A"]
    assert W_DUPLICATE_INDUSTRY in batch_warnings


def test_namespaced_evidence_validation():
    outputs, _, _ = _validate_batch_response(
        {"parent_sector": "Tech", "industries": [_industry_item("A")]},
        "Tech",
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
    item = _industry_item("A")
    item["category_impacts"][CATEGORIES[0]]["evidence_refs"] = [f"{CATEGORIES[0]}:DOC-999"]

    outputs, warnings_by_industry, _ = _validate_batch_response({"parent_sector": "Tech", "industries": [item]}, "Tech", ["A"], _allowed_refs())

    assert outputs["A"]["category_impacts"][CATEGORIES[0]]["evidence_refs"] == []
    assert W_UNKNOWN_EVIDENCE in warnings_by_industry["A"]


def test_missing_evidence_warning():
    item = _industry_item("A")
    item["category_impacts"][CATEGORIES[0]]["evidence_refs"] = []

    _, warnings_by_industry, _ = _validate_batch_response({"parent_sector": "Tech", "industries": [item]}, "Tech", ["A"], _allowed_refs())

    assert W_MISSING_EVIDENCE in warnings_by_industry["A"]


def test_forbidden_score_rank_stock_recommendation_selection_fields_removed():
    item = _industry_item("A")
    item["score"] = 99
    item["stock_recommendation"] = "BUY ABC"
    item["selected"] = True

    outputs, _, batch_warnings = _validate_batch_response({"parent_sector": "Tech", "industries": [item]}, "Tech", ["A"], _allowed_refs())

    serialized = json.dumps(outputs)
    assert "stock_recommendation" not in serialized
    assert "\"score\"" not in serialized
    assert "\"selected\"" not in serialized
    assert W_INVALID_OVERALL in batch_warnings


def test_one_repair_attempt(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id, responses=["not json", _batch_response("Technology", ["Software"])])

    svc.generate_industry_impacts(run_id)

    assert svc._llm.call.call_count == 2
    assert _industry_impacts(disc_session, run_id)[0].impact == "POSITIVE"


def test_invalid_repaired_output_uses_deterministic_fallback(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id, responses=["not json", "still not json"])

    svc.generate_industry_impacts(run_id)
    row = _industry_impacts(disc_session, run_id)[0]

    assert row.status == "FALLBACK"
    assert row.overall_impact == _fallback_industry("Software")["overall_impact"]
    assert W_LLM_INVALID in row.warnings


def test_one_failed_batch_preserves_successful_batches(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    industries = [f"Industry {index:02d}" for index in range(9)]
    svc = _setup(
        disc_session,
        run_id,
        industries=industries,
        responses=[_batch_response("Technology", industries[:8]), "bad", "bad again"],
    )

    svc.generate_industry_impacts(run_id)
    rows = _industry_impacts(disc_session, run_id)

    assert len(rows) == 9
    assert sum(1 for row in rows if row.status == "FALLBACK") == 1
    assert sum(1 for row in rows if row.status != "FALLBACK") == 8


def test_idempotent_persistence(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)
    first = svc.generate_industry_impacts(run_id)["impact_ids"]
    svc._llm = _llm([_batch_response("Technology", ["Software"], impact="NEGATIVE")])
    second = svc.generate_industry_impacts(run_id)["impact_ids"]

    assert first == second
    assert len(_industry_impacts(disc_session, run_id)) == 1
    assert _industry_impacts(disc_session, run_id)[0].impact == "NEGATIVE"


def test_stale_industry_impacts_cleaned_only_for_same_run(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    other_run = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)
    stale = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="INDUSTRY",
        entity_name="Stale",
        parent_sector="OldSector",
        parent_industry="",
        status="COMPLETED",
    )
    other = MacroEntityImpact(
        id=str(uuid.uuid4()),
        run_id=other_run,
        entity_type="INDUSTRY",
        entity_name="OtherRunStale",
        parent_sector="OldSector",
        parent_industry="",
        status="COMPLETED",
    )
    disc_session.add_all([stale, other])
    disc_session.commit()

    result = svc.generate_industry_impacts(run_id)

    assert result["metadata"]["stale_impact_count"] == 1
    assert disc_session.query(MacroEntityImpact).filter_by(id=stale.id).first() is None
    assert disc_session.query(MacroEntityImpact).filter_by(id=other.id).first() is not None


def test_sector_impact_rows_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)
    sector_row = disc_session.query(MacroEntityImpact).filter_by(run_id=run_id, entity_type="SECTOR").first()
    before = copy_row = {
        "impact": sector_row.impact,
        "warnings": list(sector_row.warnings),
        "overall_impact": dict(sector_row.overall_impact),
    }

    svc.generate_industry_impacts(run_id)
    disc_session.expire(sector_row)

    assert sector_row.impact == copy_row["impact"]
    assert sector_row.warnings == before["warnings"]
    assert sector_row.overall_impact == before["overall_impact"]


def test_industry_technical_and_fundamental_scores_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)
    group = disc_session.query(GroupScore).filter_by(run_id=run_id, entity_type="INDUSTRY").first()
    before = {
        "technical_score": group.technical_score,
        "fundamental_score": group.fundamental_score,
        "warnings": list(group.warnings),
        "calculation_details": dict(group.calculation_details),
    }

    svc.generate_industry_impacts(run_id)
    disc_session.expire(group)

    assert group.technical_score == before["technical_score"]
    assert group.fundamental_score == before["fundamental_score"]
    assert group.warnings == before["warnings"]
    assert group.calculation_details == before["calculation_details"]


def test_parallel_ai_is_not_called(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    svc = _setup(disc_session, run_id)

    svc.generate_industry_impacts(run_id)

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
        svc.generate_industry_impacts(run_id)
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []
