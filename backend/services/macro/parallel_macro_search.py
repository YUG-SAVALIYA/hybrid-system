"""
ParallelMacroSearchProvider

Fetches generated macro reports directly from the Parallel Search API.
Replaces the web search iteration with a single task_run that generates
a strict JSON object containing the four requested categories.
"""
from __future__ import annotations

import logging
import os
import time
import json
import re
import datetime
from sqlalchemy.orm import Session

from parallel import Parallel
import config
from services.macro.macro_search_batch import MacroSearchBatchService, PROVIDER_PARALLEL

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_BACKOFF_BASE = 2.0

W_TIMEOUT = "PARALLEL_REQUEST_TIMEOUT"
W_FAILED = "PARALLEL_TASK_FAILED"
W_INVALID_JSON = "PARALLEL_INVALID_JSON"

PROMPT = """You are a macroeconomic research assistant. Please provide a comprehensive macro report focusing on the Indian economy and its current state.
CRITICAL INSTRUCTION: Return a JSON object with exactly the following four keys (no other text, no markdown outside the JSON).
{
  "interest_rates": "details on interest rates, RBI policy, banking liquidity, credit conditions, and bond yields.",
  "commodity_input_costs": "details on commodity prices, crude oil, metals, agricultural commodities, and global supply chain conditions.",
  "government_policies": "details on Indian government fiscal policy, budget spending, sector subsidies, infrastructure investment, and regulatory changes.",
  "demand_conditions": "details on demand conditions, consumer sentiment, retail spending, corporate earnings trends, exports, and economic growth indicators."
}"""

class ParallelMacroSearchProvider:
    """
    Fetches macro evidence via the Parallel Search API using task_run.
    Bypasses standard web search snippet collection.
    """

    def __init__(self, discovery_session: Session):
        self._disc = discovery_session
        self._batch_svc = MacroSearchBatchService(discovery_session)

    def _get_client(self) -> Parallel:
        api_key = os.environ.get("PARALLEL_API_KEY")
        if not api_key:
            raise ValueError("PARALLEL_API_KEY environment variable is not set")
        return Parallel(api_key=api_key, timeout=600.0)

    def fetch_macro_data(self, run_id: str) -> dict:
        batch_id = f"macro-parallel-{run_id}"
        batch = self._batch_svc.get_or_create_batch(run_id, PROVIDER_PARALLEL, batch_id)
        
        # Reset batch
        batch.results = []
        batch.failed_categories = []
        batch.warnings = []
        batch.provider_metadata = {}
        batch.total_results = 0
        batch.status = "PENDING"
        self._disc.flush()

        try:
            client = self._get_client()
        except ValueError as exc:
            batch.status = "FAILED"
            batch.warnings = [f"Auth Error: {str(exc)}"]
            self._disc.commit()
            return {"status": "FAILED", "warnings": batch.warnings, "metadata": {"result_id": batch_id}}

        last_exc = None
        result_text = ""
        
        for attempt in range(MAX_RETRIES + 1):
            try:
                task_run = client.task_run.create(
                    input=PROMPT,
                    processor="pro-fast"
                )
                run_result = client.task_run.result(task_run.run_id, api_timeout=600)
                
                # Extract text depending on SDK version output type
                output_obj = getattr(run_result, "output", "")
                if hasattr(output_obj, "content") and isinstance(output_obj.content, dict):
                    result_text = output_obj.content.get("answer", "")
                elif hasattr(output_obj, "answer"):
                    result_text = output_obj.answer
                else:
                    result_text = str(output_obj)
                break
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_BASE ** (attempt + 1))
                else:
                    batch.status = "FAILED"
                    batch.warnings = [f"Task Failed: {str(exc)}"]
                    self._disc.commit()
                    return {"status": "FAILED", "warnings": batch.warnings, "metadata": {"result_id": batch_id}}

        # Attempt to parse output as JSON
        try:
            clean_text = result_text.strip()
            
            # Find the first { and the last }
            start_idx = clean_text.find('{')
            end_idx = clean_text.rfind('}')
            
            if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                clean_text = clean_text[start_idx:end_idx + 1]
                
            clean_text = clean_text.strip()
            
            try:
                parsed = json.loads(clean_text)
            except Exception as inner_e:
                # If it still fails, let's log the exact slice we tried to parse
                logger.error(f"Failed to parse sliced JSON. Length: {len(clean_text)}. Error: {inner_e}")
                raise
            
            # Save the JSON structure in batch.results
            # so downstream services can consume it
            batch.results = [parsed]
            batch.total_results = 1
            batch.status = "COMPLETED"
            self._disc.commit()
            
        except json.JSONDecodeError as exc:
            batch.status = "FAILED"
            batch.warnings = [f"Invalid JSON from Parallel: {str(exc)}", result_text]
            self._disc.commit()
            return {"status": "FAILED", "warnings": batch.warnings, "metadata": {"result_id": batch_id}}
            
        return {"status": "COMPLETED", "metadata": {"result_id": batch_id}}
