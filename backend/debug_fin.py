"""Debug why all companies show INSUFFICIENT_FINANCIAL_DATA."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import SourceSessionLocal
from sqlalchemy import text
from services.common.period_parser import PeriodParser, PeriodComparator

session = SourceSessionLocal()

# Get 3 companies and their P&L periods
rows = session.execute(text("""
    SELECT company_id, period FROM company_profit_losses
    WHERE company_id IN (
        SELECT company_id FROM company_profit_losses GROUP BY company_id ORDER BY COUNT(*) DESC LIMIT 3
    )
    ORDER BY company_id, period
""")).fetchall()

by_co = {}
for r in rows:
    by_co.setdefault(r.company_id, []).append(r.period)

for cid, periods in by_co.items():
    print(f"\nCompany: {cid}")
    print(f"  periods: {periods}")
    parsed_valid = sorted(
        [p for p in periods if PeriodParser.parse(p)["parse_status"] == "VALID"],
        key=lambda p: PeriodParser.parse(p)["period_end"],
        reverse=True
    )
    latest = parsed_valid[0] if parsed_valid else None
    print(f"  latest valid: {latest}")
    if latest:
        prior_candidate = PeriodComparator.get_previous_comparable_period(latest)
        print(f"  prior_candidate: {prior_candidate}")
        print(f"  periods set repr: {[repr(p) for p in periods]}")
        print(f"  prior_candidate repr: {repr(prior_candidate)}")
        print(f"  prior in periods: {prior_candidate in periods}")

session.close()
