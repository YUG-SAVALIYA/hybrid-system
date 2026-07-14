import os
from sqlalchemy import create_engine, inspect

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/trade_signal"
engine = create_engine(DATABASE_URL)
inspector = inspect(engine)

tables_to_check = [
    "companies",
    "company_overviews",
    "company_profit_losses",
    "company_balance_sheets",
    "company_cash_flows"
]

print("Available tables in database:")
all_tables = inspector.get_table_names()
print(all_tables)

print("\n--- SCHEMA INSPECTION ---\n")

for table in tables_to_check + [t for t in all_tables if "candle" in t.lower()]:
    if table in all_tables:
        print(f"TABLE: {table}")
        pk = inspector.get_pk_constraint(table)
        print(f"  Primary Key: {pk['constrained_columns']}")
        for column in inspector.get_columns(table):
            print(f"  - {column['name']}: {column['type']}")
        print()
    else:
        print(f"Table {table} not found in database.\n")
