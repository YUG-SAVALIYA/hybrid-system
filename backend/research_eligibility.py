import os
from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/trade_signal"
engine = create_engine(DATABASE_URL)

def run_query(conn, query, params=None):
    return conn.execute(text(query), params or {}).fetchall()

with engine.connect() as conn:
    print("=== 4. Annual-Period Cadence ===")
    # Max periods per company per calendar year
    cadence = run_query(conn, """
        WITH parsed AS (
            SELECT company_id, 
                   period,
                   RIGHT(period, 4) as year,
                   LEFT(period, 3) as month
            FROM company_profit_losses
        ),
        yearly_counts AS (
            SELECT company_id, year, COUNT(*) as c, COUNT(DISTINCT month) as dist_months
            FROM parsed
            GROUP BY company_id, year
        )
        SELECT 
            COUNT(CASE WHEN c > 1 THEN 1 END) as companies_with_multiple_per_year,
            COUNT(CASE WHEN dist_months > 1 THEN 1 END) as companies_with_mult_months_per_year
        FROM yearly_counts
    """)
    print(f"Cadence check (multiple per year, mult months per year): {cadence}")
    
    # Do they use the same recurring fiscal month?
    recurring = run_query(conn, """
        WITH parsed AS (
            SELECT company_id, LEFT(period, 3) as month
            FROM company_profit_losses
        ),
        month_counts AS (
            SELECT company_id, COUNT(DISTINCT month) as dist_months
            FROM parsed
            GROUP BY company_id
        )
        SELECT dist_months, COUNT(*) as company_count
        FROM month_counts
        GROUP BY dist_months
        ORDER BY dist_months
    """)
    print(f"Distinct months per company distribution: {recurring}")

    print("\n=== 7. Symbol Matching ===")
    exact_matches = run_query(conn, """
        SELECT COUNT(*) FROM companies c
        JOIN market_candles_cleaned m ON c.share_symbol = m.symbol
    """)[0][0]
    print(f"Exact matches (rows joined, not distinct): {exact_matches}")
    
    unmatched_companies = run_query(conn, """
        SELECT COUNT(*) FROM companies c 
        LEFT JOIN market_candles_cleaned m ON c.share_symbol = m.symbol
        WHERE m.id IS NULL
    """)[0][0]
    print(f"Companies without exact candle match: {unmatched_companies}")

    # Let's try simple normalization: strip whitespace and upper case
    norm_matches = run_query(conn, """
        SELECT COUNT(*) FROM companies c
        JOIN market_candles_cleaned m ON UPPER(TRIM(c.share_symbol)) = UPPER(TRIM(m.symbol))
    """)[0][0]
    print(f"Normalized matches: {norm_matches}")
