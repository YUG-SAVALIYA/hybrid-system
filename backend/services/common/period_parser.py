import datetime
import calendar

MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4,
    "May": 5, "Jun": 6, "Jul": 7, "Aug": 8,
    "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12
}

class PeriodParser:
    @staticmethod
    def parse(period_str: str) -> dict:
        if not period_str:
            return {
                "original_period": period_str,
                "period_type": "UNKNOWN",
                "period_end": None,
                "financial_year": None,
                "parse_status": "INVALID"
            }
        
        parts = period_str.strip().split()
        if len(parts) == 2 and parts[0] in MONTH_MAP and parts[1].isdigit():
            month_str, year_str = parts
            month = MONTH_MAP[month_str]
            year = int(year_str)
            
            # Find the last day of that month
            last_day = calendar.monthrange(year, month)[1]
            period_end = f"{year}-{month:02d}-{last_day:02d}"
            
            # We assume FY is the year in the string
            financial_year = f"FY{year}"
            
            return {
                "original_period": period_str,
                "period_type": "ANNUAL",
                "period_end": period_end,
                "financial_year": financial_year,
                "parse_status": "VALID"
            }

        return {
            "original_period": period_str,
            "period_type": "UNKNOWN",
            "period_end": None,
            "financial_year": None,
            "parse_status": "INVALID"
        }

class PeriodComparator:
    @staticmethod
    def get_previous_comparable_period(current_period_str: str) -> str | None:
        """
        Takes a string like 'Mar 2024' and returns 'Mar 2023'.
        Returns None if format is not recognized.
        """
        parsed = PeriodParser.parse(current_period_str)
        if parsed["parse_status"] != "VALID" or parsed["period_type"] != "ANNUAL":
            return None
            
        parts = current_period_str.strip().split()
        month_str = parts[0]
        year = int(parts[1])
        
        return f"{month_str} {year - 1}"

    @staticmethod
    def is_comparable(period_1_str: str, period_2_str: str) -> bool:
        """
        Verifies both periods are of the same type.
        """
        p1 = PeriodParser.parse(period_1_str)
        p2 = PeriodParser.parse(period_2_str)
        
        if p1["parse_status"] != "VALID" or p2["parse_status"] != "VALID":
            return False
            
        if p1["period_type"] != p2["period_type"]:
            return False
            
        # For now, everything valid is ANNUAL, so they are comparable.
        return True
