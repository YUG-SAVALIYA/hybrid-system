"""
Tests for FundamentalPeerMedianService.
"""
import uuid
import pytest
from sqlalchemy import text
import copy

from database import DiscoverySessionLocal
from models.discovery import CompanyFundamentalMetric
from services.fundamental.fundamental_peer_median import FundamentalPeerMedianService


@pytest.fixture
def disc_session():
    session = DiscoverySessionLocal()
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    yield session
    session.execute(text("DELETE FROM company_fundamental_metrics"))
    session.commit()
    session.close()

def _create_mock_metrics(session, run_id):
    def _create(sym, sec, ind, bas, values, std_debt=True):
        calc_details = {
            "financial_strength": {
                "standard_debt_rule_applicable": std_debt
            },
            "warnings": ["EXISTING_WARN"]
        }
        
        # Populate metrics
        # growth
        calc_details["growth"] = {}
        if "sales" in values:
            calc_details["growth"]["sales_growth_pct"] = values["sales"]
            calc_details["growth"]["sales_growth_available"] = True
            
        # profitability
        calc_details["profitability"] = {}
        if "op_margin" in values:
            calc_details["profitability"]["latest_operating_margin_pct"] = values["op_margin"]
            calc_details["profitability"]["latest_operating_margin_available"] = True
            
        # debt
        if "debt" in values:
            calc_details["financial_strength"]["debt_to_equity"] = values["debt"]
            calc_details["financial_strength"]["debt_to_equity_available"] = True
            
        # eq
        calc_details["earnings_quality"] = {"cash_conversion": {}, "profit_stability": {}}
        if "ocf" in values:
            calc_details["earnings_quality"]["cash_conversion"]["latest"] = {"ocf_to_pat": values["ocf"]}
            calc_details["earnings_quality"]["cash_conversion"]["latest_cash_conversion_available"] = True
            
        rec = CompanyFundamentalMetric(
            id=str(uuid.uuid4()),
            run_id=run_id,
            source_company_id=sym,
            symbol=sym,
            sector=sec,
            industry=ind,
            basic_industry=bas,
            calculation_details=calc_details
        )
        session.add(rec)
        
    # Target C1 (Sector A, Ind A, Basic A)
    _create("C1", "SecA", "IndA", "BasA", {"sales": 10.0, "op_margin": 15.0, "debt": 0.5, "ocf": 1.2})
    
    # 1. Basic-industry median resolution (need 3 basic peers) -> Odd-count (3 peers) -> 7.
    _create("B1", "SecA", "IndA", "BasA", {"sales": 8.0})
    _create("B2", "SecA", "IndA", "BasA", {"sales": 9.0})
    _create("B3", "SecA", "IndA", "BasA", {"sales": 11.0})
    
    # 2. Industry fallback, 6. Even count (4 peers for IndA minus BasA)
    # Target needs IndA peers for op_margin because B1,B2,B3 lack op_margin.
    _create("I1", "SecA", "IndA", "BasX", {"op_margin": 10.0})
    _create("I2", "SecA", "IndA", "BasY", {"op_margin": 12.0})
    _create("I3", "SecA", "IndA", "BasZ", {"op_margin": 14.0})
    _create("I4", "SecA", "IndA", "BasW", {"op_margin": 16.0}) # Middle values: 12.0, 14.0 -> average 13.0
    
    # 3. Sector fallback (Target needs SecA peers for ocf because IndA lacks it)
    _create("S1", "SecA", "IndX", "BasX", {"ocf": 1.0})
    _create("S2", "SecA", "IndY", "BasY", {"ocf": 2.0})
    _create("S3", "SecA", "IndZ", "BasZ", {"ocf": 3.0}) # middle value 2.0
    
    # 8. Same industry name in another sector excluded
    _create("F1", "SecB", "IndA", "BasA", {"sales": 50.0, "op_margin": 50.0}) 
    
    # Target C2 (No valid fallback)
    _create("C2", "SecC", "IndC", "BasC", {"sales": 10.0})
    # Only 2 peers
    _create("E1", "SecC", "IndC", "BasC", {"sales": 5.0})
    _create("E2", "SecC", "IndC", "BasC", {"sales": 6.0})
    
    # Target C3 (Debt to equity excludes financial businesses - 11, 12)
    _create("C3", "SecD", "IndD", "BasD", {"debt": 0.5}, std_debt=False)
    _create("C4", "SecD", "IndD", "BasD", {"debt": 1.5}, std_debt=True)
    _create("D1", "SecD", "IndD", "BasD", {"debt": 1.0}, std_debt=True)
    _create("D2", "SecD", "IndD", "BasD", {"debt": 1.2}, std_debt=True)
    _create("D3", "SecD", "IndD", "BasD", {"debt": 1.4}, std_debt=True)
    _create("D4", "SecD", "IndD", "BasD", {"debt": 2.0}, std_debt=False) # financial business peer
    
    # Target C5 (Missing and non-finite values excluded - 10)
    _create("C5", "SecE", "IndE", "BasE", {"sales": 10.0})
    # We test non-finite via strings or None since Postgres rejects real inf/nan
    _create("M1", "SecE", "IndE", "BasE", {"sales": "inf"})
    _create("M2", "SecE", "IndE", "BasE", {"sales": "NaN"})
    _create("M3", "SecE", "IndE", "BasE", {}) # missing
    _create("M4", "SecE", "IndE", "BasE", {"sales": 2.0})
    _create("M5", "SecE", "IndE", "BasE", {"sales": 3.0})
    
    session.commit()

