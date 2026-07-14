from sqlalchemy.orm import Session
from sqlalchemy import text

class CadenceAuditor:
    def __init__(self, session: Session):
        self.session = session

    def audit_company(self, company_id: str) -> str:
        """
        Check:
        - Maximum number of financial records per company per calendar year
        - Whether companies have multiple different period months within the same year
        - Whether each company generally reports using the same recurring fiscal month
        """
        query = text("""
            WITH parsed AS (
                SELECT 
                    period,
                    RIGHT(period, 4) as year,
                    LEFT(period, 3) as month
                FROM company_profit_losses
                WHERE company_id = :cid
            ),
            yearly_counts AS (
                SELECT year, COUNT(*) as c, COUNT(DISTINCT month) as dist_months
                FROM parsed
                GROUP BY year
            )
            SELECT 
                MAX(c) as max_per_year,
                MAX(dist_months) as max_months_per_year
            FROM yearly_counts
        """)
        
        res = self.session.execute(query, {"cid": company_id}).fetchone()
        if not res or res.max_per_year is None:
            return "NO_DATA"
            
        max_per_year = res.max_per_year
        max_months_per_year = res.max_months_per_year
        
        # Check overall distinct months used across the entire history
        dist_months_total = self.session.execute(text("""
            SELECT COUNT(DISTINCT LEFT(period, 3)) 
            FROM company_profit_losses 
            WHERE company_id = :cid
        """), {"cid": company_id}).scalar()
        
        # If a company has multiple financial records in the same year using different months, 
        # do not automatically classify those records as annual.
        if max_per_year > 1 or max_months_per_year > 1 or dist_months_total > 1:
            return "AMBIGUOUS_PERIOD_CADENCE"
            
        return "ANNUAL_CONFIRMED"
