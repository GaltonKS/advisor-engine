// Shapes returned by the API. Every number in them was computed by the engine.

export interface Adviser {
  id: string;
  email: string;
  firm_id: string;
  role: string;
  status?: string;
}

export interface Me {
  adviser: Adviser;
  firm: { id: string; name?: string };
  auth: "supabase" | "dev";
  llm: "mock" | "anthropic";
  daily_turn_limit: number;
  engine_version: string;
}

export interface Portfolio {
  id: string;
  client_id: string;
  filename: string;
  sha256: string;
  created_at: string;
  label?: string;
  source?: "upload" | "editor";
}

export interface QuestionnaireResult {
  score: number;
  max_score: number;
  risk_level: number;
  risk_level_max: number;
  tolerable_loss: number;
  answers: Record<string, string>;
  method_version: string;
  method_status: string;
}

export interface Client {
  id: string;
  name: string;
  objective: string;
  tone: string;
  created_at: string;
  questionnaire?: Record<string, number> | null;
  questionnaire_result?: QuestionnaireResult | null;
  portfolios?: Portfolio[];
}

export interface Position {
  ticker: string;
  sector: string;
  asset_class?: string;
  weight: number;
  risk_share: number;
  risk_to_weight: number;
  volatility: number;
  avg_corr_to_rest: number;
  beta?: number; // with a market series only
  corr_to_market?: number;
}

export interface RiskProfile {
  returns_source: string;
  engine_version: string;
  n_periods: number;
  concentration: {
    effective_bets: number;
    n_positions: number;
    top_component_share: number;
    component_shares: number[];
    risk_sources?: RiskSource[];
    market_adjusted: { residual_effective_bets: number; market_proxy: string; market_share_of_risk?: number;
                       portfolio_beta?: number; portfolio_corr_to_market?: number };
    // sector_effective_bets is absent (and `withheld` set) when some holdings have no sector.
    sector_level: { sector_effective_bets?: number; n_sectors: number; withheld?: string };
  };
  sector_exposure: Record<string, number>;
  asset_class_exposure?: Record<string, number>;
  sector_risk?: Record<string, { weight: number; risk_share: number }>;
  asset_class_risk?: Record<string, { weight: number; risk_share: number }>;
  sector_coverage?: { unclassified_weight: number; n_unclassified: number; unclassified_tickers: string[] };
  drawdown: {
    historical_max_drawdown: number;
    period_volatility: number;
    stress_loss_1_in_100: number;
    period_label: string;
  };
  positions: Position[];
}

export interface HoldingRow {
  ticker: string;
  shares: number;
  price: number;
  sector: string;
  asset_class?: string;
  value?: number;
  weight?: number;
}

export interface ComparisonRow {
  key: string;
  label: string;
  kind: "number" | "fraction" | "count" | "loss";  // loss: a positive size
  before: number;
  after: number;
  change: number;
}

export interface Comparison {
  headline: ComparisonRow[];
  sectors: { sector: string; before: number; after: number; change: number }[];
  added: string[];
  removed: string[];
  period_label: string;
}

export interface CompareResult {
  before: RiskProfile;
  after: RiskProfile;
  comparison: Comparison;
}

export interface Scenario {
  id: string;
  label: string;
  description: string;
  default: number;
  sectors: Record<string, number>;
  asset_classes?: Record<string, number>;
}

export interface StressResult {
  scenario: Scenario | { id: "custom"; label: string; sectors: Record<string, number>; default: number; asset_classes?: Record<string, number> };
  portfolio_return: number;
  portfolio_value: number;
  value_change: number;
  sectors: { sector: string; weight: number; shock: number; contribution: number }[];
  asset_classes?: { asset_class: string; weight: number; shock: number; contribution: number }[];
  positions: { ticker: string; sector: string; asset_class?: string; weight: number; shock: number; contribution: number; value_change: number }[];
  basis: string;
  /** Where the assumptions came from; results are always engine output. */
  assumptions?: { source: "preset" | "adviser" | "ai_suggested" | "ai_suggested_edited";
                  description?: string; label?: string; model?: string };
}

/** AI-suggested shocks for a described scenario: assumptions to review, never results. */
export interface ScenarioSuggestion {
  description: string;
  label: string;
  sector_shocks: Record<string, number>;
  asset_class_shocks: Record<string, number>;
  default_shock: number;
  reasons: { kind: "sector" | "asset_class" | "default"; target: string; shock: number; reason: string }[];
  rationale: string;
  explanation_withheld: boolean;
  model: string;
  source: "ai" | "mock";
}

export interface Question {
  id: string;
  text: string;
  options: string[];
}

export interface SuitabilityCheck {
  id: string;
  label: string;
  portfolio_value: number;
  client_limit: number;
  within: boolean;
}

export interface ToolCall {
  name: string;
  label: string;
  is_error: boolean;
  holdings_sha256: string | null;
  output: RiskProfile | string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  blocked?: boolean;
  verification?: { ok: boolean; checked?: number; unmatched?: string[] };
  tool_calls?: ToolCall[];
  draft_id?: string | null;
}

export interface Draft {
  id: string;
  portfolio_id: string;
  original: string;
  approved: boolean;
  approved_text?: string | null;
  approved_by?: string | null;
  approved_at?: string | null;
  created_at: string;
}

export interface AuditRow {
  timestamp: string;
  event: string | null;
  engine_version: string;
  model: string | null;
  adviser_id: string | null;
  portfolio_id: string | null;
  user_message: string | null;
  blocked: boolean | null;
  verified: boolean | null;
}

// --- chart specs (built by the engine's charts.py; drawn, never computed, here) ---------

export interface ChartSource {
  label: string;
  n_periods: number;
  period_label: string;
  returns_source: string;
  engine_version: string;
  holdings_sha256: string | null;
}

interface ChartBase { title: string; subtitle: string; caveat: string; source: ChartSource }

export interface RiskByGroupChart extends ChartBase {
  kind: "risk_by_group";
  group: "sector" | "asset_class";
  rows: { label: string; weight: number; risk_share: number; risk_to_weight: number }[];
  axis_max: number;
  note: string;
}

export interface RiskVsSizeChart extends ChartBase {
  kind: "risk_vs_size";
  points: { ticker: string; weight: number; risk_share: number; risk_to_weight: number;
            highlight: boolean; label: boolean }[];
  x_max: number;
  y_max: number;
  highlight_rule: string;
}

export interface RiskSource { name: string; share: number }

export interface WhatIfChart extends ChartBase {
  kind: "what_if";
  option_label: string;
  mix: { label: string; sources: RiskSource[] }[];
  measures: { key: string; label: string; hint: string; kind: "fraction" | "number" | "loss";
              current: number; option: number }[];
  added: string[];
  removed: string[];
}

export type ChartSpec = RiskByGroupChart | RiskVsSizeChart | WhatIfChart;
