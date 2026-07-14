import logging

logger = logging.getLogger(__name__)

class NumericCleaner:
    @staticmethod
    def clean(value) -> float | None:
        """
        Preserves valid negative values.
        Converts valid numeric strings safely.
        Removes commas only when appropriate.
        Converts blank strings to unavailable (None).
        Rejects malformed values and returns None.
        Never converts unavailable values to zero.
        Records parsing warnings.
        """
        if value is None:
            return None
            
        if isinstance(value, (int, float)):
            return float(value)
            
        if isinstance(value, str):
            val_str = value.strip()
            if not val_str:
                return None
                
            # Remove commas
            val_str = val_str.replace(",", "")
            
            try:
                return float(val_str)
            except ValueError:
                logger.warning(f"Could not parse numeric value: '{value}'")
                return None
                
        logger.warning(f"Unexpected type for numeric cleaner: {type(value)}")
        return None
