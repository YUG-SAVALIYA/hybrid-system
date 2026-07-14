class SymbolMatcher:
    def __init__(self, suffixes_to_strip: list[str] = None):
        self.suffixes_to_strip = suffixes_to_strip or []

    def normalize(self, symbol: str) -> str:
        if not symbol:
            return ""
        
        # Trim whitespace and upper case
        norm = symbol.strip().upper()
        
        # Safe removal of known exchange suffixes only when configured
        for suffix in self.suffixes_to_strip:
            suffix_upper = suffix.upper()
            if norm.endswith(suffix_upper):
                # Only strip if it actually has the suffix, don't use replace to avoid middle-string replacements
                norm = norm[:-len(suffix_upper)]
                break
                
        return norm

    def is_match(self, company_symbol: str, candle_symbol: str) -> bool:
        return self.normalize(company_symbol) == self.normalize(candle_symbol)
