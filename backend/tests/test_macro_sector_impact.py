"""Offline tests for MacroSectorImpactService."""
import datetime
import json
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event, text

from database import DiscoverySessionLocal, source_engine
from models.discovery import GroupScore, MacroEntityImpact, MacroSummary
from services.macro.macro_filter_summary import CATEGORIES
from services.macro.macro_sector_impact import (
    MAX_SECTORS_PER_BATCH,
    MacroSectorImpactService,
    VALID_CONFIDENCES,
    VALID_IMPACTS,
    W_DUPLICATE_SECTOR,
    W_EXTRA_SECTOR,
    W_INVALID_CATEGORY,
    W_INVALID_OVERALL,
    W_LLM_INVALID,
    W_MISSING_EVIDENCE,
    W_MISSING_SECTOR,
    W_UNKNOWN_EVIDENCE,
    _build_allowed_evidence_refs,
    _fallback_sector,
    _validate_batch_response,
)


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM macro_entity_impacts"))
    session.execute(text("DELETE FROM macro_summaries"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM macro_entity_impacts"))
    session.execute(text("DELETE FROM macro_summaries"))
    session.execute(text("DELETE FROM group_scores"))
    session.commit()
    session.close()


def _llm(responses):
    mock = MagicMock()
    mock.call.side_effect = responses
    return mock


def _summary_payload(tag="latest"):
    category_summaries = {}
    for category in CATEGORIES:
        category_summaries[category] = {
            "category": category,
            "condition": "STABLE",
            "summary": f"{tag} summary for {category}",
            "summary_source_ids": ["DOC-001"],
            "key_developments": [
                {
                    "development": f"{tag} development",
                    "direction": "POSITIVE",
                    "source_ids": ["DOC-001"],
                }
            ],
            "contradictions": [],
            "missing_information": [],
            "used_source_ids": ["DOC-001"],
            "ignored_source_ids": ["DOC-999"],
        }
    overall = {
        "overall_summary": f"{tag} overall macro summary",
        "dominant_condition": "MIXED",
        "dominant_themes": [],
        "cross_category_conflicts": [],
        "category_conditions": {category: "STABLE" for category in CATEGORIES},
        "missing_categories": [],
    }
    return category_summaries, overall


def _make_summary(session, run_id, tag="latest", status="COMPLETED", created_at=None):
    category_summaries, overall = _summary_payload(tag)
    rec = MacroSummary(
        id=f"summary-{tag}-{uuid.uuid4().hex[:6]}",
        run_id=run_id,
        source_batch_id=f"batch-{tag}",
        summary_type="MACRO_FILTER",
        status=status,
        model_name="fake-model",
        prompt_version="test",
        category_summaries=category_summaries,
        overall_synthesis=overall,
        document_statistics={},
        warnings=[],
        created_at=created_at or datetime.datetime.utcnow(),
        updated_at=created_at or datetime.datetime.utcnow(),
    )
    session.add(rec)
    session.commit()
    return rec


def _make_group(session, run_id, sector, horizon="SHORT"):
    rec = GroupScore(
        id=str(uuid.uuid4()),
        run_id=run_id,
        entity_type="SECTOR",
        entity_name=sector,
        parent_sector="",
        parent_industry="",
        horizon=horizon,
        technical_score=77.0,
        fundamental_score=66.0,
        macro_score=None,
        final_score=None,
        rank=None,
        warnings=[],
        calculation_details={"before": True},
    )
    session.add(rec)
    session.commit()
    return rec


def _sector_item(sector, impact="POSITIVE", confidence="MEDIUM", include_all_categories=True):
    category_impacts = {}
    for category in CATEGORIES:
        category_impacts[category] = {
            "impact": impact,
            "confidence": confidence,
            "reason": f"{sector} reason for {category}.",
            "evidence_refs": [f"{category}:DOC-001"],
        }
    if not include_all_categories:
        category_impacts.pop(CATEGORIES[-1])
    return {
        "sector": sector,
        "category_impacts": category_impacts,
        "overall_impact": {
            "impact": impact,
            "confidence": confidence,
            "reason": f"{sector} net macro effect.",
            "dominant_categories": [CATEGORIES[0]],
            "evidence_refs": [f"{CATEGORIES[0]}:DOC-001"],
        },
    }


def _batch_response(sectors, impact="POSITIVE", confidence="MEDIUM"):
    return json.dumps(
        {
            "sectors": [
                _sector_item(sector, impact=impact, confidence=confidence)
                for sector in sectors
            ]
        }
    )


def _service_with_summary(session, run_id, sectors, responses):
    _make_summary(session, run_id)
    for sector in sectors:
        _make_group(session, run_id, sector)
    return MacroSectorImpactService(session, llm_caller=_llm(responses))


def _records(session, run_id):
    return (
        session.query(MacroEntityImpact)
        .filter_by(run_id=run_id, entity_type="SECTOR")
        .order_by(MacroEntityImpact.entity_name.asc())
        .all()
    )


def _allowed_refs():
    category_summaries, _ = _summary_payload()
    return _build_allowed_evidence_refs(category_summaries)


def test_latest_successful_macro_filter_summary_selected(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    old = datetime.datetime.utcnow() - datetime.timedelta(days=1)
    _make_summary(disc_session, run_id, tag="old", created_at=old)
    latest = _make_summary(disc_session, run_id, tag="latest")
    _make_summary(disc_session, run_id, tag="failed", status="FAILED")
    _make_group(disc_session, run_id, "Automobiles")

    svc = MacroSectorImpactService(disc_session, llm_caller=_llm([_batch_response(["Automobiles"])]))
    svc.generate_sector_impacts(run_id)

    assert _records(disc_session, run_id)[0].source_summary_id == latest.id


def test_sector_universe_loaded_and_deduplicated(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    _make_group(disc_session, run_id, "Banking", horizon="SHORT")
    _make_group(disc_session, run_id, "Banking", horizon="MID")
    _make_group(disc_session, run_id, "Energy")

    svc = MacroSectorImpactService(disc_session, llm_caller=_llm([_batch_response(["Banking", "Energy"])]))
    assert svc._load_sector_universe(run_id) == ["Banking", "Energy"]


def test_empty_sector_names_excluded(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    _make_group(disc_session, run_id, "")
    _make_group(disc_session, run_id, "Healthcare")

    svc = MacroSectorImpactService(disc_session, llm_caller=_llm([_batch_response(["Healthcare"])]))
    assert svc._load_sector_universe(run_id) == ["Healthcare"]


def test_sectors_sorted_deterministically(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    _make_summary(disc_session, run_id)
    for sector in ["Zinc", "Automobiles", "Banking"]:
        _make_group(disc_session, run_id, sector)

    svc = MacroSectorImpactService(disc_session, llm_caller=_llm([]))
    assert svc._load_sector_universe(run_id) == ["Automobiles", "Banking", "Zinc"]


def test_maximum_eight_sectors_per_batch(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sectors = [f"Sector {index:02d}" for index in range(9)]
    responses = [_batch_response(sectors[:8]), _batch_response(sectors[8:])]
    svc = _service_with_summary(disc_session, run_id, sectors, responses)

    svc.generate_sector_impacts(run_id)

    assert MAX_SECTORS_PER_BATCH == 8
    assert [len(batch) for batch in svc.last_batches] == [8, 1]


def test_approximately_22_sectors_produce_three_llm_calls(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sectors = [f"Sector {index:02d}" for index in range(22)]
    batches = [sectors[:8], sectors[8:16], sectors[16:]]
    llm = _llm([_batch_response(batch) for batch in batches])
    svc = _service_with_summary(disc_session, run_id, sectors, [])
    svc._llm = llm

    svc.generate_sector_impacts(run_id)

    assert llm.call.call_count == 3


def test_all_four_category_impacts_required():
    raw = {"sectors": [_sector_item("Auto", include_all_categories=False)]}

    outputs, sector_warnings, _ = _validate_batch_response(raw, ["Auto"], _allowed_refs())

    assert outputs == {}
    assert W_INVALID_CATEGORY in sector_warnings["Auto"]


def test_overall_impact_required():
    item = _sector_item("Auto")
    item.pop("overall_impact")

    outputs, sector_warnings, _ = _validate_batch_response({"sectors": [item]}, ["Auto"], _allowed_refs())

    assert outputs == {}
    assert W_INVALID_OVERALL in sector_warnings["Auto"]


def test_valid_impact_and_confidence_enums():
    assert VALID_IMPACTS == {"POSITIVE", "NEGATIVE", "NEUTRAL", "N_A", "UNCERTAIN"}
    assert VALID_CONFIDENCES == {"HIGH", "MEDIUM", "LOW"}


def test_missing_requested_sector_detection():
    outputs, sector_warnings, batch_warnings = _validate_batch_response(
        {"sectors": [_sector_item("Auto")]}, ["Auto", "Banking"], _allowed_refs()
    )

    assert "Auto" in outputs
    assert W_MISSING_SECTOR in batch_warnings
    assert W_MISSING_SECTOR in sector_warnings["Banking"]


def test_extra_sector_removal():
    outputs, _, batch_warnings = _validate_batch_response(
        {"sectors": [_sector_item("Auto"), _sector_item("Unexpected")]},
        ["Auto"],
        _allowed_refs(),
    )

    assert list(outputs) == ["Auto"]
    assert W_EXTRA_SECTOR in batch_warnings


def test_duplicate_sector_removal():
    outputs, _, batch_warnings = _validate_batch_response(
        {"sectors": [_sector_item("Auto"), _sector_item("Auto")]},
        ["Auto"],
        _allowed_refs(),
    )

    assert list(outputs) == ["Auto"]
    assert W_DUPLICATE_SECTOR in batch_warnings


def test_namespaced_evidence_reference_validation():
    outputs, _, _ = _validate_batch_response(
        {"sectors": [_sector_item("Auto")]}, ["Auto"], _allowed_refs()
    )

    assert outputs["Auto"]["category_impacts"][CATEGORIES[0]]["evidence_refs"] == [
        f"{CATEGORIES[0]}:DOC-001"
    ]


def test_same_doc_id_in_different_categories_remains_distinct():
    refs = _allowed_refs()

    assert f"{CATEGORIES[0]}:DOC-001" in refs[CATEGORIES[0]]
    assert f"{CATEGORIES[1]}:DOC-001" in refs[CATEGORIES[1]]
    assert f"{CATEGORIES[0]}:DOC-001" != f"{CATEGORIES[1]}:DOC-001"


def test_unknown_evidence_reference_removed():
    item = _sector_item("Auto")
    item["category_impacts"][CATEGORIES[0]]["evidence_refs"] = [f"{CATEGORIES[0]}:DOC-999"]

    outputs, sector_warnings, _ = _validate_batch_response({"sectors": [item]}, ["Auto"], _allowed_refs())

    assert outputs["Auto"]["category_impacts"][CATEGORIES[0]]["evidence_refs"] == []
    assert W_UNKNOWN_EVIDENCE in sector_warnings["Auto"]


def test_missing_evidence_warning():
    item = _sector_item("Auto")
    item["category_impacts"][CATEGORIES[0]]["evidence_refs"] = []

    _, sector_warnings, _ = _validate_batch_response({"sectors": [item]}, ["Auto"], _allowed_refs())

    assert W_MISSING_EVIDENCE in sector_warnings["Auto"]


def test_dominant_category_validation():
    item = _sector_item("Auto")
    item["overall_impact"]["dominant_categories"] = ["NOT_A_CATEGORY"]

    outputs, sector_warnings, _ = _validate_batch_response({"sectors": [item]}, ["Auto"], _allowed_refs())

    assert outputs["Auto"]["overall_impact"]["dominant_categories"] == []
    assert W_INVALID_OVERALL in sector_warnings["Auto"]


def test_forbidden_score_rank_stock_recommendation_fields_removed():
    item = _sector_item("Auto")
    item["overall_impact"]["score"] = 99
    item["stock_recommendation"] = "BUY ABC"
    item["rank"] = 1

    outputs, _, batch_warnings = _validate_batch_response({"sectors": [item]}, ["Auto"], _allowed_refs())

    serialized = json.dumps(outputs)
    assert "stock_recommendation" not in serialized
    assert "\"score\"" not in serialized
    assert W_INVALID_OVERALL in batch_warnings


def test_one_repair_attempt(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sector = "Auto"
    llm = _llm(["not json", _batch_response([sector])])
    svc = _service_with_summary(disc_session, run_id, [sector], [])
    svc._llm = llm

    svc.generate_sector_impacts(run_id)

    assert llm.call.call_count == 2
    assert _records(disc_session, run_id)[0].impact == "POSITIVE"


def test_invalid_repaired_batch_uses_deterministic_fallback(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sector = "Auto"
    llm = _llm(["not json", "still not json"])
    svc = _service_with_summary(disc_session, run_id, [sector], [])
    svc._llm = llm

    svc.generate_sector_impacts(run_id)

    record = _records(disc_session, run_id)[0]
    assert record.status == "FALLBACK"
    assert record.overall_impact == _fallback_sector(sector)["overall_impact"]
    assert W_LLM_INVALID in record.warnings


def test_one_failed_batch_does_not_erase_successful_batches(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sectors = [f"Sector {index:02d}" for index in range(9)]
    llm = _llm([_batch_response(sectors[:8]), "bad", "bad again"])
    svc = _service_with_summary(disc_session, run_id, sectors, [])
    svc._llm = llm

    svc.generate_sector_impacts(run_id)

    records = _records(disc_session, run_id)
    assert len(records) == 9
    assert sum(1 for record in records if record.status == "FALLBACK") == 1
    assert sum(1 for record in records if record.status != "FALLBACK") == 8


def test_deterministic_metadata_counts(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sectors = ["A", "B", "C"]
    responses = [
        json.dumps(
            {
                "sectors": [
                    _sector_item("A", impact="POSITIVE", confidence="HIGH"),
                    _sector_item("B", impact="NEGATIVE", confidence="MEDIUM"),
                    _sector_item("C", impact="UNCERTAIN", confidence="LOW"),
                ]
            }
        )
    ]
    svc = _service_with_summary(disc_session, run_id, sectors, responses)

    result = svc.generate_sector_impacts(run_id)

    assert result["metadata"]["sector_count"] == 3
    assert result["metadata"]["classified_sector_count"] == 3
    assert result["metadata"]["positive_sector_count"] == 1
    assert result["metadata"]["negative_sector_count"] == 1
    assert result["metadata"]["uncertain_sector_count"] == 1
    assert result["metadata"]["high_confidence_count"] == 1
    assert result["metadata"]["medium_confidence_count"] == 1
    assert result["metadata"]["low_confidence_count"] == 1
    assert result["metadata"]["evidence_reference_count"] == 12


def test_idempotent_persistence(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sector = "Auto"
    svc = _service_with_summary(disc_session, run_id, [sector], [_batch_response([sector])])
    first = svc.generate_sector_impacts(run_id)["impact_ids"][0]
    svc._llm = _llm([_batch_response([sector], impact="NEGATIVE")])
    second = svc.generate_sector_impacts(run_id)["impact_ids"][0]

    assert first == second
    assert disc_session.query(MacroEntityImpact).filter_by(run_id=run_id).count() == 1
    assert _records(disc_session, run_id)[0].impact == "NEGATIVE"


def test_macro_search_provider_is_not_called(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sector = "Auto"
    svc = _service_with_summary(disc_session, run_id, [sector], [_batch_response([sector])])

    svc.generate_sector_impacts(run_id)

    assert not hasattr(svc, "_parallel")
    assert not hasattr(svc, "_provider")


def test_source_financial_database_is_not_accessed(disc_session):
    accessed = []

    @event.listens_for(source_engine, "before_cursor_execute")
    def intercept(conn, cursor, statement, parameters, context, executemany):
        accessed.append(statement)

    try:
        run_id = f"run_{uuid.uuid4().hex[:6]}"
        sector = "Auto"
        svc = _service_with_summary(disc_session, run_id, [sector], [_batch_response([sector])])
        svc.generate_sector_impacts(run_id)
    finally:
        event.remove(source_engine, "before_cursor_execute", intercept)

    assert accessed == []


def test_technical_and_fundamental_group_scores_remain_unchanged(disc_session):
    run_id = f"run_{uuid.uuid4().hex[:6]}"
    sector = "Auto"
    svc = _service_with_summary(disc_session, run_id, [sector], [_batch_response([sector])])
    group = disc_session.query(GroupScore).filter_by(run_id=run_id, entity_name=sector).first()
    before = {
        "technical_score": group.technical_score,
        "fundamental_score": group.fundamental_score,
        "macro_score": group.macro_score,
        "final_score": group.final_score,
        "rank": group.rank,
        "warnings": list(group.warnings),
        "calculation_details": dict(group.calculation_details),
    }

    svc.generate_sector_impacts(run_id)
    disc_session.expire(group)

    assert group.technical_score == before["technical_score"]
    assert group.fundamental_score == before["fundamental_score"]
    assert group.macro_score == before["macro_score"]
    assert group.final_score == before["final_score"]
    assert group.rank == before["rank"]
    assert group.warnings == before["warnings"]
    assert group.calculation_details == before["calculation_details"]
