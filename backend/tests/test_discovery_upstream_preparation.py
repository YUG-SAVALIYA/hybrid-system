import inspect
import uuid

import pytest
from sqlalchemy import text

from database import DiscoverySessionLocal
from models.discovery import (
    CompanyFundamentalMetric,
    CompanyTechnicalMetric,
    DiscoveryRun,
    EligibleUniverseSnapshot,
    GroupScore,
)
from services.discovery import discovery_upstream_preparation as prep
from services.discovery.discovery_upstream_preparation import DiscoveryUpstreamPreparationService


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    _clean(session)
    yield session
    session.rollback()
    _clean(session)
    session.close()


def _clean(session):
    for table in [
        "eligible_universe_snapshots",
        "group_scores",
        "company_technical_metrics",
        "company_fundamental_metrics",
        "discovery_runs",
    ]:
        session.execute(text(f"DELETE FROM {table}"))
    session.commit()


def _run_id():
    return f"prep_{uuid.uuid4().hex[:8]}"


def _make_run(session, run_id=None, status="PENDING", source_data_as_of="2026-07-13", prep_results=None):
    run = DiscoveryRun(
        id=run_id or _run_id(),
        run_date="2026-07-13",
        source_data_as_of=source_data_as_of,
        status=status,
        preparation_status="PENDING",
        preparation_stage_results=prep_results or {},
        preparation_warnings=[],
        preparation_resume_count=0,
    )
    session.add(run)
    session.commit()
    return run


def _upsert_technical_metric(session, run_id, horizon):
    row = (
        session.query(CompanyTechnicalMetric)
        .filter_by(run_id=run_id, source_company_id="c1", horizon=horizon)
        .first()
    )
    if row is None:
        row = CompanyTechnicalMetric(
            id=str(uuid.uuid4()),
            run_id=run_id,
            source_company_id="c1",
            symbol="AAA",
            sector="Technology",
            industry="Software",
            basic_industry="Enterprise Software",
            horizon=horizon,
        )
        session.add(row)
    row.return_available = True
    row.volume_available = True
    row.consistency_available = True
    session.commit()


def _upsert_fundamental_metric(session, run_id):
    row = (
        session.query(CompanyFundamentalMetric)
        .filter_by(run_id=run_id, source_company_id="c1")
        .first()
    )
    if row is None:
        row = CompanyFundamentalMetric(
            id=str(uuid.uuid4()),
            run_id=run_id,
            source_company_id="c1",
            symbol="AAA",
            sector="Technology",
            industry="Software",
            basic_industry="Enterprise Software",
        )
        session.add(row)
    row.final_fundamental_score = 80.0
    row.fundamental_status = "READY"
    session.commit()


def _upsert_group(session, run_id, horizon, entity_type, technical=False, fundamental=False):
    names = {
        "SECTOR": ("Technology", "", ""),
        "INDUSTRY": ("Software", "Technology", ""),
        "BASIC_INDUSTRY": ("Enterprise Software", "Technology", "Software"),
    }
    name, parent_sector, parent_industry = names[entity_type]
    row = (
        session.query(GroupScore)
        .filter_by(
            run_id=run_id,
            horizon=horizon,
            entity_type=entity_type,
            entity_name=name,
            parent_sector=parent_sector,
            parent_industry=parent_industry,
        )
        .first()
    )
    if row is None:
        row = GroupScore(
            id=str(uuid.uuid4()),
            run_id=run_id,
            horizon=horizon,
            entity_type=entity_type,
            entity_name=name,
            parent_sector=parent_sector,
            parent_industry=parent_industry,
            calculation_details={},
        )
        session.add(row)
    if technical:
        row.technical_score = 70.0
    if fundamental:
        row.fundamental_score = 80.0
    session.commit()


class Alignment:
    def __init__(self, horizon, status="READY"):
        self.horizon = horizon
        self.status = status


class UniverseFake:
    def __init__(self, calls):
        self.calls = calls

    def build(self, horizon, as_of_date=None):
        self.calls.append(f"UNIVERSE_SNAPSHOT.{horizon}")
        return [
            {
                "source_company_id": "c1",
                "symbol": "AAA",
                "sector": "Technology",
                "industry": "Software",
                "basic_industry": "Enterprise Software",
                "market_cap": 123.0,
                "run_id": "ignored",
                "as_of_date": as_of_date,
                "return_available": True,
                "volume_available": True,
                "consistency_available": True,
                "financial_data_available": True,
                "technical_data_coverage": 1.0,
                "fundamental_data_coverage": 1.0,
                "eligible_for_sector": True,
                "eligible_for_industry": True,
                "eligible_for_basic_industry": True,
                "exclusion_reasons": [],
            }
        ]


