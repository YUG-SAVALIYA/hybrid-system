from sqlalchemy import Column, String, Float, Integer, BigInteger, JSON
from database import SourceBase

class Company(SourceBase):
    __tablename__ = "companies"

    id = Column(String, primary_key=True)
    share_symbol = Column(String)
    sectore = Column(String)
    industry = Column(String)
    categorized_industry = Column(String)
    created_at = Column(String)

class CompanyOverview(SourceBase):
    __tablename__ = "company_overviews"

    id = Column(String, primary_key=True)
    share_symbol = Column(String)
    market_cap = Column(Float)
    # Include other fields if needed, but not required yet

class CompanyProfitLoss(SourceBase):
    __tablename__ = "company_profit_losses"

    id = Column(String, primary_key=True)
    company_id = Column(String)
    period = Column(String)
    sales = Column(Float)
    operating_profit = Column(Float)
    net_profit = Column(Float)
    eps_in_rs = Column(Float)
    created_at = Column(String)

class CompanyBalanceSheet(SourceBase):
    __tablename__ = "company_balance_sheets"

    id = Column(String, primary_key=True)
    company_id = Column(String)
    period = Column(String)
    equity_capital = Column(Float)
    reserves = Column(Float)
    borrowings = Column(Float)
    total_assets = Column(Float)
    total_liabilities = Column(Float)
    created_at = Column(String)

class CompanyCashFlow(SourceBase):
    __tablename__ = "company_cash_flows"

    id = Column(String, primary_key=True)
    company_id = Column(String)
    period = Column(String)
    cash_from_operating_activity = Column(Float)
    net_cash_flow = Column(Float)
    created_at = Column(String)

class MarketCandleCleaned(SourceBase):
    __tablename__ = "market_candles_cleaned"

    id = Column(Integer, primary_key=True)
    symbol = Column(String)
    datetime = Column(String)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(BigInteger)
    first_seen_at = Column(BigInteger)