def test_fundamental_peer_median_service(disc_session):
    _create_mock_metrics(disc_session, "run1")
    
    # 17. No source DB access!
    svc = FundamentalPeerMedianService(disc_session)
    svc.resolve_peer_medians("run1")
    
    def get_c(sym):
        return disc_session.query(CompanyFundamentalMetric).filter_by(symbol=sym).first()
        
    c1 = get_c("C1")
    pb1 = c1.calculation_details["peer_benchmarks"]
    m1 = pb1["metrics"]
    
    # 1. Basic-industry median, 7. Odd count
    assert m1["sales_growth_pct"]["comparison_level"] == "BASIC_INDUSTRY"
    assert m1["sales_growth_pct"]["peer_count"] == 3
    assert m1["sales_growth_pct"]["peer_median"] == 9.0 # B1=8, B2=9, B3=11
    
    # 2. Industry fallback, 6. Even count
    assert m1["latest_operating_margin_pct"]["comparison_level"] == "INDUSTRY"
    assert m1["latest_operating_margin_pct"]["peer_count"] == 4
    assert m1["latest_operating_margin_pct"]["peer_median"] == 13.0 # I1=10, I2=12, I3=14, I4=16
    
    # 3. Sector fallback
    assert m1["latest_ocf_to_pat"]["comparison_level"] == "SECTOR"
    assert m1["latest_ocf_to_pat"]["peer_count"] == 3
    assert m1["latest_ocf_to_pat"]["peer_median"] == 2.0
    
    # 5. Target company excluded from peers (C1 values didn't skew medians)
    # 8. Same industry name in another sector excluded (F1's 50.0 didn't appear in B or I)
    
    # 9. Each metric resolves independently (BASIC_IND for sales, IND for margin, SEC for ocf)
    
    # 13. Coverage calculation
    # C1 has 4 applicable metrics: sales, op_margin, debt, ocf.
    # debt has no peers in SecA for C1. So it fails (INSUFFICIENT_SECTOR_PEERS).
    # so resolved = 3, applicable = 4. Coverage = 75.0%
    assert pb1["applicable_metric_count"] == 4
    assert pb1["resolved_metric_count"] == 3
    assert pb1["coverage_pct"] == 75.0
    
    # 14. Partial and unavailable warnings
    assert "PEER_BASELINE_PARTIAL" in c1.calculation_details["warnings"]
    assert "EXISTING_WARN" in c1.calculation_details["warnings"]
    
    # 4. No valid fallback
    c2 = get_c("C2")
    pb2 = c2.calculation_details["peer_benchmarks"]
    assert pb2["resolved_metric_count"] == 0
    assert "PEER_BASELINE_UNAVAILABLE" in c2.calculation_details["warnings"]
    assert pb2["metrics"]["sales_growth_pct"]["reason"] == "INSUFFICIENT_SECTOR_PEERS"
    
    # 11. Debt-to-equity excludes financial businesses (Target C3 is financial)
    c3 = get_c("C3")
    pb3 = c3.calculation_details["peer_benchmarks"]
    d3 = pb3["metrics"]["debt_to_equity"]
    assert d3["reason"] == "N_A_STANDARD_DEBT_RULE"
    assert d3["available"] is False
    assert pb3["applicable_metric_count"] == 0 # Debt is not applicable
    assert pb3["coverage_pct"] == 0.0
    
    # 12. Debt peers include only standard debt-rule companies (Target C4 is standard)
    c4 = get_c("C4")
    pb4 = c4.calculation_details["peer_benchmarks"]
    d4 = pb4["metrics"]["debt_to_equity"]
    assert d4["peer_count"] == 3 # D1, D2, D3. D4 is excluded.
    assert d4["peer_median"] == 1.2
    
    # 10. Missing and non-finite values are excluded
    c5 = get_c("C5")
    pb5 = c5.calculation_details["peer_benchmarks"]
    m5 = pb5["metrics"]["sales_growth_pct"]
    assert m5["peer_count"] == 0 # M4, M5 (inf, nan, missing are skipped). But wait!
    # Target requires 3 peers to resolve. So it fails!
    assert m5["available"] is False
    assert m5["reason"] == "INSUFFICIENT_SECTOR_PEERS"
    
    # 15. Existing fundamental JSON remains unchanged
    assert c1.calculation_details["growth"]["sales_growth_pct"] == 10.0
    
    # 16. Idempotent update
    svc.resolve_peer_medians("run1")
    c1_2 = get_c("C1")
    assert c1_2.calculation_details["peer_benchmarks"]["coverage_pct"] == 75.0
