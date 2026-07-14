"""Quick script to run the live coverage report for all horizons."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from database import SourceSessionLocal
from services.universe.universe_builder import UniverseBuilder

session = SourceSessionLocal()
builder = UniverseBuilder(session)

for horizon in ("SHORT", "MID", "LONG"):
    report = builder.generate_coverage_report(horizon)
    print(f"\n=== {horizon} ===")
    for k, v in report.items():
        if k != "excluded_counts_by_reason":
            print(f"  {k}: {v}")
    print(f"  excluded_counts_by_reason:")
    for reason, count in report["excluded_counts_by_reason"].items():
        print(f"    {reason}: {count}")

session.close()
