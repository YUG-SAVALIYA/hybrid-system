import os
from sqlalchemy import create_engine, text

DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/trade_signal"
engine = create_engine(DATABASE_URL)

with engine.connect() as conn:
    print("Profit and Loss periods:")
    res = conn.execute(text("SELECT period FROM company_profit_losses LIMIT 5"))
    for row in res:
        print(row)
