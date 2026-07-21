export const dummyRunData: any = {
  "run_id": "run-dummy-1234",
  "status": "COMPLETED",
  "resume_count": 0,
  "warnings": [],
  "stage_results": {},
  "horizons": {
    "SHORT": {
      "status": "COMPLETED",
      "sectors": [
        {
          "name": "Technology",
          "horizon": "SHORT",
          "rank": 1,
          "selected": true,
          "constituent_count": 50,
          "final_score": 85.4,
          "technical_score": 88.2,
          "fundamental_score": 75.5,
          "macro_score": 82.0,
          "tech_details": {
            "median_relative_return": 15.4,
            "outperformance_breadth": 68.0,
            "percent_consistency_gte_60": 72.0,
            "positive_return_breadth": 85.0,
            "scores": {
              "return_score": 90.0,
              "breadth": 82.5,
              "volume": 70.2,
              "consistency": 92.0
            }
          },
          "fund_details": {
            "metrics": {
              "sales_growth_pct": { "median": 25.2 },
              "net_profit_growth_pct": { "median": 32.4 },
              "latest_operating_margin_pct": { "median": 22.5 },
              "operating_margin_change_pp": { "median": 4.1 },
              "debt_to_equity": { "median": 0.15 },
              "borrowing_change_pct": { "median": -12.0 },
              "latest_ocf_to_pat": { "median": 1.5 },
              "pat_growth_volatility_pct": { "median": 12.3 }
            },
            "pillar_scores": {
              "growth": { "score": 95.2 },
              "profitability": { "score": 88.4 },
              "financial_strength": { "score": 92.0 },
              "earnings_quality": { "score": 85.1 }
            }
          },
          "macro_details": {
            "llm_overall_impact": "POSITIVE",
            "categories": {
              "INTEREST_RATES_AND_LIQUIDITY": { "impact": "NEUTRAL", "numeric_value": 50, "confidence": "MEDIUM" },
              "COMMODITY_AND_INPUT_COSTS": { "impact": "POSITIVE", "numeric_value": 100, "confidence": "HIGH" },
              "GOVERNMENT_POLICY_AND_SPENDING": { "impact": "POSITIVE", "numeric_value": 100, "confidence": "HIGH" },
              "DEMAND_CONDITIONS": { "impact": "POSITIVE", "numeric_value": 100, "confidence": "HIGH" }
            }
          }
        },
        {
          "name": "Healthcare",
          "horizon": "SHORT",
          "rank": 2,
          "selected": true,
          "constituent_count": 40,
          "final_score": 70.1,
          "technical_score": 65.0,
          "fundamental_score": 85.0,
          "macro_score": 75.0,
          "tech_details": {
            "median_relative_return": 5.4,
            "outperformance_breadth": 50.0,
            "percent_consistency_gte_60": 55.0,
            "positive_return_breadth": 60.0,
            "scores": {
              "return_score": 60.0,
              "breadth": 55.5,
              "volume": 50.2,
              "consistency": 62.0
            }
          },
          "fund_details": {
            "metrics": {
              "sales_growth_pct": { "median": 10.2 },
              "net_profit_growth_pct": { "median": 15.4 },
              "latest_operating_margin_pct": { "median": 15.5 },
              "operating_margin_change_pp": { "median": 1.1 },
              "debt_to_equity": { "median": 0.25 },
              "borrowing_change_pct": { "median": -2.0 },
              "latest_ocf_to_pat": { "median": 1.1 },
              "pat_growth_volatility_pct": { "median": 8.3 }
            },
            "pillar_scores": {
              "growth": { "score": 75.2 },
              "profitability": { "score": 80.4 },
              "financial_strength": { "score": 85.0 },
              "earnings_quality": { "score": 90.1 }
            }
          },
          "macro_details": {
            "llm_overall_impact": "POSITIVE",
            "categories": {
              "DEMAND_CONDITIONS": { "impact": "POSITIVE", "numeric_value": 100, "confidence": "HIGH" }
            }
          }
        },
        {
          "name": "Energy",
          "horizon": "SHORT",
          "rank": 3,
          "selected": false,
          "constituent_count": 30,
          "final_score": 45.4,
          "technical_score": 40.2,
          "fundamental_score": 55.5,
          "macro_score": 32.0,
          "tech_details": {
            "median_relative_return": -5.4,
            "outperformance_breadth": 30.0,
            "percent_consistency_gte_60": 20.0,
            "positive_return_breadth": 25.0,
            "scores": {
              "return_score": 30.0,
              "breadth": 25.5,
              "volume": 40.2,
              "consistency": 22.0
            }
          },
          "fund_details": {
            "metrics": {
              "sales_growth_pct": { "median": -5.2 },
              "net_profit_growth_pct": { "median": -12.4 },
              "latest_operating_margin_pct": { "median": 8.5 },
              "operating_margin_change_pp": { "median": -4.1 },
              "debt_to_equity": { "median": 0.85 },
              "borrowing_change_pct": { "median": 15.0 },
              "latest_ocf_to_pat": { "median": 0.5 },
              "pat_growth_volatility_pct": { "median": 45.3 }
            },
            "pillar_scores": {
              "growth": { "score": 25.2 },
              "profitability": { "score": 45.4 },
              "financial_strength": { "score": 35.0 },
              "earnings_quality": { "score": 40.1 }
            }
          },
          "macro_details": {
            "llm_overall_impact": "NEGATIVE",
            "categories": {
              "DEMAND_CONDITIONS": { "impact": "NEGATIVE", "numeric_value": 0, "confidence": "HIGH" }
            }
          }
        }
      ],
      "industries": [
        {
          "name": "Software",
          "parent_sector": "Technology",
          "horizon": "SHORT",
          "rank": 1,
          "selected": true,
          "final_score": 88.4,
          "technical_score": 90.2,
          "fundamental_score": 80.5,
          "macro_score": 85.0,
          "tech_details": {
            "scores": { "return_score": 95.0, "breadth": 88.5, "volume": 75.2, "consistency": 95.0 }
          },
          "fund_details": {
            "metrics": {
              "sales_growth_pct": { "median": 30.2 },
              "net_profit_growth_pct": { "median": 40.4 },
              "latest_operating_margin_pct": { "median": 25.5 },
              "operating_margin_change_pp": { "median": 5.1 },
              "debt_to_equity": { "median": 0.05 },
              "borrowing_change_pct": { "median": -15.0 },
              "latest_ocf_to_pat": { "median": 1.8 },
              "pat_growth_volatility_pct": { "median": 10.3 }
            },
            "pillar_scores": {
              "growth": { "score": 98.2 },
              "profitability": { "score": 92.4 },
              "financial_strength": { "score": 95.0 },
              "earnings_quality": { "score": 90.1 }
            }
          },
          "macro_details": {
            "llm_overall_impact": "POSITIVE",
            "categories": { "DEMAND_CONDITIONS": { "impact": "POSITIVE", "numeric_value": 100, "confidence": "HIGH" } }
          }
        },
        {
          "name": "Hardware",
          "parent_sector": "Technology",
          "horizon": "SHORT",
          "rank": 2,
          "selected": true,
          "final_score": 75.4,
          "technical_score": 72.2,
          "fundamental_score": 70.5,
          "macro_score": 80.0,
          "tech_details": {
            "scores": { "return_score": 70.0, "breadth": 75.5, "volume": 65.2, "consistency": 70.0 }
          },
          "fund_details": {
            "pillar_scores": { "growth": { "score": 75.2 }, "profitability": { "score": 70.4 }, "financial_strength": { "score": 75.0 }, "earnings_quality": { "score": 80.1 } }
          }
        },
        {
          "name": "Biotech",
          "parent_sector": "Healthcare",
          "horizon": "SHORT",
          "rank": 3,
          "selected": true,
          "final_score": 72.4,
          "technical_score": 68.2,
          "fundamental_score": 82.5,
          "macro_score": 70.0,
          "tech_details": {
            "scores": { "return_score": 65.0, "breadth": 60.5, "volume": 55.2, "consistency": 65.0 }
          },
          "fund_details": {
            "pillar_scores": { "growth": { "score": 80.2 }, "profitability": { "score": 85.4 }, "financial_strength": { "score": 90.0 }, "earnings_quality": { "score": 95.1 } }
          }
        }
      ],
      "basic_industries": [
        {
          "name": "Cloud Computing",
          "parent_sector": "Technology",
          "parent_industry": "Software",
          "horizon": "SHORT",
          "rank": 1,
          "selected": true,
          "final_score": 92.4,
          "technical_score": 95.2,
          "fundamental_score": 85.5,
          "macro_score": 90.0,
          "tech_details": {
            "scores": { "return_score": 98.0, "breadth": 92.5, "volume": 85.2, "consistency": 98.0 }
          },
          "fund_details": {
            "pillar_scores": { "growth": { "score": 99.2 }, "profitability": { "score": 95.4 }, "financial_strength": { "score": 98.0 }, "earnings_quality": { "score": 92.1 } }
          }
        },
        {
          "name": "Cybersecurity",
          "parent_sector": "Technology",
          "parent_industry": "Software",
          "horizon": "SHORT",
          "rank": 2,
          "selected": true,
          "final_score": 85.4,
          "technical_score": 82.2,
          "fundamental_score": 88.5,
          "macro_score": 80.0,
          "tech_details": {
            "scores": { "return_score": 85.0, "breadth": 80.5, "volume": 75.2, "consistency": 85.0 }
          },
          "fund_details": {
            "pillar_scores": { "growth": { "score": 90.2 }, "profitability": { "score": 85.4 }, "financial_strength": { "score": 90.0 }, "earnings_quality": { "score": 88.1 } }
          }
        },
        {
          "name": "Genomics",
          "parent_sector": "Healthcare",
          "parent_industry": "Biotech",
          "horizon": "SHORT",
          "rank": 3,
          "selected": true,
          "final_score": 78.4,
          "technical_score": 75.2,
          "fundamental_score": 82.5,
          "macro_score": 80.0,
          "tech_details": {
            "scores": { "return_score": 75.0, "breadth": 70.5, "volume": 65.2, "consistency": 75.0 }
          },
          "fund_details": {
            "pillar_scores": { "growth": { "score": 85.2 }, "profitability": { "score": 80.4 }, "financial_strength": { "score": 85.0 }, "earnings_quality": { "score": 90.1 } }
          }
        }
      ],
      "stocks": [
        {
          "company_id": "c1",
          "symbol": "TECHSTK1",
          "rank": 1,
          "selected": true,
          "final_score": 95.0,
          "technical_score": 98.0,
          "fundamental_score": 92.0,
          "inherited_macro_score": 90.0,
          "score_status": "VERY_STRONG",
          "sector": "Technology",
          "industry": "Software",
          "basic_industry": "Cloud Computing"
        },
        {
          "company_id": "c2",
          "symbol": "TECHSTK2",
          "rank": 2,
          "selected": true,
          "final_score": 88.0,
          "technical_score": 85.0,
          "fundamental_score": 89.0,
          "inherited_macro_score": 80.0,
          "score_status": "STRONG",
          "sector": "Technology",
          "industry": "Software",
          "basic_industry": "Cybersecurity"
        },
        {
          "company_id": "c3",
          "symbol": "HEALTHSTK1",
          "rank": 3,
          "selected": true,
          "final_score": 82.0,
          "technical_score": 75.0,
          "fundamental_score": 90.0,
          "inherited_macro_score": 80.0,
          "score_status": "STRONG",
          "sector": "Healthcare",
          "industry": "Biotech",
          "basic_industry": "Genomics"
        },
        {
          "company_id": "c4",
          "symbol": "ENERGYSTK1",
          "rank": 150,
          "selected": false,
          "final_score": 35.0,
          "technical_score": 30.0,
          "fundamental_score": 40.0,
          "inherited_macro_score": 32.0,
          "score_status": "WEAK",
          "sector": "Energy",
          "industry": "Oil & Gas",
          "basic_industry": "Exploration"
        }
      ],
      "warnings": []
    }
  }
};
