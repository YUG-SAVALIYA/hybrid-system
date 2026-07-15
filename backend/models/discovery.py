from sqlalchemy import Column, String, Float, Integer, JSON, DateTime, Date, Enum, func, Boolean, BigInteger, UniqueConstraint
from database import DiscoveryBase
import datetime

class DiscoveryRun(DiscoveryBase):
    __tablename__ = "discovery_runs"

    id = Column(String, primary_key=True)
    run_date = Column(String) # Or Date depending on preference
    horizon = Column(String)
    status = Column(String)
    current_stage = Column(String)
    last_completed_stage = Column(String)
    source_data_as_of = Column(String)
    started_at = Column(DateTime, default=datetime.datetime.utcnow)
    completed_at = Column(DateTime)
    stage_results = Column(JSON)
    warnings = Column(JSON)
    error_code = Column(String)
    error_message = Column(String)
    resume_count = Column(Integer, nullable=False, default=0)
    preparation_status = Column(String)
    preparation_current_stage = Column(String)
    preparation_last_completed_stage = Column(String)
    preparation_stage_results = Column(JSON)
    preparation_warnings = Column(JSON)
    preparation_error_code = Column(String)
    preparation_error_message = Column(String)
    preparation_started_at = Column(DateTime)
    preparation_completed_at = Column(DateTime)
    preparation_resume_count = Column(Integer, nullable=False, default=0)
    configuration_snapshot = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)


class CompanyTechnicalMetric(DiscoveryBase):
    __tablename__ = "company_technical_metrics"

    id = Column(String, primary_key=True)
    run_id = Column(String)
    source_company_id = Column(String)
    symbol = Column(String)
    sector = Column(String)
    industry = Column(String)
    basic_industry = Column(String)
    horizon = Column(String)
    # Date anchors
    as_of_date = Column(Date)
    company_candle_date = Column(String)   # latest candle date used for company
    benchmark_candle_date = Column(String) # latest candle date used for benchmark
    # Company price points
    current_close = Column(Float)
    start_close = Column(Float)
    company_return = Column(Float)
    # Benchmark price points
    benchmark_current_close = Column(Float)
    benchmark_start_close = Column(Float)
    benchmark_return = Column(Float)
    relative_return = Column(Float)
    # Volume metrics
    average_volume_current = Column(Float)
    average_volume_previous = Column(Float)
    volume_change = Column(Float)
    # Breadth / consistency metrics
    positive_period_ratio = Column(Float)
    benchmark_outperformance_ratio = Column(Float)
    company_consistency_score = Column(Float)
    final_technical_score = Column(Float)
    technical_status = Column(String)
    technical_eligible_for_selection = Column(Boolean)
    # Component availability flags
    return_available = Column(Boolean)
    volume_available = Column(Boolean)
    consistency_available = Column(Boolean)
    # Quality
    data_coverage = Column(Float)
    warnings = Column(JSON)
    calculation_details = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('run_id', 'source_company_id', 'horizon',
                         name='uq_technical_run_company_horizon'),
    )


