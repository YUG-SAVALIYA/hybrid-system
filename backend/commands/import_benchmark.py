import argparse
import sys
import os

# Add backend dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from database import DiscoverySessionLocal
from services.benchmark.benchmark_importer import BenchmarkImporter

def main():
    parser = argparse.ArgumentParser(description="Import benchmark data from CSV or JSON.")
    parser.add_argument("--benchmark", required=True, help="Benchmark code (e.g., NIFTY500)")
    parser.add_argument("--name", default=None, help="Benchmark name (e.g., NIFTY 500)")
    parser.add_argument("--file", required=True, help="Path to the import file")
    parser.add_argument("--source", default="Manual CLI Import", help="Source name of the data")
    
    args = parser.parse_args()

    benchmark_name = args.name or args.benchmark.replace("_", " ")

    session = DiscoverySessionLocal()
    try:
        importer = BenchmarkImporter(session)
        
        if args.file.lower().endswith(".csv"):
            results = importer.import_from_csv(args.file, args.benchmark, benchmark_name, args.source)
        elif args.file.lower().endswith(".json"):
            results = importer.import_from_json(args.file, args.benchmark, benchmark_name, args.source)
        else:
            print("Error: File must be .csv or .json")
            sys.exit(1)
            
        print(f"Import complete for {args.benchmark}:")
        print(f"  Inserted: {results['inserted']}")
        print(f"  Updated:  {results['updated']}")
        print(f"  Skipped:  {results['skipped']}")
        print(f"  Invalid:  {results['invalid']}")

    finally:
        session.close()

if __name__ == "__main__":
    main()
