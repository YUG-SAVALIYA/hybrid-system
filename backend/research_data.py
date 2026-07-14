import os
import json
from sqlalchemy import create_engine, text
from collections import defaultdict
import datetime

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/trade_signal"
engine = create_engine(DATABASE_URL)

def run_query(conn, query, params=None):
    return conn.execute(text(query), params or {}).fetchall()

with engine.connect() as conn:
    print("=== 1. Financial Period Audit ===")
    for table in ["company_profit_losses", "company_balance_sheets", "company_cash_flows"]:
        print(f"--- {table} ---")
        periods = run_query(conn, f"SELECT period, COUNT(*) FROM {table} GROUP BY period ORDER BY COUNT(*) DESC LIMIT 10")
        print(f"Top periods: {periods}")
        min_max = run_query(conn, f"SELECT MIN(period), MAX(period) FROM {table}")
        print(f"Min/Max period: {min_max}")
        duplicates = run_query(conn, f"SELECT company_id, period, COUNT(*) FROM {table} GROUP BY company_id, period HAVING COUNT(*) > 1 LIMIT 5")
        print(f"Duplicates (sample): {duplicates}")
        records_per_company = run_query(conn, f"SELECT AVG(count) FROM (SELECT company_id, COUNT(*) as count FROM {table} GROUP BY company_id) as sq")
        print(f"Avg records per company: {records_per_company}")
        quarter_like = run_query(conn, f"SELECT period FROM {table} WHERE period ILIKE '%Q%' OR period ILIKE '%Mar%' OR period ILIKE '%Jun%' OR period ILIKE '%Sep%' OR period ILIKE '%Dec%' LIMIT 5")
        print(f"Quarter-like/Month samples: {quarter_like}")

    print("\n=== 3. Field Types and Samples ===")
    pnl_samples = run_query(conn, "SELECT sales, operating_profit, net_profit FROM company_profit_losses WHERE sales IS NOT NULL LIMIT 5")
    print(f"P&L Samples: {pnl_samples}")
    bs_samples = run_query(conn, "SELECT equity_capital, reserves, borrowings, total_assets, total_liabilities FROM company_balance_sheets WHERE equity_capital IS NOT NULL LIMIT 5")
    print(f"BS Samples: {bs_samples}")
    cf_samples = run_query(conn, "SELECT cash_from_operating_activity, net_cash_flow FROM company_cash_flows WHERE cash_from_operating_activity IS NOT NULL LIMIT 5")
    print(f"CF Samples: {cf_samples}")

    print("\n=== 4. Joins and Coverage ===")
    total_companies = run_query(conn, "SELECT COUNT(*) FROM companies")[0][0]
    print(f"Total active companies: {total_companies}")
    
    pnl_companies = run_query(conn, "SELECT COUNT(DISTINCT company_id) FROM company_profit_losses")[0][0]
    bs_companies = run_query(conn, "SELECT COUNT(DISTINCT company_id) FROM company_balance_sheets")[0][0]
    cf_companies = run_query(conn, "SELECT COUNT(DISTINCT company_id) FROM company_cash_flows")[0][0]
    all_three = run_query(conn, """
        SELECT COUNT(DISTINCT c.id) 
        FROM companies c
        JOIN company_profit_losses p ON c.id = p.company_id
        JOIN company_balance_sheets b ON c.id = b.company_id
        JOIN company_cash_flows f ON c.id = f.company_id
    """)[0][0]
    print(f"P&L: {pnl_companies}, BS: {bs_companies}, CF: {cf_companies}, All Three: {all_three}")

    missing_sector = run_query(conn, "SELECT COUNT(*) FROM companies WHERE sectore IS NULL OR sectore = ''")[0][0]
    missing_industry = run_query(conn, "SELECT COUNT(*) FROM companies WHERE industry IS NULL OR industry = ''")[0][0]
    missing_basic = run_query(conn, "SELECT COUNT(*) FROM companies WHERE categorized_industry IS NULL OR categorized_industry = ''")[0][0]
    print(f"Missing - Sector: {missing_sector}, Industry: {missing_industry}, Basic: {missing_basic}")

    duplicate_symbols = run_query(conn, "SELECT share_symbol, COUNT(*) FROM companies GROUP BY share_symbol HAVING COUNT(*) > 1")
    print(f"Duplicate symbols: {duplicate_symbols}")

    companies_no_candles = run_query(conn, "SELECT COUNT(*) FROM companies c LEFT JOIN market_candles_cleaned m ON c.share_symbol = m.symbol WHERE m.id IS NULL")[0][0]
    candles_no_companies = run_query(conn, "SELECT COUNT(DISTINCT m.symbol) FROM market_candles_cleaned m LEFT JOIN companies c ON m.symbol = c.share_symbol WHERE c.id IS NULL")[0][0]
    print(f"Companies without candles: {companies_no_candles}, Candle symbols without companies: {candles_no_companies}")

    print("\n=== 5. NIFTY 500 Benchmark ===")
    benchmark_candidates = run_query(conn, "SELECT DISTINCT symbol FROM market_candles_cleaned WHERE symbol ILIKE '%nifty%' OR symbol ILIKE '%500%'")
    print(f"Benchmark candidates: {benchmark_candidates}")
    
    # Try NIFTY 500 specifically
    nifty_stats = run_query(conn, "SELECT MIN(datetime), MAX(datetime), COUNT(*), SUM(CASE WHEN volume IS NULL OR volume = 0 THEN 1 ELSE 0 END) FROM market_candles_cleaned WHERE symbol = 'NIFTY 500' OR symbol = 'NIFTY500'")
    print(f"NIFTY 500 stats (min_date, max_date, count, zero_vol_count): {nifty_stats}")
    
    nifty_dups = run_query(conn, "SELECT datetime, COUNT(*) FROM market_candles_cleaned WHERE symbol = 'NIFTY 500' OR symbol = 'NIFTY500' GROUP BY datetime HAVING COUNT(*) > 1 LIMIT 5")
    print(f"NIFTY 500 duplicate dates: {nifty_dups}")

    print("\n=== 6. Financial Businesses ===")
    sectors = run_query(conn, "SELECT DISTINCT sectore FROM companies WHERE sectore IS NOT NULL")
    print(f"Distinct Sectors: {sectors}")
    industries = run_query(conn, "SELECT DISTINCT industry FROM companies WHERE industry ILIKE '%bank%' OR industry ILIKE '%financ%' OR industry ILIKE '%insur%'")
    print(f"Financial-like Industries: {industries}")
    basic_industries = run_query(conn, "SELECT DISTINCT categorized_industry FROM companies WHERE categorized_industry ILIKE '%bank%' OR categorized_industry ILIKE '%financ%' OR categorized_industry ILIKE '%insur%'")
    print(f"Financial-like Basic Industries: {basic_industries}")
