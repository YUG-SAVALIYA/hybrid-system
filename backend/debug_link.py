import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import SourceSessionLocal
from sqlalchemy import text

session = SourceSessionLocal()

# Check companies table columns
all_company_cols = session.execute(text("""
    SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'companies' ORDER BY ordinal_position
""")).fetchall()
print(f"Companies columns: {[r[0] for r in all_company_cols]}")

# Check how profit_loss links -- maybe via share_symbol?
sample_pnl = session.execute(text("SELECT company_id, period FROM company_profit_losses LIMIT 3")).fetchall()
print(f"P&L company_ids: {sample_pnl}")

# Try to find what column the financial tables join on
r = session.execute(text("""
    SELECT c.id as cid, c.share_symbol, o.id as oid, o.share_symbol as o_sym
    FROM companies c
    JOIN company_overviews o ON c.share_symbol = o.share_symbol
    LIMIT 3
""")).fetchall()
print(f"Company join via share_symbol: {r}")

# Check if company_overviews.id = company_profit_losses.company_id
r2 = session.execute(text("""
    SELECT COUNT(*) FROM company_overviews o
    JOIN company_profit_losses p ON o.id = p.company_id
""")).scalar()
print(f"Overview→P&L join count: {r2}")

session.close()
