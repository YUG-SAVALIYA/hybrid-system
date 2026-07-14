import os
from dotenv import load_dotenv

load_dotenv()

SOURCE_DATABASE_URL = os.environ.get("SOURCE_DATABASE_URL")
DISCOVERY_DATABASE_URL = os.environ.get("DISCOVERY_DATABASE_URL")

# Horizon Constants (trading days)
HORIZON_SHORT_DAYS = int(os.environ.get("HORIZON_SHORT_DAYS", 20))
HORIZON_MID_DAYS   = int(os.environ.get("HORIZON_MID_DAYS", 63))
HORIZON_LONG_DAYS  = int(os.environ.get("HORIZON_LONG_DAYS", 252))

# Minimum candle counts for benchmark readiness (horizon + 1 for return calculation)
BENCHMARK_MIN_SHORT = HORIZON_SHORT_DAYS + 1   # 21
BENCHMARK_MIN_MID   = HORIZON_MID_DAYS   + 1   # 64
BENCHMARK_MIN_LONG  = HORIZON_LONG_DAYS  + 1   # 253

# Minimum percentage of valid volume sessions required in a benchmark window
MIN_VOLUME_WINDOW_COVERAGE = 80.0

# Maximum trading-session lag allowed before a company candle is treated as unavailable
MAX_COMPANY_CANDLE_STALENESS_SESSIONS = int(
    os.environ.get("MAX_COMPANY_CANDLE_STALENESS_SESSIONS", 3)
)

# Consistency block structure
CONSISTENCY_BLOCKS = {
    "SHORT": {"num_blocks": 4, "sessions_per_block": 5},
    "MID": {"num_blocks": 3, "sessions_per_block": 21},
    "LONG": {"num_blocks": 4, "sessions_per_block": 63},
}


# Volume window: current horizon + preceding equal-length window
VOLUME_COMPARISON_MULTIPLIER = int(os.environ.get("VOLUME_COMPARISON_MULTIPLIER", 2))

# Sector Technical Score Weights
TECHNICAL_SCORE_WEIGHTS = {
    "return": 25.0,
    "breadth": 25.0,
    "volume": 25.0,
    "consistency": 25.0
}
MIN_GROUP_TECHNICAL_COVERAGE = 75.0

MIN_INDUSTRY_COMPANIES = 3
MIN_BASIC_INDUSTRY_COMPANIES = 2

# Minimum candle history per horizon for universe eligibility
# (2x horizon for volume window coverage)
UNIVERSE_MIN_CANDLES_SHORT = HORIZON_SHORT_DAYS * VOLUME_COMPARISON_MULTIPLIER   # 40
UNIVERSE_MIN_CANDLES_MID   = HORIZON_MID_DAYS   * VOLUME_COMPARISON_MULTIPLIER   # 126
UNIVERSE_MIN_CANDLES_LONG  = HORIZON_LONG_DAYS  * VOLUME_COMPARISON_MULTIPLIER   # 504

# Primary benchmark identifiers
PRIMARY_TECHNICAL_BENCHMARK      = os.environ.get("PRIMARY_TECHNICAL_BENCHMARK", "NIFTY_500")
PRIMARY_TECHNICAL_BENCHMARK_NAME = os.environ.get("PRIMARY_TECHNICAL_BENCHMARK_NAME", "NIFTY 500")

# Minimum group sizes for sector / industry / basic-industry scoring
MIN_SECTOR_COMPANIES        = int(os.environ.get("MIN_SECTOR_COMPANIES", 5))
MIN_INDUSTRY_COMPANIES      = int(os.environ.get("MIN_INDUSTRY_COMPANIES", 3))
MIN_BASIC_INDUSTRY_COMPANIES = int(os.environ.get("MIN_BASIC_INDUSTRY_COMPANIES", 2))

