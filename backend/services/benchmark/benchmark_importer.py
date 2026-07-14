import csv
import json
import uuid
import logging
from datetime import datetime
from sqlalchemy.orm import Session
from models.discovery import BenchmarkCandle
from database import DiscoverySessionLocal

logger = logging.getLogger(__name__)

class BenchmarkImporter:
    def __init__(self, session: Session):
        self.session = session

    def import_from_csv(self, file_path: str, benchmark_code: str, benchmark_name: str, source_name: str) -> dict:
        batch_id = str(uuid.uuid4())
        results = {"inserted": 0, "updated": 0, "skipped": 0, "invalid": 0}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                
                # Check required headers
                headers = [h.strip().lower() for h in (reader.fieldnames or [])]
                if "date" not in headers or "close" not in headers:
                    raise ValueError("CSV must contain 'date' and 'close' columns")

                # Map original headers to lowercase for easy access
                row_dicts = []
                for row in reader:
                    mapped = {k.strip().lower(): v for k, v in row.items() if k}
                    row_dicts.append(mapped)

                self._process_rows(row_dicts, benchmark_code, benchmark_name, source_name, batch_id, results)

        except Exception as e:
            logger.error(f"Failed to import CSV: {e}")
            raise

        return results

    def import_from_json(self, file_path: str, benchmark_code: str, benchmark_name: str, source_name: str) -> dict:
        batch_id = str(uuid.uuid4())
        results = {"inserted": 0, "updated": 0, "skipped": 0, "invalid": 0}

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
            if not isinstance(data, list):
                raise ValueError("JSON must be a list of objects")

            # Lowercase keys
            row_dicts = [{k.lower(): v for k, v in row.items()} for row in data]
            self._process_rows(row_dicts, benchmark_code, benchmark_name, source_name, batch_id, results)

        except Exception as e:
            logger.error(f"Failed to import JSON: {e}")
            raise

        return results

    def _process_rows(self, rows: list[dict], benchmark_code: str, benchmark_name: str, source_name: str, batch_id: str, results: dict):
        processed_dates = set()

        for row in rows:
            try:
                date_str = row.get("date", "").strip()
                close_str = row.get("close")
                
                if not date_str or close_str is None or str(close_str).strip() == "":
                    results["invalid"] += 1
                    continue

                # Parse date safely (supports YYYY-MM-DD or standard formats)
                try:
                    # Attempt ISO format first
                    trade_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except ValueError:
                    # Fallback or other formats could be added here
                    results["invalid"] += 1
                    continue
                
                if trade_date in processed_dates:
                    results["skipped"] += 1
                    continue
                
                close_val = float(str(close_str).replace(",", ""))
                if close_val <= 0:
                    results["invalid"] += 1
                    continue

                # Parse optional fields
                open_val = float(str(row.get("open", close_val)).replace(",", "")) if row.get("open") else close_val
                high_val = float(str(row.get("high", close_val)).replace(",", "")) if row.get("high") else close_val
                low_val = float(str(row.get("low", close_val)).replace(",", "")) if row.get("low") else close_val
                
                vol_str = row.get("volume")
                volume_val = int(float(str(vol_str).replace(",", ""))) if vol_str and str(vol_str).strip() else None

                processed_dates.add(trade_date)

                # Upsert logic
                existing = self.session.query(BenchmarkCandle).filter_by(
                    benchmark_code=benchmark_code,
                    trade_date=trade_date
                ).first()

                if existing:
                    existing.open = open_val
                    existing.high = high_val
                    existing.low = low_val
                    existing.close = close_val
                    existing.volume = volume_val
                    existing.source_name = source_name
                    existing.import_batch_id = batch_id
                    results["updated"] += 1
                else:
                    new_candle = BenchmarkCandle(
                        id=str(uuid.uuid4()),
                        benchmark_code=benchmark_code,
                        benchmark_name=benchmark_name,
                        trade_date=trade_date,
                        open=open_val,
                        high=high_val,
                        low=low_val,
                        close=close_val,
                        volume=volume_val,
                        source_name=source_name,
                        import_batch_id=batch_id
                    )
                    self.session.add(new_candle)
                    results["inserted"] += 1

            except Exception as e:
                logger.warning(f"Invalid row skipped: {row} - {e}")
                results["invalid"] += 1

        self.session.commit()
