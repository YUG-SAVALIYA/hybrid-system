from sqlalchemy.orm import Session
from sqlalchemy import text
from repositories.source_repo import SourceRepository

class CoverageReportService:
    def __init__(self, session: Session, benchmark_symbol: str = "NIFTY 500"):
        self.session = session
        self.repo = SourceRepository(session)
        self.benchmark_symbol = benchmark_symbol

    def run_report(self) -> dict:
        total_companies = self.session.execute(text("SELECT COUNT(*) FROM companies")).scalar()
        
        pnl_companies = self.session.execute(text("SELECT COUNT(DISTINCT company_id) FROM company_profit_losses")).scalar()
        bs_companies = self.session.execute(text("SELECT COUNT(DISTINCT company_id) FROM company_balance_sheets")).scalar()
        cf_companies = self.session.execute(text("SELECT COUNT(DISTINCT company_id) FROM company_cash_flows")).scalar()
        
        all_three = self.session.execute(text("""
            SELECT COUNT(DISTINCT c.id) 
            FROM companies c
            JOIN company_profit_losses p ON c.id = p.company_id
            JOIN company_balance_sheets b ON c.id = b.company_id
            JOIN company_cash_flows f ON c.id = f.company_id
        """)).scalar()

        missing_sector = self.session.execute(text("SELECT COUNT(*) FROM companies WHERE sectore IS NULL OR sectore = ''")).scalar()
        missing_industry = self.session.execute(text("SELECT COUNT(*) FROM companies WHERE industry IS NULL OR industry = ''")).scalar()
        missing_basic = self.session.execute(text("SELECT COUNT(*) FROM companies WHERE categorized_industry IS NULL OR categorized_industry = ''")).scalar()

        duplicate_symbols = self.session.execute(text("SELECT share_symbol, COUNT(*) FROM companies GROUP BY share_symbol HAVING COUNT(*) > 1")).fetchall()
        duplicate_symbols = [(row[0], row[1]) for row in duplicate_symbols]
        
        companies_no_candles = self.session.execute(text("SELECT COUNT(*) FROM companies c LEFT JOIN market_candles_cleaned m ON c.share_symbol = m.symbol WHERE m.id IS NULL")).scalar()
        candles_no_companies = self.session.execute(text("SELECT COUNT(DISTINCT m.symbol) FROM market_candles_cleaned m LEFT JOIN companies c ON m.symbol = c.share_symbol WHERE c.id IS NULL")).scalar()

        benchmark_stats = self.session.execute(text("SELECT MIN(datetime), MAX(datetime), COUNT(*) FROM market_candles_cleaned WHERE symbol = :symbol"), {"symbol": self.benchmark_symbol}).fetchone()
        
        if benchmark_stats and benchmark_stats[2] > 0:
            benchmark_status = "AVAILABLE"
            benchmark_min = benchmark_stats[0]
            benchmark_max = benchmark_stats[1]
            benchmark_count = benchmark_stats[2]
        else:
            benchmark_status = "BENCHMARK_DATA_UNAVAILABLE"
            benchmark_min = None
            benchmark_max = None
            benchmark_count = 0

        return {
            "total_companies": total_companies,
            "pnl_companies": pnl_companies,
            "bs_companies": bs_companies,
            "cf_companies": cf_companies,
            "all_three": all_three,
            "missing_sector": missing_sector,
            "missing_industry": missing_industry,
            "missing_basic": missing_basic,
            "duplicate_symbols": duplicate_symbols,
            "companies_no_candles": companies_no_candles,
            "candles_no_companies": candles_no_companies,
            "benchmark_status": benchmark_status,
            "benchmark_min_date": benchmark_min,
            "benchmark_max_date": benchmark_max,
            "benchmark_count": benchmark_count
        }
