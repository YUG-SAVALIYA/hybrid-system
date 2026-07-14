class SectorClassifier:
    FINANCIAL_INDUSTRIES = {
        "Housing Finance Company",
        "Financial Technology (Fintech)",
        "Life Insurance",
        "Insurance Distributors",
        "Financial Institution",
        "Private Sector Bank",
        "Non Banking Financial Company (NBFC)",
        "Other Bank",
        "Financial Services",
        "Public Sector Bank",
        "General Insurance",
        "Other Financial Services",
        "Microfinance Institutions",
        "Financial Products Distributor",
        "Financial Services (Non-Bank Finance)"
    }

    @classmethod
    def is_financial_business(cls, sector: str | None, industry: str | None, basic_industry: str | None) -> bool:
        """
        Determines if a company is a financial business (bank, NBFC, insurer, etc.)
        where normal debt-to-equity scoring must not be used.
        """
        if industry and industry.strip() in cls.FINANCIAL_INDUSTRIES:
            return True
            
        if basic_industry and basic_industry.strip() in cls.FINANCIAL_INDUSTRIES:
            return True
            
        return False