# Final Component Weights
WEIGHT_TECHNICAL   = float(os.environ.get("WEIGHT_TECHNICAL", 0.40))
WEIGHT_FUNDAMENTAL = float(os.environ.get("WEIGHT_FUNDAMENTAL", 0.40))
WEIGHT_MACRO       = float(os.environ.get("WEIGHT_MACRO", 0.20))

# Symbol normalizer – exchange suffixes to strip when configured
SYMBOL_SUFFIXES_TO_STRIP = [
    s for s in os.environ.get("SYMBOL_SUFFIXES_TO_STRIP", "").split(",") if s
]

# Parallel Search API (macro data)
PARALLEL_API_KEY = os.environ.get("PARALLEL_API_KEY", "")   # never hardcoded
PARALLEL_SEARCH_MODE = os.environ.get("PARALLEL_SEARCH_MODE", "advanced")
PARALLEL_MACRO_MAX_CHARS = int(os.environ.get("PARALLEL_MACRO_MAX_CHARS", 50000))

# Fundamental group thresholds
MIN_GROUP_FUNDAMENTAL_COVERAGE = float(os.environ.get("MIN_GROUP_FUNDAMENTAL_COVERAGE", 75.0))
MIN_SECTOR_FUNDAMENTAL_COMPANIES = int(os.environ.get("MIN_SECTOR_FUNDAMENTAL_COMPANIES", 5))
MIN_INDUSTRY_FUNDAMENTAL_COMPANIES = int(os.environ.get("MIN_INDUSTRY_FUNDAMENTAL_COMPANIES", 3))
MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES = int(os.environ.get("MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES", 2))
MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS = int(os.environ.get("MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS", 3))
MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE = float(os.environ.get("MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE", 60.0))

# LLM / Macro filter summary
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "gemini-2.0-flash")
MACRO_PROMPT_VERSION = os.environ.get("MACRO_PROMPT_VERSION", "1.0")
MACRO_LOW_SOURCE_COVERAGE_THRESHOLD = float(os.environ.get("MACRO_LOW_SOURCE_COVERAGE_THRESHOLD", 50.0))
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")  # never hardcoded

# Sector macro scoring
MIN_SECTOR_MACRO_COVERAGE = float(os.environ.get("MIN_SECTOR_MACRO_COVERAGE", 75.0))
MIN_INDUSTRY_MACRO_COVERAGE = float(os.environ.get("MIN_INDUSTRY_MACRO_COVERAGE", 75.0))
MIN_BASIC_INDUSTRY_MACRO_COVERAGE = float(os.environ.get("MIN_BASIC_INDUSTRY_MACRO_COVERAGE", 75.0))

# Final sector discovery ranking
MIN_SECTOR_DISCOVERY_COVERAGE = float(os.environ.get("MIN_SECTOR_DISCOVERY_COVERAGE", 80.0))
SECTOR_SELECTION_COUNT = int(os.environ.get("SECTOR_SELECTION_COUNT", 1))

# Final industry discovery ranking
MIN_INDUSTRY_DISCOVERY_COVERAGE = float(os.environ.get("MIN_INDUSTRY_DISCOVERY_COVERAGE", 80.0))
INDUSTRY_SELECTION_COUNT = int(os.environ.get("INDUSTRY_SELECTION_COUNT", 1))

# Final basic-industry discovery ranking
MIN_BASIC_INDUSTRY_DISCOVERY_COVERAGE = float(os.environ.get("MIN_BASIC_INDUSTRY_DISCOVERY_COVERAGE", 80.0))
BASIC_INDUSTRY_SELECTION_COUNT = int(os.environ.get("BASIC_INDUSTRY_SELECTION_COUNT", 1))

# Final stock candidate scoring
MIN_STOCK_SCORE_COVERAGE = float(os.environ.get("MIN_STOCK_SCORE_COVERAGE", 80.0))

# Final stock discovery ranking
STOCK_SELECTION_COUNT = int(os.environ.get("STOCK_SELECTION_COUNT", 5))
