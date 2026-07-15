from sqlalchemy import create_engine, text
from config import DISCOVERY_DATABASE_URL

engine = create_engine(DISCOVERY_DATABASE_URL)
with engine.connect() as conn:
    conn.execute(text("UPDATE discovery_runs SET preparation_status = 'PENDING', status = 'PENDING', stage_results = '{}'::jsonb WHERE id = 'run-c36b8201f92c46c39ca23281325ba56d'"))
    conn.commit()
    print("Run reset successfully and stage_results cleared.")
