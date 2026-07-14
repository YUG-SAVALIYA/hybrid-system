import pytest
import uuid
import datetime
from sqlalchemy import text, insert
from database import source_engine, discovery_engine, SourceSessionLocal, DiscoverySessionLocal
from models.source import Company
from models.discovery import DiscoveryRun, CompanyTechnicalMetric
from repositories.source_repo import SourceRepository
from repositories.discovery_repo import DiscoveryRepository

def test_databases_connect_independently():
    # Both engines should connect
    with source_engine.connect() as conn:
        res = conn.execute(text("SELECT 1")).scalar()
        assert res == 1

    with discovery_engine.connect() as conn:
        res = conn.execute(text("SELECT 1")).scalar()
        assert res == 1

def test_source_repo_can_read():
    session = SourceSessionLocal()
    repo = SourceRepository(session)
    
    # Try fetching companies, shouldn't raise exception
    companies = repo.get_all_companies()
    assert isinstance(companies, list)
    session.close()

def test_source_repo_cannot_write():
    session = SourceSessionLocal()
    
    with pytest.raises(Exception, match="Modification queries are prohibited"):
        session.execute(text("INSERT INTO companies (id, share_symbol) VALUES ('test1', 'TEST')"))
        
    with pytest.raises(Exception, match="Modification queries are prohibited"):
        session.execute(text("DELETE FROM companies WHERE id = 'test1'"))
    
    with pytest.raises(Exception, match="Modification queries are prohibited"):
        session.execute(text("UPDATE companies SET share_symbol = 'TEST2' WHERE id = 'test1'"))
    
    session.rollback()
    session.close()

def test_discovery_records_can_be_inserted():
    session = DiscoverySessionLocal()
    repo = DiscoveryRepository(session)
    
    run_id = str(uuid.uuid4())
    run = DiscoveryRun(
        id=run_id,
        run_date="2024-03-01",
        horizon="Short",
        status="PENDING",
        source_data_as_of="2024-03-01"
    )
    repo.save_run(run)
    
    fetched = repo.get_run(run_id)
    assert fetched is not None
    assert fetched.id == run_id
    assert fetched.status == "PENDING"
    
    # Insert metric
    metric_id = str(uuid.uuid4())
    metric = CompanyTechnicalMetric(
        id=metric_id,
        run_id=run_id,
        source_company_id="test_comp_id",
        symbol="RELIANCE",
        company_return=1.5,
    )
    repo.save_technical_metrics([metric])
    
    # Clean up (write operations are allowed on discovery)
    session.execute(text(f"DELETE FROM company_technical_metrics WHERE id = '{metric_id}'"))
    session.execute(text(f"DELETE FROM discovery_runs WHERE id = '{run_id}'"))
    session.commit()
    session.close()

def test_no_raw_tables_in_discovery():
    # Verify discovery DB doesn't have companies or ta_candles table
    from sqlalchemy import inspect
    inspector = inspect(discovery_engine)
    tables = inspector.get_table_names()
    
    assert "companies" not in tables
    assert "company_overviews" not in tables
    assert "market_candles_cleaned" not in tables
    
    # Verify discovery DB has its own tables
    assert "discovery_runs" in tables
    assert "company_technical_metrics" in tables
