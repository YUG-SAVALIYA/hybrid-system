import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import SourceSessionLocal
from sqlalchemy import text

session = SourceSessionLocal()

# Check the join issue
r = session.execute(text("""
    SELECT COUNT(DISTINCT c.id) 
    FROM companies c
    JOIN company_profit_losses p ON c.id = p.company_id
    JOIN company_balance_sheets b ON c.id = b.company_id
    JOIN company_cash_flows f ON c.id = f.company_id
""")).scalar()
print(f"All-three join count (standard): {r}")

# Maybe the join key mismatch - does company_overviews have the id?
r2 = session.execute(text("""
    SELECT COUNT(*) FROM companies c 
    JOIN company_profit_losses p ON c.id = p.company_id LIMIT 5
""")).scalar()
print(f"P&L join check: {r2}")

# Check sample ids
ids = session.execute(text("SELECT id FROM companies LIMIT 3")).fetchall()
print(f"Company IDs: {ids}")

pnl_ids = session.execute(text("SELECT DISTINCT company_id FROM company_profit_losses LIMIT 3")).fetchall()
print(f"P&L company_ids: {pnl_ids}")

session.close()
