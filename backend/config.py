import os
from dotenv import load_dotenv

load_dotenv()

SOURCE_DATABASE_URL = os.environ.get("SOURCE_DATABASE_URL")
DISCOVERY_DATABASE_URL = os.environ.get("DISCOVERY_DATABASE_URL")

PARALLEL_API_KEY = os.environ.get("PARALLEL_API_KEY")

# Horizon Constants (trading days)
HORIZON_SHORT_DAYS = int(os.environ.get("HORIZON_SHORT_DAYS", 1))
HORIZON_MID_DAYS   = int(os.environ.get("HORIZON_MID_DAYS", 5))
HORIZON_LONG_DAYS  = int(os.environ.get("HORIZON_LONG_DAYS", 21))

# Minimum candle counts for benchmark readiness (set to safe defaults to allow long continuity lookbacks)
BENCHMARK_MIN_SHORT = 21   # Covers up to 1 month lookback for short
BENCHMARK_MIN_MID   = 64   # Covers up to 3 months lookback for mid
BENCHMARK_MIN_LONG  = 253  # Covers up to 1 year lookback for long

# Minimum percentage of valid volume sessions required in a benchmark window
MIN_VOLUME_WINDOW_COVERAGE = 80.0

# Maximum trading-session lag allowed before a company candle is treated as unavailable
MAX_COMPANY_CANDLE_STALENESS_SESSIONS = int(
    os.environ.get("MAX_COMPANY_CANDLE_STALENESS_SESSIONS", 3)
)

# Consistency block structure
CONSISTENCY_BLOCKS = {
    "SHORT": {"num_blocks": 5, "sessions_per_block": 1},   # check past 5 days
    "MID": {"num_blocks": 4, "sessions_per_block": 5},     # check past 4 weeks
    "LONG": {"num_blocks": 6, "sessions_per_block": 21},   # check past 6 months
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
MIN_COMPANY_TECHNICAL_COVERAGE = float(os.environ.get("MIN_COMPANY_TECHNICAL_COVERAGE", 0.0))
MIN_GROUP_TECHNICAL_COVERAGE = 0.0

MIN_INDUSTRY_COMPANIES = 1
MIN_BASIC_INDUSTRY_COMPANIES = 1

# Minimum candle history per horizon for universe eligibility (must cover volume and consistency windows)
UNIVERSE_MIN_CANDLES_SHORT = 21
UNIVERSE_MIN_CANDLES_MID   = 64
UNIVERSE_MIN_CANDLES_LONG  = 253

# Primary benchmark identifiers
PRIMARY_TECHNICAL_BENCHMARK      = os.environ.get("PRIMARY_TECHNICAL_BENCHMARK", "NIFTY500")
PRIMARY_TECHNICAL_BENCHMARK_NAME = os.environ.get("PRIMARY_TECHNICAL_BENCHMARK_NAME", "NIFTY 500")

# Minimum group sizes for sector / industry / basic-industry scoring
MIN_SECTOR_COMPANIES        = int(os.environ.get("MIN_SECTOR_COMPANIES", 1))
MIN_INDUSTRY_COMPANIES      = int(os.environ.get("MIN_INDUSTRY_COMPANIES", 1))
MIN_BASIC_INDUSTRY_COMPANIES = int(os.environ.get("MIN_BASIC_INDUSTRY_COMPANIES", 1))

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
MIN_COMPANY_FUNDAMENTAL_COVERAGE = float(os.environ.get("MIN_COMPANY_FUNDAMENTAL_COVERAGE", 0.0))
MIN_GROUP_FUNDAMENTAL_COVERAGE = float(os.environ.get("MIN_GROUP_FUNDAMENTAL_COVERAGE", 0.0))
MIN_SECTOR_FUNDAMENTAL_COMPANIES = int(os.environ.get("MIN_SECTOR_FUNDAMENTAL_COMPANIES", 1))
MIN_INDUSTRY_FUNDAMENTAL_COMPANIES = int(os.environ.get("MIN_INDUSTRY_FUNDAMENTAL_COMPANIES", 1))
MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES = int(os.environ.get("MIN_BASIC_INDUSTRY_FUNDAMENTAL_COMPANIES", 1))
MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS = int(os.environ.get("MIN_GROUP_FUNDAMENTAL_METRIC_OBSERVATIONS", 1))
MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE = float(os.environ.get("MIN_GROUP_FUNDAMENTAL_METRIC_COVERAGE", 0.0))

# LLM / Macro filter summary
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME")
MACRO_PROMPT_VERSION = os.environ.get("MACRO_PROMPT_VERSION", "1.0")
MACRO_LOW_SOURCE_COVERAGE_THRESHOLD = float(os.environ.get("MACRO_LOW_SOURCE_COVERAGE_THRESHOLD", 50.0))
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")  # never hardcoded

# Sector macro scoring
MIN_SECTOR_MACRO_COVERAGE = float(os.environ.get("MIN_SECTOR_MACRO_COVERAGE", 75.0))
MIN_INDUSTRY_MACRO_COVERAGE = float(os.environ.get("MIN_INDUSTRY_MACRO_COVERAGE", 75.0))
MIN_BASIC_INDUSTRY_MACRO_COVERAGE = float(os.environ.get("MIN_BASIC_INDUSTRY_MACRO_COVERAGE", 75.0))

# Final sector discovery ranking
MIN_SECTOR_DISCOVERY_COVERAGE = float(os.environ.get("MIN_SECTOR_DISCOVERY_COVERAGE", 0.0))
SECTOR_SELECTION_COUNT = int(os.environ.get("SECTOR_SELECTION_COUNT", 3))

# Final industry discovery ranking
MIN_INDUSTRY_DISCOVERY_COVERAGE = float(os.environ.get("MIN_INDUSTRY_DISCOVERY_COVERAGE", 0.0))
INDUSTRY_SELECTION_COUNT = int(os.environ.get("INDUSTRY_SELECTION_COUNT", 3))

# Final basic-industry discovery ranking
MIN_BASIC_INDUSTRY_DISCOVERY_COVERAGE = float(os.environ.get("MIN_BASIC_INDUSTRY_DISCOVERY_COVERAGE", 0.0))
BASIC_INDUSTRY_SELECTION_COUNT = int(os.environ.get("BASIC_INDUSTRY_SELECTION_COUNT", 2))

# Final stock candidate scoring
MIN_STOCK_SCORE_COVERAGE = float(os.environ.get("MIN_STOCK_SCORE_COVERAGE", 0.0))

# Final stock discovery ranking
STOCK_SELECTION_COUNT = int(os.environ.get("STOCK_SELECTION_COUNT", 3))
