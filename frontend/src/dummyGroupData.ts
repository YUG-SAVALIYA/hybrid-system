import { DiscoveryGroupResult } from "./api/discovery";

export const dummyGroupData: DiscoveryGroupResult = {
  name: "Dummy Sector (For UI Testing)",
  rank: 1,
  selected: true,
  constituent_count: 50,
  final_score: 75.4,
  technical_score: 82.1,
  fundamental_score: 68.5,
  macro_score: 72.0,
  status: "COMPLETED",
  coverage_pct: 100,
  warnings: [],
  tech_details: {
    median_relative_return: 12.4,
    outperformance_breadth: 65.0,
    percent_consistency_gte_60: 70.0,
    positive_return_breadth: 80.0,
    scores: {
      return_score: 85.0,
      breadth: 78.5,
      volume: 60.2,
      consistency: 88.0
    }
  },
  fund_details: {
    metrics: {
      sales_growth_pct: { median: 15.2 },
      net_profit_growth_pct: { median: 22.4 },
      latest_operating_margin_pct: { median: 18.5 },
      operating_margin_change_pp: { median: 2.1 },
      debt_to_equity: { median: 0.45 },
      borrowing_change_pct: { median: -5.0 },
      latest_ocf_to_pat: { median: 1.2 },
      pat_growth_volatility_pct: { median: 14.3 }
    },
    pillar_scores: {
      growth: { score: 85.2 },
      profitability: { score: 72.4 },
      financial_strength: { score: 65.0 },
      earnings_quality: { score: 80.1 }
    }
  },
  macro_details: {
    llm_overall_impact: "POSITIVE",
    categories: {
      INTEREST_RATES_AND_LIQUIDITY: {
        impact: "NEUTRAL",
        numeric_value: 50,
        confidence: "MEDIUM",
        configured_weight: 25.0,
        confidence_multiplier: 0.75,
        effective_weight: 18.75
      },
      COMMODITY_AND_INPUT_COSTS: {
        impact: "POSITIVE",
        numeric_value: 100,
        confidence: "HIGH",
        configured_weight: 25.0,
        confidence_multiplier: 1.0,
        effective_weight: 25.0
      },
      GOVERNMENT_POLICY_AND_SPENDING: {
        impact: "POSITIVE",
        numeric_value: 100,
        confidence: "HIGH",
        configured_weight: 25.0,
        confidence_multiplier: 1.0,
        effective_weight: 25.0
      },
      DEMAND_CONDITIONS: {
        impact: "NEGATIVE",
        numeric_value: 0,
        confidence: "LOW",
        configured_weight: 25.0,
        confidence_multiplier: 0.5,
        effective_weight: 12.5
      }
    }
  }
};