class AlignmentFake:
    def __init__(self, calls, failures=None, assert_stage_persisted=None):
        self.calls = calls
        self.failures = failures or {}
        self.assert_stage_persisted = assert_stage_persisted

    def align(self, horizon):
        if self.assert_stage_persisted:
            self.assert_stage_persisted()
        self.calls.append(f"TECHNICAL_DATE_ALIGNMENT.{horizon}")
        return Alignment(horizon, self.failures.get(horizon, "READY"))


class MethodFake:
    def __init__(self, calls, label, method, callback=None, fail=False, message=None):
        self.calls = calls
        self.label = label
        self.method = method
        self.callback = callback
        self.fail = fail
        self.message = message or f"{label} failed token=secret postgresql://user:pass@host/db"

    def __getattr__(self, name):
        if name != self.method:
            raise AttributeError(name)

        def call(*args):
            horizon = args[-1] if args and args[-1] in prep.HORIZONS else None
            self.calls.append(f"{self.label}.{horizon}" if horizon else self.label)
            if self.fail:
                raise RuntimeError(self.message)
            if self.callback:
                self.callback(*args)

        return call


def _services(session, calls, align_failures=None, fail_label=None, align_assertion=None):
    services = {
        prep.UNIVERSE_SNAPSHOT: UniverseFake(calls),
        "TECHNICAL_DATE_ALIGNMENT": AlignmentFake(calls, align_failures, align_assertion),
        "TECHNICAL_RETURN": MethodFake(calls, "TECHNICAL_RETURN", "calculate_and_save_returns"),
        "TECHNICAL_VOLUME": MethodFake(calls, "TECHNICAL_VOLUME", "calculate_and_save_volumes"),
        "TECHNICAL_CONSISTENCY": MethodFake(
            calls,
            "TECHNICAL_CONSISTENCY",
            "calculate_and_save_consistency",
            lambda run_id, horizon: _upsert_technical_metric(session, run_id, horizon),
        ),
        "TECHNICAL_SECTOR_AGGREGATION": MethodFake(
            calls, "TECHNICAL_SECTOR_AGGREGATION", "aggregate_sectors"
        ),
        "TECHNICAL_SECTOR_SCORE": MethodFake(
            calls,
            "TECHNICAL_SECTOR_SCORE",
            "calculate_sector_scores",
            lambda run_id, horizon: _upsert_group(session, run_id, horizon, "SECTOR", technical=True),
        ),
        "TECHNICAL_INDUSTRY_AGGREGATION": MethodFake(
            calls, "TECHNICAL_INDUSTRY_AGGREGATION", "aggregate_industries"
        ),
        "TECHNICAL_INDUSTRY_SCORE": MethodFake(
            calls,
            "TECHNICAL_INDUSTRY_SCORE",
            "calculate_industry_scores",
            lambda run_id, horizon: _upsert_group(session, run_id, horizon, "INDUSTRY", technical=True),
        ),
        "TECHNICAL_BASIC_INDUSTRY_AGGREGATION": MethodFake(
            calls, "TECHNICAL_BASIC_INDUSTRY_AGGREGATION", "aggregate_basic_industries"
        ),
        "TECHNICAL_BASIC_INDUSTRY_SCORE": MethodFake(
            calls,
            "TECHNICAL_BASIC_INDUSTRY_SCORE",
            "calculate_basic_industry_scores",
            lambda run_id, horizon: _upsert_group(
                session, run_id, horizon, "BASIC_INDUSTRY", technical=True
            ),
        ),
        "FUNDAMENTAL_PERIOD_SELECTION": MethodFake(calls, "FUNDAMENTAL_PERIOD_SELECTION", "select_periods"),
        "FUNDAMENTAL_GROWTH": MethodFake(calls, "FUNDAMENTAL_GROWTH", "calculate_growth"),
        "FUNDAMENTAL_PROFITABILITY": MethodFake(
            calls, "FUNDAMENTAL_PROFITABILITY", "calculate_profitability"
        ),
        "FUNDAMENTAL_FINANCIAL_STRENGTH": MethodFake(
            calls, "FUNDAMENTAL_FINANCIAL_STRENGTH", "calculate_financial_strength"
        ),
        "FUNDAMENTAL_CASH_CONVERSION": MethodFake(
            calls, "FUNDAMENTAL_CASH_CONVERSION", "calculate_cash_conversion"
        ),
        "FUNDAMENTAL_PROFIT_STABILITY": MethodFake(
            calls, "FUNDAMENTAL_PROFIT_STABILITY", "calculate_profit_stability"
        ),
        "FUNDAMENTAL_PEER_MEDIAN": MethodFake(
            calls, "FUNDAMENTAL_PEER_MEDIAN", "resolve_peer_medians"
        ),
        "FUNDAMENTAL_GROWTH_SCORE": MethodFake(calls, "FUNDAMENTAL_GROWTH_SCORE", "score_growth"),
        "FUNDAMENTAL_PROFITABILITY_SCORE": MethodFake(
            calls, "FUNDAMENTAL_PROFITABILITY_SCORE", "score_profitability"
        ),
        "FUNDAMENTAL_FINANCIAL_STRENGTH_SCORE": MethodFake(
            calls, "FUNDAMENTAL_FINANCIAL_STRENGTH_SCORE", "score_financial_strength"
        ),
        "FUNDAMENTAL_EARNINGS_QUALITY_SCORE": MethodFake(
            calls, "FUNDAMENTAL_EARNINGS_QUALITY_SCORE", "score_earnings_quality"
        ),
        "COMPANY_FUNDAMENTAL_SCORE": MethodFake(
            calls,
            "COMPANY_FUNDAMENTAL_SCORE",
            "score_companies",
            lambda run_id: _upsert_fundamental_metric(session, run_id),
        ),
        "FUNDAMENTAL_SECTOR_AGGREGATION": MethodFake(
            calls, "FUNDAMENTAL_SECTOR_AGGREGATION", "aggregate_sectors"
        ),
        "FUNDAMENTAL_SECTOR_METRIC_NORMALIZATION": MethodFake(
            calls, "FUNDAMENTAL_SECTOR_METRIC_NORMALIZATION", "normalize_metrics"
        ),
        "FUNDAMENTAL_SECTOR_TRANSITION_SCORE": MethodFake(
            calls, "FUNDAMENTAL_SECTOR_TRANSITION_SCORE", "calculate_transition_scores"
        ),
        "FUNDAMENTAL_SECTOR_PILLAR_SCORE": MethodFake(
            calls, "FUNDAMENTAL_SECTOR_PILLAR_SCORE", "calculate_pillar_scores"
        ),
        "FUNDAMENTAL_SECTOR_SCORE": MethodFake(
            calls,
            "FUNDAMENTAL_SECTOR_SCORE",
            "calculate_final_scores",
            lambda run_id: _upsert_group(session, run_id, "1Y", "SECTOR", fundamental=True),
        ),
        "FUNDAMENTAL_INDUSTRY_AGGREGATION": MethodFake(
            calls, "FUNDAMENTAL_INDUSTRY_AGGREGATION", "aggregate_industries"
        ),
        "FUNDAMENTAL_INDUSTRY_METRIC_NORMALIZATION": MethodFake(
            calls, "FUNDAMENTAL_INDUSTRY_METRIC_NORMALIZATION", "normalize_industry_metrics"
        ),
        "FUNDAMENTAL_INDUSTRY_TRANSITION_SCORE": MethodFake(
            calls, "FUNDAMENTAL_INDUSTRY_TRANSITION_SCORE", "calculate_transition_scores"
        ),
        "FUNDAMENTAL_INDUSTRY_PILLAR_SCORE": MethodFake(
            calls, "FUNDAMENTAL_INDUSTRY_PILLAR_SCORE", "calculate_pillar_scores"
        ),
        "FUNDAMENTAL_INDUSTRY_SCORE": MethodFake(
            calls,
            "FUNDAMENTAL_INDUSTRY_SCORE",
            "calculate_industry_scores",
            lambda run_id: _upsert_group(session, run_id, "1Y", "INDUSTRY", fundamental=True),
        ),
        "FUNDAMENTAL_BASIC_INDUSTRY_AGGREGATION": MethodFake(
            calls, "FUNDAMENTAL_BASIC_INDUSTRY_AGGREGATION", "aggregate_basic_industries"
        ),
        "FUNDAMENTAL_BASIC_INDUSTRY_METRIC_NORMALIZATION": MethodFake(
            calls,
            "FUNDAMENTAL_BASIC_INDUSTRY_METRIC_NORMALIZATION",
            "normalize_basic_industry_metrics",
        ),
        "FUNDAMENTAL_BASIC_INDUSTRY_TRANSITION_SCORE": MethodFake(
            calls,
            "FUNDAMENTAL_BASIC_INDUSTRY_TRANSITION_SCORE",
            "calculate_basic_industry_transitions",
        ),
        "FUNDAMENTAL_BASIC_INDUSTRY_PILLAR_SCORE": MethodFake(
            calls, "FUNDAMENTAL_BASIC_INDUSTRY_PILLAR_SCORE", "calculate_pillar_scores"
        ),
        "FUNDAMENTAL_BASIC_INDUSTRY_SCORE": MethodFake(
            calls,
            "FUNDAMENTAL_BASIC_INDUSTRY_SCORE",
            "calculate_basic_industry_scores",
            lambda run_id: _upsert_group(session, run_id, "1Y", "BASIC_INDUSTRY", fundamental=True),
        ),
    }
    if fail_label:
        services[fail_label].fail = True
    return services


