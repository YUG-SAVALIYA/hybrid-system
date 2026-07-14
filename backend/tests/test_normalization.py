import pytest
from services.common.period_parser import PeriodParser, PeriodComparator
from services.common.numeric_cleaner import NumericCleaner
from services.common.sector_classifier import SectorClassifier
from services.common.coverage_report import CoverageReportService
from database import SourceSessionLocal

def test_period_parser():
    # Valid
    p1 = PeriodParser.parse("Mar 2024")
    assert p1["period_type"] == "ANNUAL"
    assert p1["period_end"] == "2024-03-31"
    assert p1["financial_year"] == "FY2024"
    assert p1["parse_status"] == "VALID"

    p2 = PeriodParser.parse("Sep 2025")
    assert p2["period_type"] == "ANNUAL"
    assert p2["period_end"] == "2025-09-30"
    
    # Invalid
    p3 = PeriodParser.parse("Invalid Format")
    assert p3["period_type"] == "UNKNOWN"
    assert p3["parse_status"] == "INVALID"
    
    p4 = PeriodParser.parse(None)
    assert p4["period_type"] == "UNKNOWN"

def test_period_comparator():
    assert PeriodComparator.get_previous_comparable_period("Mar 2024") == "Mar 2023"
    assert PeriodComparator.get_previous_comparable_period("Dec 2025") == "Dec 2024"
    assert PeriodComparator.get_previous_comparable_period("Unknown") is None
    
    assert PeriodComparator.is_comparable("Mar 2024", "Mar 2023") is True
    assert PeriodComparator.is_comparable("Mar 2024", "Unknown") is False

def test_numeric_cleaner():
    assert NumericCleaner.clean(100.5) == 100.5
    assert NumericCleaner.clean(100) == 100.0
    assert NumericCleaner.clean("-20.5") == -20.5
    assert NumericCleaner.clean("  1,234.56  ") == 1234.56
    assert NumericCleaner.clean("") is None
    assert NumericCleaner.clean("   ") is None
    assert NumericCleaner.clean(None) is None
    assert NumericCleaner.clean("Invalid") is None

def test_sector_classifier():
    assert SectorClassifier.is_financial_business(None, "Private Sector Bank", None) is True
    assert SectorClassifier.is_financial_business(None, None, "Life Insurance") is True
    assert SectorClassifier.is_financial_business("Technology", "Software", "Software") is False
    assert SectorClassifier.is_financial_business(None, None, None) is False

def test_coverage_report():
    # Requires DB connection
    session = SourceSessionLocal()
    try:
        service = CoverageReportService(session, benchmark_symbol="NIFTY 500")
        report = service.run_report()
        
        # Test basic structures
        assert "total_companies" in report
        assert "pnl_companies" in report
        assert "benchmark_status" in report
        
        # We know NIFTY 500 is missing based on research
        assert report["benchmark_status"] == "BENCHMARK_DATA_UNAVAILABLE"
        assert report["benchmark_count"] == 0
    finally:
        session.close()

def test_canonical_field_mapping():
    # Simple check on the mapped dict to ensure all fields are documented
    CANONICAL_MAPPING = {
        "sales": "sales",
        "operating_profit": "operating_profit",
        "net_profit": "net_profit",
        "share_capital": "equity_capital",
        "reserves": "reserves",
        "borrowings": "borrowings",
        "operating_cash_flow": "cash_from_operating_activity"
    }
    
    # In practice, these would map to model attributes
    from models.source import CompanyProfitLoss, CompanyBalanceSheet, CompanyCashFlow
    
    assert hasattr(CompanyProfitLoss, CANONICAL_MAPPING["sales"])
    assert hasattr(CompanyProfitLoss, CANONICAL_MAPPING["operating_profit"])
    assert hasattr(CompanyProfitLoss, CANONICAL_MAPPING["net_profit"])
    assert hasattr(CompanyBalanceSheet, CANONICAL_MAPPING["share_capital"])
    assert hasattr(CompanyBalanceSheet, CANONICAL_MAPPING["reserves"])
    assert hasattr(CompanyBalanceSheet, CANONICAL_MAPPING["borrowings"])
    assert hasattr(CompanyCashFlow, CANONICAL_MAPPING["operating_cash_flow"])