class CompanyFundamentalMetric(DiscoveryBase):
    __tablename__ = "company_fundamental_metrics"

    id = Column(String, primary_key=True)
    run_id = Column(String)
    source_company_id = Column(String)
    symbol = Column(String)
    sector = Column(String)
    industry = Column(String)
    basic_industry = Column(String)
    growth_score = Column(Float)
    profitability_score = Column(Float)
    financial_strength_score = Column(Float)
    earnings_quality_score = Column(Float)
    final_fundamental_score = Column(Float)
    fundamental_status = Column(String)
    fundamental_eligible_for_selection = Column(Boolean)
    benchmark_level_used = Column(String)
    data_coverage = Column(Float)
    unavailable_fields = Column(JSON)
    calculation_details = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class GroupScore(DiscoveryBase):
    __tablename__ = "group_scores"

    id = Column(String, primary_key=True)
    run_id = Column(String)
    entity_type = Column(String)   # SECTOR, INDUSTRY or BASIC_INDUSTRY
    entity_name = Column(String)
    # parent_sector and parent_industry are empty strings (not NULL) for top-level
    # entities so the unique constraint works correctly in PostgreSQL.
    parent_sector   = Column(String, nullable=False, server_default="", default="")
    parent_industry = Column(String, nullable=False, server_default="", default="")
    horizon = Column(String)
    constituent_count = Column(Integer)
    eligible_constituent_count = Column(Integer)
    technical_return_score = Column(Float)
    technical_breadth_score = Column(Float)
    technical_volume_score = Column(Float)
    technical_consistency_score = Column(Float)
    technical_score = Column(Float)
    fundamental_growth_score = Column(Float)
    fundamental_profitability_score = Column(Float)
    fundamental_financial_strength_score = Column(Float)
    fundamental_earnings_quality_score = Column(Float)
    fundamental_score = Column(Float)
    macro_score = Column(Float)
    final_score = Column(Float)
    rank = Column(Integer)
    data_coverage = Column(Float)
    warnings = Column(JSON)
    calculation_details = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            'run_id', 'entity_type', 'entity_name',
            'parent_sector', 'parent_industry', 'horizon',
            name='uq_group_score_hierarchy_horizon'
        ),
    )


class MacroSearchBatch(DiscoveryBase):
    __tablename__ = "macro_search_batches"

    id = Column(String, primary_key=True)          # stable: macro-parallel-{run_id}
    run_id = Column(String, nullable=False)
    provider = Column(String, nullable=False)       # e.g. PARALLEL_AI_SEARCH
    external_batch_id = Column(String)              # provider-assigned search_id / batch ref
    session_id = Column(String)
    status = Column(String, nullable=False)         # PENDING / COMPLETED / COMPLETED_WITH_WARNINGS / FAILED
    total_results = Column(Integer, default=0)
    failed_categories = Column(JSON, default=list)
    warnings = Column(JSON, default=list)
    provider_metadata = Column(JSON)                # objective, search_queries, usage, retrieved_at, etc.
    results = Column(JSON, default=list)            # list of normalized result dicts
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('run_id', 'provider', name='uq_macro_batch_run_provider'),
    )


class MacroSummary(DiscoveryBase):
    __tablename__ = "macro_summaries"

    id = Column(String, primary_key=True)
    run_id = Column(String, nullable=False)
    source_batch_id = Column(String)                # FK-style ref to macro_search_batches.id
    summary_type = Column(String, nullable=False)   # MACRO_FILTER
    status = Column(String, nullable=False)         # COMPLETED / COMPLETED_WITH_WARNINGS / FAILED
    model_name = Column(String)
    prompt_version = Column(String)
    category_summaries = Column(JSON)               # {category: summary_dict}
    overall_synthesis = Column(JSON)
    document_statistics = Column(JSON)              # per-category + aggregate counts
    warnings = Column(JSON, default=list)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('run_id', 'summary_type', name='uq_macro_summary_run_type'),
    )


class MacroEntityImpact(DiscoveryBase):
    __tablename__ = "macro_entity_impacts"

    id = Column(String, primary_key=True)
    run_id = Column(String, nullable=False)
    horizon = Column(String, nullable=False, server_default="", default="")
    source_summary_id = Column(String)
    source_parent_impact_id = Column(String)
    entity_type = Column(String, nullable=False)     # SECTOR / INDUSTRY / BASIC_INDUSTRY
    entity_name = Column(String, nullable=False)
    parent_sector = Column(String, nullable=False, server_default="", default="")
    parent_industry = Column(String, nullable=False, server_default="", default="")
    category_impacts = Column(JSON)
    overall_impact = Column(JSON)
    impact = Column(String)
    confidence = Column(String)
    reason = Column(String)
    evidence_refs = Column(JSON, default=list)
    relationship_to_parent_sector = Column(String)
    relationship_to_parent_industry = Column(String)
    warnings = Column(JSON, default=list)
    status = Column(String, nullable=False)
    model_name = Column(String)
    prompt_version = Column(String)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            'run_id', 'horizon', 'entity_type', 'entity_name', 'parent_sector', 'parent_industry',
            name='uq_macro_entity_impact_hierarchy'
        ),
    )

