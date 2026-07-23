import os
import json
import logging
import httpx
import time
import config

logger = logging.getLogger(__name__)

class GeminiCaller:
    """
    A lightweight HTTP client to call the Gemini REST API directly.
    Replaces the heavy parallel.ai agent wrapper for simple classification tasks.
    """
    def __init__(self):
        self.api_key = getattr(config, "LLM_API_KEY", None) or os.environ.get("LLM_API_KEY")
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not set in configuration or environment variables.")
        
        self.model_name = getattr(config, "LLM_MODEL_NAME", "gemini-3.1-flash-lite-preview")
        # Ensure we construct the standard Google GenAI REST URL
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"

    def call(self, prompt: str, system_prompt: str = "") -> str:
        url = f"{self.base_url}?key={self.api_key}"
        
        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,  # Low temperature for deterministic JSON extraction
            }
        }
        
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }

        headers = {
            "Content-Type": "application/json"
        }

        max_retries = 10
        base_delay = 10.0

        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
                    response = client.post(url, headers=headers, json=payload)
                    response.raise_for_status()
                    data = response.json()
                
                # Extract the text from the Gemini response structure
                candidates = data.get("candidates", [])
                if not candidates:
                    logger.error(f"Gemini API returned no candidates: {data}")
                    raise ValueError("No candidates returned from Gemini API.")
                    
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if not parts:
                    logger.error(f"Gemini API returned empty parts: {data}")
                    raise ValueError("No text parts returned from Gemini API.")
                    
                return parts[0].get("text", "")
                
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code if e.response else 0
                if status_code in {429, 500, 502, 503, 504}:
                    if attempt < max_retries - 1:
                        # Try to parse exact delay from response body
                        delay = base_delay * (2 ** attempt)
                        if status_code == 429:
                            try:
                                err_data = e.response.json()
                                details = err_data.get("error", {}).get("details", [])
                                for detail in details:
                                    if "retryDelay" in detail:
                                        retry_delay_str = detail["retryDelay"]
                                        # "32s" -> 32.0
                                        if retry_delay_str.endswith('s'):
                                            delay = float(retry_delay_str[:-1]) + 1.0 # add 1s buffer
                                        break
                            except Exception:
                                pass
                            
                        logger.warning(f"Gemini API HTTP error ({status_code}). Retrying in {delay:.1f} seconds (Attempt {attempt + 1}/{max_retries})...")
                        time.sleep(delay)
                        continue
                logger.error(f"Gemini API HTTP request failed ({status_code}): {e}")
                logger.error(f"Response body: {e.response.text if e.response else ''}")
                raise
            except (httpx.TimeoutException, httpx.RequestError) as e:
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Gemini API request timed out or network error ({type(e).__name__}: {e}). Retrying in {delay} seconds (Attempt {attempt + 1}/{max_retries})...")
                    time.sleep(delay)
                    continue
                logger.error(f"Gemini API request failed after {max_retries} attempts: {e}")
                raise
            except Exception as e:
                logger.error(f"Failed to parse Gemini API response: {e}")
                raise
