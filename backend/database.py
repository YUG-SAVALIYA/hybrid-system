from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.engine import Engine
import os
import sys

from config import SOURCE_DATABASE_URL, DISCOVERY_DATABASE_URL

if not SOURCE_DATABASE_URL:
    raise ValueError("SOURCE_DATABASE_URL is not set")
if not DISCOVERY_DATABASE_URL:
    raise ValueError("DISCOVERY_DATABASE_URL is not set")

# 1. Create source engine (Read-Only)
source_engine = create_engine(SOURCE_DATABASE_URL)

# Prevent accidental writes on source DB connection
@event.listens_for(source_engine, "before_cursor_execute")
def receive_before_cursor_execute(
    conn, cursor, statement, parameters, context, executemany
):
    stmt = statement.strip().upper()
    if stmt.startswith(("INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER")):
        raise Exception(f"Modification queries are prohibited on the source database: {stmt}")

# 2. Create discovery engine (Write allowed)
discovery_engine = create_engine(DISCOVERY_DATABASE_URL)

# 3. Session factories
SourceSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=source_engine)
DiscoverySessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=discovery_engine)

SourceBase = declarative_base()
DiscoveryBase = declarative_base()
