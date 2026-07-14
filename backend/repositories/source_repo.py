from sqlalchemy.orm import Session
from models.source import (
    Company,
    CompanyOverview,
    CompanyProfitLoss,
    CompanyBalanceSheet,
    CompanyCashFlow,
    MarketCandleCleaned
)

class SourceRepository:
    def __init__(self, session: Session):
        self.session = session

    def get_all_companies(self):
        return self.session.query(Company).all()

    def get_company_by_symbol(self, symbol: str):
        return self.session.query(Company).filter(Company.share_symbol == symbol).first()

    def get_company_overview(self, symbol: str):
        return self.session.query(CompanyOverview).filter(CompanyOverview.share_symbol == symbol).first()

    def get_profit_loss(self, company_id: str):
        return self.session.query(CompanyProfitLoss).filter(CompanyProfitLoss.company_id == company_id).all()

    def get_balance_sheet(self, company_id: str):
        return self.session.query(CompanyBalanceSheet).filter(CompanyBalanceSheet.company_id == company_id).all()

    def get_cash_flow(self, company_id: str):
        return self.session.query(CompanyCashFlow).filter(CompanyCashFlow.company_id == company_id).all()

    def get_candles(self, symbol: str):
        return self.session.query(MarketCandleCleaned).filter(MarketCandleCleaned.symbol == symbol).all()

    def get_nifty_500_candles(self):
        return self.get_candles("NIFTY 500")
