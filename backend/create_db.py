import sqlalchemy
from sqlalchemy import create_engine

engine = create_engine('postgresql://postgres:postgres@localhost:5432/postgres', isolation_level='AUTOCOMMIT')
try:
    with engine.connect() as conn:
        conn.execute(sqlalchemy.text('CREATE DATABASE discovery_db'))
        print("Database created")
except Exception as e:
    print("Database might already exist or error:", e)
