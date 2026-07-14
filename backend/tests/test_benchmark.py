"""
Tests for benchmark import, validation, and related safety rules.
"""
import os
import uuid
import csv
import json
import pytest
import tempfile
import datetime

from database import DiscoverySessionLocal
from services.benchmark.benchmark_importer import BenchmarkImporter
from services.benchmark.benchmark_validator import BenchmarkValidator
from models.discovery import BenchmarkCandle


BENCH_CODE = "TEST_BENCH"
BENCH_NAME = "Test Benchmark"


def _session():
    return DiscoverySessionLocal()


def _cleanup(session, code: str):
    from sqlalchemy import text
    session.execute(text("DELETE FROM benchmark_candles WHERE benchmark_code = :c"), {"c": code})
    session.commit()


def _write_csv(rows: list[dict], path: str):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)


def _write_json(rows: list[dict], path: str):
    with open(path, "w") as f:
        json.dump(rows, f)


# ------------------------------------------------------------------ #
#  CSV import                                                          #
# ------------------------------------------------------------------ #

def test_benchmark_csv_import_valid():
    session = _session()
    try:
        code = f"{BENCH_CODE}_CSV"
        rows = [
            {"date": "2024-01-02", "open": 100, "high": 105, "low": 99,  "close": 103, "volume": 1000},
            {"date": "2024-01-03", "open": 103, "high": 107, "low": 102, "close": 106, "volume": 1100},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        _write_csv(rows, path)

        importer = BenchmarkImporter(session)
        result = importer.import_from_csv(path, code, BENCH_NAME, "test")
        os.unlink(path)

        assert result["inserted"] == 2
        assert result["invalid"] == 0
    finally:
        _cleanup(session, f"{BENCH_CODE}_CSV")
        session.close()


def test_benchmark_json_import_valid():
    session = _session()
    try:
        code = f"{BENCH_CODE}_JSON"
        rows = [
            {"date": "2024-02-01", "close": 200.5},
            {"date": "2024-02-02", "close": 201.0},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        _write_json(rows, path)

        importer = BenchmarkImporter(session)
        result = importer.import_from_json(path, code, BENCH_NAME, "test")
        os.unlink(path)

        assert result["inserted"] == 2
        assert result["invalid"] == 0
    finally:
        _cleanup(session, f"{BENCH_CODE}_JSON")
        session.close()


def test_benchmark_import_rejects_negative_close():
    session = _session()
    try:
        code = f"{BENCH_CODE}_NEG"
        rows = [
            {"date": "2024-03-01", "close": -10},
            {"date": "2024-03-02", "close": 150},
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        _write_csv(rows, path)

        importer = BenchmarkImporter(session)
        result = importer.import_from_csv(path, code, BENCH_NAME, "test")
        os.unlink(path)

        assert result["invalid"] == 1
        assert result["inserted"] == 1
    finally:
        _cleanup(session, f"{BENCH_CODE}_NEG")
        session.close()


def test_benchmark_import_rejects_zero_close():
    session = _session()
    try:
        code = f"{BENCH_CODE}_ZERO"
        rows = [{"date": "2024-04-01", "close": 0}]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        _write_csv(rows, path)

        importer = BenchmarkImporter(session)
        result = importer.import_from_csv(path, code, BENCH_NAME, "test")
        os.unlink(path)
        assert result["invalid"] == 1
    finally:
        _cleanup(session, f"{BENCH_CODE}_ZERO")
        session.close()


def test_benchmark_import_deduplicates_dates_in_file():
    session = _session()
    try:
        code = f"{BENCH_CODE}_DUP"
        rows = [
            {"date": "2024-05-01", "close": 120},
            {"date": "2024-05-01", "close": 125},  # same date, should be skipped
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        _write_csv(rows, path)

        importer = BenchmarkImporter(session)
        result = importer.import_from_csv(path, code, BENCH_NAME, "test")
        os.unlink(path)

        assert result["inserted"] == 1
        assert result["skipped"] == 1
    finally:
        _cleanup(session, f"{BENCH_CODE}_DUP")
        session.close()


def test_benchmark_import_upserts_existing_date():
    session = _session()
    try:
        code = f"{BENCH_CODE}_UPS"
        row = {"date": "2024-06-01", "close": 300}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path1 = f.name
        _write_csv([row], path1)
        importer = BenchmarkImporter(session)
        r1 = importer.import_from_csv(path1, code, BENCH_NAME, "test")
        os.unlink(path1)
        assert r1["inserted"] == 1

        # Import same date with different close
        row["close"] = 310
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path2 = f.name
        _write_csv([row], path2)
        r2 = importer.import_from_csv(path2, code, BENCH_NAME, "test")
        os.unlink(path2)

        assert r2["updated"] == 1
        # Verify the close was updated
        rec = session.query(BenchmarkCandle).filter_by(benchmark_code=code).first()
        assert rec.close == 310
    finally:
        _cleanup(session, f"{BENCH_CODE}_UPS")
        session.close()


def test_benchmark_import_rejects_malformed_date():
    session = _session()
    try:
        code = f"{BENCH_CODE}_BADDATE"
        rows = [{"date": "01/01/2024", "close": 100}]   # wrong format
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
            path = f.name
        _write_csv(rows, path)
        importer = BenchmarkImporter(session)
        result = importer.import_from_csv(path, code, BENCH_NAME, "test")
        os.unlink(path)
        assert result["invalid"] == 1
    finally:
        _cleanup(session, f"{BENCH_CODE}_BADDATE")
        session.close()


# ------------------------------------------------------------------ #
#  Benchmark validator                                                 #
# ------------------------------------------------------------------ #

def test_benchmark_validator_missing_data():
    session = _session()
    try:
        validator = BenchmarkValidator(session, benchmark_code="NONEXISTENT_BENCH")
        report = validator.validate()
        assert report["status"] == "BENCHMARK_DATA_UNAVAILABLE"
        assert report["total_rows"] == 0
        assert report["20_day_availability"] is False
    finally:
        session.close()


def _insert_n_candles(session, code: str, n: int):
    batch = str(uuid.uuid4())
    start = datetime.date(2020, 1, 1)
    for i in range(n):
        d = datetime.date(2020, 1, 1)
        # advance working days simply by offset
        d = datetime.date.fromordinal(start.toordinal() + i)
        candle = BenchmarkCandle(
            id=str(uuid.uuid4()),
            benchmark_code=code,
            benchmark_name="TEST",
            trade_date=d,
            open=100.0, high=101.0, low=99.0, close=100.5,
            source_name="test",
            import_batch_id=batch
        )
        session.add(candle)
    session.commit()


def test_benchmark_validator_insufficient_history():
    session = _session()
    code = f"{BENCH_CODE}_VAL_INSUF"
    try:
        _insert_n_candles(session, code, 10)  # only 10 rows – not enough for any horizon
        validator = BenchmarkValidator(session, benchmark_code=code)
        report = validator.validate()
        assert report["total_rows"] == 10
        assert report["20_day_availability"] is False
        assert report["status"] == "INSUFFICIENT_HISTORY"
    finally:
        _cleanup(session, code)
        session.close()


def test_benchmark_validator_short_only():
    session = _session()
    code = f"{BENCH_CODE}_VAL_SHORT"
    try:
        _insert_n_candles(session, code, 30)  # >=21 but <64
        validator = BenchmarkValidator(session, benchmark_code=code)
        report = validator.validate()
        assert report["20_day_availability"] is True
        assert report["63_day_availability"] is False
    finally:
        _cleanup(session, code)
        session.close()


def test_benchmark_validator_all_horizons_ready():
    session = _session()
    code = f"{BENCH_CODE}_VAL_ALL"
    try:
        _insert_n_candles(session, code, 300)  # >=253
        validator = BenchmarkValidator(session, benchmark_code=code)
        report = validator.validate()
        assert report["20_day_availability"]  is True
        assert report["63_day_availability"]  is True
        assert report["252_day_availability"] is True
        assert report["status"] == "READY"
    finally:
        _cleanup(session, code)
        session.close()


def test_benchmark_importer_never_writes_to_source():
    """Importer should not touch the source DB at all."""
    from database import source_engine
    from sqlalchemy import inspect
    inspector = inspect(source_engine)
    assert "benchmark_candles" not in inspector.get_table_names()