def _prepare_success(session, run=None, calls=None, **kwargs):
    calls = calls if calls is not None else []
    run = run or _make_run(session)
    result = DiscoveryUpstreamPreparationService(
        session,
        services=_services(session, calls, **kwargs),
        lock_enabled=False,
    ).prepare(run.id)
    return run, result, calls


def test_no_formulas_exist_in_orchestrator():
    source = inspect.getsource(prep)

    assert "calculate_final_sector_score" not in source
    assert "calculate_final_industry_score" not in source
    assert "calculate_final_basic_industry_score" not in source
    assert "calculate_final_stock_score" not in source


def test_source_universe_snapshot_runs_first(disc_session):
    _, _, calls = _prepare_success(disc_session)

    assert calls[:3] == [
        "UNIVERSE_SNAPSHOT.SHORT",
        "UNIVERSE_SNAPSHOT.MID",
        "UNIVERSE_SNAPSHOT.LONG",
    ]
    assert calls.index("UNIVERSE_SNAPSHOT.LONG") < calls.index("TECHNICAL_DATE_ALIGNMENT.SHORT")


def test_completed_results_survive_failure(disc_session):
    run = _make_run(disc_session)
    calls = []
    result = DiscoveryUpstreamPreparationService(
        disc_session,
        services=_services(disc_session, calls, align_failures={"SHORT": "FAILED", "MID": "FAILED", "LONG": "FAILED"}),
        lock_enabled=False,
    ).prepare(run.id)

    assert result["status"] == prep.PREP_FAILED
    assert result["stage_results"][prep.UNIVERSE_SNAPSHOT]["status"] == prep.STAGE_COMPLETED


