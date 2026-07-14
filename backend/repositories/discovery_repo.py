from sqlalchemy.orm import Session
from models.discovery import (
    DiscoveryRun,
    CompanyTechnicalMetric,
    CompanyFundamentalMetric,
    GroupScore
)

class DiscoveryRepository:
    def __init__(self, session: Session):
        self.session = session

    def save_run(self, run: DiscoveryRun):
        self.session.add(run)
        self.session.commit()
        return run
    
    def get_run(self, run_id: str):
        return self.session.query(DiscoveryRun).filter(DiscoveryRun.id == run_id).first()

    def save_technical_metrics(self, metrics: list[CompanyTechnicalMetric]):
        self.session.add_all(metrics)
        self.session.commit()

    def save_fundamental_metrics(self, metrics: list[CompanyFundamentalMetric]):
        self.session.add_all(metrics)
        self.session.commit()

    def save_group_scores(self, scores: list[GroupScore]):
        self.session.add_all(scores)
        self.session.commit()
