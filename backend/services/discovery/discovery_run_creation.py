"""Creation service for discovery runs."""
from __future__ import annotations

import datetime
import uuid
from typing import Optional

from sqlalchemy import null
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.discovery import DiscoveryRun


E_RUN_ALREADY_EXISTS = "DISCOVERY_RUN_ALREADY_EXISTS"
E_INVALID_AS_OF_DATE = "INVALID_DISCOVERY_AS_OF_DATE"


class DiscoveryRunAlreadyExistsError(RuntimeError):
    error_code = E_RUN_ALREADY_EXISTS


class InvalidDiscoveryAsOfDateError(ValueError):
    error_code = E_INVALID_AS_OF_DATE


class DiscoveryRunCreationService:
    def __init__(self, discovery_session: Session):
        self._disc = discovery_session

    def create_run(
        self,
        run_id: Optional[str] = None,
        as_of_date: Optional[datetime.date] = None,
    ) -> DiscoveryRun:
        clean_run_id = run_id or self._generate_run_id()
        clean_as_of_date = as_of_date or datetime.datetime.now(datetime.timezone.utc).date()
        if clean_as_of_date > datetime.datetime.now(datetime.timezone.utc).date():
            raise InvalidDiscoveryAsOfDateError("Discovery as_of_date cannot be in the future.")

        try:
            if self._disc.query(DiscoveryRun).filter_by(id=clean_run_id).first() is not None:
                raise DiscoveryRunAlreadyExistsError("Discovery run already exists.")

            now = datetime.datetime.now(datetime.timezone.utc)
            row = DiscoveryRun(
                id=clean_run_id,
                run_date=clean_as_of_date.isoformat(),
                source_data_as_of=clean_as_of_date.isoformat(),
                status="PENDING",
                current_stage=None,
                last_completed_stage=None,
                started_at=null(),
                completed_at=None,
                stage_results={},
                warnings=[],
                error_code=None,
                error_message=None,
                resume_count=0,
                created_at=now,
                updated_at=now,
            )
            self._disc.add(row)
            self._disc.commit()
            self._disc.refresh(row)
            return row
        except DiscoveryRunAlreadyExistsError:
            self._disc.rollback()
            raise
        except IntegrityError as exc:
            self._disc.rollback()
            raise DiscoveryRunAlreadyExistsError("Discovery run already exists.") from exc
        except Exception:
            self._disc.rollback()
            raise

    def _generate_run_id(self) -> str:
        return f"run-{uuid.uuid4().hex}"