class DiscoverySelection(DiscoveryBase):
    __tablename__ = "discovery_selections"

    id = Column(String, primary_key=True)
    run_id = Column(String, nullable=False)
    horizon = Column(String, nullable=False)
    entity_type = Column(String, nullable=False)
    entity_name = Column(String, nullable=False)
    company_id = Column(String)
    symbol = Column(String)
    parent_sector = Column(String, nullable=False, server_default="", default="")
    parent_industry = Column(String, nullable=False, server_default="", default="")
    basic_industry = Column(String)
    rank = Column(Integer)
    final_score = Column(Float)
    technical_score = Column(Float)
    fundamental_score = Column(Float)
    macro_score = Column(Float)
    selected = Column(Boolean, nullable=False, default=True)
    selection_reason = Column(String)
    calculation_details = Column(JSON)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            'run_id', 'horizon', 'entity_type', 'entity_name',
            'parent_sector', 'parent_industry',
            name='uq_discovery_selection_hierarchy'
        ),
    )

class BenchmarkCandle(DiscoveryBase):
    __tablename__ = "benchmark_candles"
    id = Column(String, primary_key=True)
    benchmark_code = Column(String, nullable=False)
    benchmark_name = Column(String, nullable=False)
    trade_date = Column(Date, nullable=False)
    open = Column(Float, nullable=False)
    high = Column(Float, nullable=False)
    low = Column(Float, nullable=False)
    close = Column(Float, nullable=False)
    volume = Column(BigInteger, nullable=True)
    source_name = Column(String, nullable=False)
    source_reference = Column(String, nullable=True)
    import_batch_id = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint('benchmark_code', 'trade_date', name='uq_benchmark_code_trade_date'),
    )

class EligibleUniverseSnapshot(DiscoveryBase):
    __tablename__ = "eligible_universe_snapshots"
    id = Column(String, primary_key=True)
    run_id = Column(String, nullable=True)
    as_of_date = Column(Date, nullable=False)
    horizon = Column(String, nullable=False)
    source_company_id = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    sector = Column(String, nullable=False)
    industry = Column(String, nullable=False)
    basic_industry = Column(String, nullable=True)
    market_cap = Column(Float, nullable=True)
    return_available = Column(Boolean, nullable=False)
    volume_available = Column(Boolean, nullable=False)
    consistency_available = Column(Boolean, nullable=False)
    financial_data_available = Column(Boolean, nullable=False)
    technical_data_coverage = Column(Float, nullable=False)
    fundamental_data_coverage = Column(Float, nullable=False)
    eligible_for_sector = Column(Boolean, nullable=False)
    eligible_for_industry = Column(Boolean, nullable=False)
    eligible_for_basic_industry = Column(Boolean, nullable=False)
    exclusion_reasons = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


class StockCandidateSnapshot(DiscoveryBase):
    __tablename__ = "stock_candidate_snapshots"

    id = Column(String, primary_key=True)
    run_id = Column(String, nullable=False)
    horizon = Column(String, nullable=False)
    company_id = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    sector = Column(String, nullable=False)
    industry = Column(String, nullable=False)
    basic_industry = Column(String, nullable=False)
    technical_metric_id = Column(String)
    fundamental_metric_id = Column(String)
    technical_available = Column(Boolean, nullable=False, default=False)
    fundamental_available = Column(Boolean, nullable=False, default=False)
    eligible = Column(Boolean, nullable=False, default=False)
    status = Column(String, nullable=False)
    warnings = Column(JSON, default=list)
    calculation_details = Column(JSON)
    technical_score = Column(Float)
    fundamental_score = Column(Float)
    inherited_macro_score = Column(Float)
    final_score = Column(Float)
    score_coverage_pct = Column(Float)
    score_status = Column(String)
    score_eligible = Column(Boolean)
    score_warnings = Column(JSON, default=list)
    score_details = Column(JSON)
    scored_at = Column(DateTime)
    rank = Column(Integer)
    selected = Column(Boolean, nullable=False, default=False)
    selection_reason = Column(String)
    selected_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            'run_id', 'horizon', 'company_id',
            name='uq_stock_candidate_run_horizon_company'
        ),
    )