def test_resume_skips_completed_stages(disc_session):
    run = _make_run(
        disc_session,
        prep_results={
            prep.UNIVERSE_SNAPSHOT: {
                "status": prep.STAGE_COMPLETED,
                "started_at": "x",
                "completed_at": "x",
                "warnings": [],
                "metadata": {},
            }
        },
    )
    run.preparation_status = prep.PREP_FAILED
    disc_session.commit()
    calls = []

    DiscoveryUpstreamPreparationService(
        disc_session, services=_services(disc_session, calls), lock_enabled=False
    ).prepare(run.id)

    assert "UNIVERSE_SNAPSHOT.SHORT" not in calls
    assert disc_session.get(DiscoveryRun, run.id).preparation_resume_count == 1


def test_same_run_concurrency_rejection(disc_session):
    run = _make_run(disc_session)
    key = f"discovery_preparation:{run.id}"
    assert disc_session.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}
    ).scalar()
    other = DiscoverySessionLocal()
    try:
        result = DiscoveryUpstreamPreparationService(other, services={}, lock_enabled=True).prepare(run.id)
        assert result["error_code"] == prep.E_ALREADY_RUNNING
    finally:
        disc_session.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
        disc_session.commit()
        other.close()


def test_advisory_lock_uses_one_connection():
    class Scalar:
        def __init__(self, value):
            self.value = value

        def scalar(self):
            return self.value

    class FakeSession:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params):
            self.calls.append((id(self), str(statement), params))
            return Scalar(True)

        def commit(self):
            pass

        def rollback(self):
            pass

    session = FakeSession()
    service = DiscoveryUpstreamPreparationService(session, lock_enabled=True)

    assert service._acquire_lock("run1") is True
    service._release_lock("run1")

    assert len({call[0] for call in session.calls}) == 1
    assert session.calls[0][2] == {"key": "discovery_preparation:run1"}
    assert session.calls[1][2] == {"key": "discovery_preparation:run1"}


def test_advisory_lock_releases_in_finally(disc_session):
    run = _make_run(disc_session)
    result = DiscoveryUpstreamPreparationService(
        disc_session, services={}, lock_enabled=True
    ).prepare(run.id)
    key = f"discovery_preparation:{run.id}"

    acquired = disc_session.execute(
        text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": key}
    ).scalar()
    disc_session.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": key})
    disc_session.commit()

    assert result["status"] == prep.PREP_FAILED
    assert acquired is True


def test_no_macro_ranking_or_selection_service_is_called(disc_session):
    _, _, calls = _prepare_success(disc_session)

    disallowed = ("MACRO", "RANKING", "STOCK_CANDIDATE", "DISCOVERY_SELECTION")
    assert not any(any(part in call for part in disallowed) for call in calls)


def test_no_parallel_or_llm_call():
    source = inspect.getsource(prep)

    assert "Parallel" not in source
    assert "PARALLEL" not in source
    assert "LLM" not in source
