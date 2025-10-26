const STORAGE_KEY = "sado-trade-bot-api-base";

const DEFAULT_API_BASE = (() => {
  if (window.__SADO_API_BASE__) {
    return window.__SADO_API_BASE__;
  }
  const { origin } = window.location;
  if (origin.includes(":8501")) {
    return `${origin}/api`;
  }
  if (origin.startsWith("http://127.0.0.1") || origin.startsWith("http://localhost")) {
    return "http://127.0.0.1:8000";
  }
  return `${origin}/api`;
})();

const HOSTS_VISIBLE_ONLY_INSIDE_CONTAINERS = new Set(["backend", "frontend", "api", "web"]);
const LOCAL_LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost"]);

let apiBaseCandidates = [];
let apiBase = DEFAULT_API_BASE;
const DEFAULT_CAPITAL_KRW = 20_000_000;
const DEFAULT_MARKET_CODE = "KRW-BTC";
const DEFAULT_AUTOPILOT_INTERVAL = "minute60";
const DEFAULT_AUTOPILOT_RISK = 0.6;
const DEFAULT_AUTOPILOT_CAPITAL = DEFAULT_CAPITAL_KRW;
const DEFAULT_AUTOPILOT_POLL_INTERVAL = 120;
const DEFAULT_AUTOPILOT_MAX_POSITION = 0.25;
const DEFAULT_AUTOPILOT_CONFIDENCE = 60;
const DEFAULT_AUTOPILOT_MAX_MARKETS = 180;
const AUTOPILOT_MIN_CAPITAL = 1_000_000;
const AUTOPILOT_MIN_POLL_INTERVAL = 15;
const AUTOPILOT_MAX_POLL_INTERVAL = 900;
const AUTOPILOT_MIN_MAX_POSITION = 0.05;
const AUTOPILOT_MAX_MAX_POSITION = 1;
const AUTOPILOT_MIN_CONFIDENCE = 0;
const AUTOPILOT_MAX_CONFIDENCE = 100;
const AUTOPILOT_MIN_RECOMMENDATION_MARKETS = 5;
const AUTOPILOT_MAX_RECOMMENDATION_MARKETS = 200;
const COLLAPSIBLE_SELECTOR = "[data-collapsible]";
const COLLAPSIBLE_DEFAULT_LIMIT = 220;
const collapsibleMetadata = new WeakMap();
const AUTO_REFRESH_INTERVALS = Object.freeze({
  blueprint: 600_000,
  rebalance: 600_000,
  aiPortfolio: 300_000,
  copilot: 300_000,
});

const toFiniteNumber = (value) => {
  if (typeof value === "number") {
    return Number.isFinite(value) ? value : NaN;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed) {
      return NaN;
    }
    const numeric = Number(trimmed);
    return Number.isFinite(numeric) ? numeric : NaN;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : NaN;
};

const sanitiseDecimalInput = (input, fallback, { min = null, max = null, precision = 4 } = {}) => {
  let numeric = toFiniteNumber(input?.value);
  if (Number.isNaN(numeric)) {
    numeric = fallback;
  }
  if (typeof min === "number" && numeric < min) {
    numeric = min;
  }
  if (typeof max === "number" && numeric > max) {
    numeric = max;
  }
  if (typeof precision === "number" && Number.isFinite(precision)) {
    const factor = 10 ** Math.max(0, precision);
    numeric = Math.round(numeric * factor) / factor;
  }
  if (input) {
    input.value = `${numeric}`;
  }
  return numeric;
};

const sanitiseIntegerInput = (input, fallback, { min = null, max = null } = {}) => {
  let numeric = toFiniteNumber(input?.value);
  if (Number.isNaN(numeric)) {
    numeric = fallback;
  }
  numeric = Math.round(numeric);
  if (typeof min === "number" && numeric < min) {
    numeric = min;
  }
  if (typeof max === "number" && numeric > max) {
    numeric = max;
  }
  if (input) {
    input.value = `${numeric}`;
  }
  return numeric;
};

const createAutoRunner = (fn, delay = 700) => {
  let timerId;
  return () => {
    if (timerId) {
      window.clearTimeout(timerId);
    }
    timerId = window.setTimeout(() => {
      Promise.resolve(fn()).catch(() => {});
    }, delay);
  };
};

const startIntervalTask = (fn, interval, { immediate = false } = {}) => {
  if (immediate) {
    Promise.resolve(fn()).catch(() => {});
  }
  return window.setInterval(() => {
    Promise.resolve(fn()).catch(() => {});
  }, interval);
};

const shouldResetStoredBase = (value) => {
  if (!value) {
    return true;
  }

  if (value.startsWith("/")) {
    return false;
  }

  try {
    const url = new URL(value);
    const host = (url.hostname || "").toLowerCase();
    const currentHost = (window.location.hostname || "").toLowerCase();

    if (HOSTS_VISIBLE_ONLY_INSIDE_CONTAINERS.has(host) && host !== currentHost) {
      return true;
    }

    if (
      LOCAL_LOOPBACK_HOSTS.has(host) &&
      host !== currentHost &&
      !LOCAL_LOOPBACK_HOSTS.has(currentHost)
    ) {
      return true;
    }
  } catch (error) {
    return true;
  }

  return false;
};

const normaliseBase = (value) => {
  if (value === undefined || value === null) {
    return "";
  }
  if (typeof value !== "string") {
    return "";
  }
  const trimmed = value.trim();
  if (!trimmed) {
    return "";
  }
  return trimmed.replace(/\/+$/, "");
};

const loadInitialApiBase = () => {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (!stored) {
    return DEFAULT_API_BASE;
  }

  const normalised = normaliseBase(stored);
  if (shouldResetStoredBase(normalised)) {
    localStorage.removeItem(STORAGE_KEY);
    return DEFAULT_API_BASE;
  }

  return normalised || DEFAULT_API_BASE;
};

const joinApiUrl = (base, path) => {
  if (!base) {
    return path;
  }
  const normalisedBase = normaliseBase(base);
  if (!normalisedBase) {
    return path;
  }
  if (normalisedBase === "/") {
    return path;
  }
  if (path.startsWith("/") && normalisedBase.endsWith("/")) {
    return `${normalisedBase}${path.slice(1)}`;
  }
  if (!path.startsWith("/") && !normalisedBase.endsWith("/")) {
    return `${normalisedBase}/${path}`;
  }
  return `${normalisedBase}${path}`;
};

const registerApiCandidate = (value, { front = false } = {}) => {
  const normalised = normaliseBase(value);
  if (!normalised) {
    return "";
  }
  const index = apiBaseCandidates.indexOf(normalised);
  if (index === -1) {
    if (front) {
      apiBaseCandidates.unshift(normalised);
    } else {
      apiBaseCandidates.push(normalised);
    }
  } else if (front && index > 0) {
    apiBaseCandidates.splice(index, 1);
    apiBaseCandidates.unshift(normalised);
  }
  return normalised;
};

const buildApiBaseCandidates = (initialValue) => {
  apiBaseCandidates = [];
  registerApiCandidate(initialValue, { front: true });
  registerApiCandidate(DEFAULT_API_BASE);
  registerApiCandidate("/api");

  const { protocol, hostname, port } = window.location;
  if (hostname) {
    const originPort = port ? `:${port}` : "";
    const originBase = `${protocol}//${hostname}${originPort}`;
    registerApiCandidate(originBase);
    registerApiCandidate(`${originBase}/api`);
    if (!port || port !== "8000") {
      registerApiCandidate(`${protocol}//${hostname}:8000`);
      registerApiCandidate(`${protocol}//${hostname}:8000/api`);
    }
  }

  registerApiCandidate("http://backend:8000");
  registerApiCandidate("http://backend:8000/api");
  registerApiCandidate("http://127.0.0.1:8000");
  registerApiCandidate("http://localhost:8000");
};

const deriveAlternateBase = (value) => {
  if (!value || value.startsWith("/")) {
    return "";
  }
  if (value.endsWith("/api")) {
    return normaliseBase(value.slice(0, -4));
  }
  return normaliseBase(`${value}/api`);
};

const pickNextApiCandidate = (attempted) => {
  for (const candidate of apiBaseCandidates) {
    if (!attempted.has(candidate)) {
      return candidate;
    }
  }
  return "";
};

const percentFormatter = new Intl.NumberFormat("ko-KR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const ratioFormatter = new Intl.NumberFormat("ko-KR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const currencyFormatter = new Intl.NumberFormat("ko-KR");
const currencySymbolFormatter = new Intl.NumberFormat("ko-KR", {
  style: "currency",
  currency: "KRW",
  maximumFractionDigits: 0,
});
const formatPercentNumber = (value) => `${ratioFormatter.format(value)}%`;
const formatPercent = (value) => `${percentFormatter.format(value)}%`;
const formatCurrency = (value) => currencyFormatter.format(Math.round(value ?? 0));
const formatCurrencyWithSymbol = (value) => currencySymbolFormatter.format(Math.round(value ?? 0));

const marketOptionsEl = document.getElementById("market-options");
const marketSearchInput = document.getElementById("market-search");
const marketResultsEl = document.getElementById("market-results");
const marketBaseButtons = document.querySelectorAll("[data-market-base]");
const marketGroupsEl = document.getElementById("market-groups");
const navToggleBtn = document.getElementById("nav-toggle");
const navLinksList = document.getElementById("global-nav-links");
const toplinePaperHeartbeatEl = document.getElementById("topline-paper-heartbeat");
const toplinePaperNoteEl = document.getElementById("topline-paper-note");
const toplineAutopilotStatusEl = document.getElementById("topline-autopilot-status");
const toplineAutopilotNoteEl = document.getElementById("topline-autopilot-note");
const toplineAutopilotCountdownEl = document.getElementById("topline-autopilot-countdown");
const toplineLiveStatusEl = document.getElementById("topline-live-status");
const toplineLiveNoteEl = document.getElementById("topline-live-note");
const initialCapitalEl = document.getElementById("metric-initial-capital");
const endingEquityEl = document.getElementById("metric-ending-equity");
const profitKrwEl = document.getElementById("metric-profit-krw");
const marketNoteEl = document.getElementById("metric-market-note");
const priceSourceEl = document.getElementById("metric-price-source");
const integrityScoreEl = document.getElementById("metric-integrity-score");
const integrityFlagsEl = document.getElementById("metric-integrity-flags");

const totalReturnEl = document.getElementById("metric-total-return");
const annualReturnEl = document.getElementById("metric-annual-return");
const drawdownEl = document.getElementById("metric-drawdown");
const tradeCountEl = document.getElementById("metric-trade-count");
const tradeWinrateEl = document.getElementById("metric-trade-winrate");
const tradeAvgEl = document.getElementById("metric-trade-avg");
const tradeExpectancyEl = document.getElementById("metric-trade-expectancy");
const tradeMedianEl = document.getElementById("metric-trade-median");
const tradeWinLossEl = document.getElementById("metric-trade-winloss");
const tradePayoffEl = document.getElementById("metric-trade-payoff");
const tradeBestEl = document.getElementById("metric-trade-best");
const tradeWorstEl = document.getElementById("metric-trade-worst");
const tradeTableBody = document.getElementById("trade-table");
const tradeHistoryRefreshBtn = document.getElementById("trade-history-refresh");
const assistantHistoryEl = document.getElementById("assistant-history");
const assistantStatusEl = document.getElementById("assistant-status");
const assistantRunBtn = document.getElementById("assistant-run");
const assistantClearBtn = document.getElementById("assistant-clear");
const assistantChatToggle = document.getElementById("assistant-chat-toggle");
const equityNoteEl = document.getElementById("equity-note");
const rebalanceOutputEl = document.getElementById("rebalance-output");
const blueprintOutputEl = document.getElementById("blueprint-output");
const yearEl = document.getElementById("year");

const recommendationsPanelEl = document.getElementById("recommendations-panel");
const recommendationsListEl = document.getElementById("recommendations-list");
const recommendationsFilterEl = document.getElementById("recommendations-filter");
const recommendationsUpdatedEl = document.getElementById("recommendations-updated");
const recommendationsSourceEl = document.getElementById("recommendations-source");
const recommendationsRefreshBtn = document.getElementById("recommendations-refresh");
const recommendationsErrorsEl = document.getElementById("recommendations-errors");

const resetIntegrityBadge = () => {
  if (!integrityScoreEl || !integrityFlagsEl) return;
  integrityScoreEl.dataset.status = "loading";
  integrityScoreEl.textContent = "점검 중";
  integrityFlagsEl.textContent = "-";
  integrityFlagsEl.classList.add("muted");
};

const updateIntegrityBadge = (report) => {
  if (!integrityScoreEl || !integrityFlagsEl) return;
  if (!report || typeof report.integrity_score !== "number") {
    resetIntegrityBadge();
    return;
  }

  const score = Number(report.integrity_score);
  let status = "online";
  if (!Number.isFinite(score)) {
    resetIntegrityBadge();
    return;
  }
  if (score < 40) {
    status = "offline";
  } else if (score < 70) {
    status = "warning";
  }

  integrityScoreEl.dataset.status = status;
  integrityScoreEl.textContent = `${Math.round(score)}점`;

  if (Array.isArray(report.integrity_flags) && report.integrity_flags.length > 0) {
    integrityFlagsEl.textContent = report.integrity_flags.join(" · ");
    integrityFlagsEl.classList.remove("muted");
  } else {
    integrityFlagsEl.textContent = "이상 없음";
    integrityFlagsEl.classList.add("muted");
  }
};

resetIntegrityBadge();

const FALLBACK_MARKET_ROWS = [
  ["KRW-BTC", "비트코인", "Bitcoin"],
  ["KRW-ETH", "이더리움", "Ethereum"],
  ["KRW-SOL", "솔라나", "Solana"],
  ["KRW-XRP", "리플", "Ripple"],
  ["KRW-ADA", "에이다", "Cardano"],
  ["KRW-MATIC", "폴리곤", "Polygon"],
  ["KRW-DOGE", "도지코인", "Dogecoin"],
  ["KRW-DOT", "폴카닷", "Polkadot"],
  ["KRW-LINK", "체인링크", "Chainlink"],
  ["KRW-BCH", "비트코인캐시", "Bitcoin Cash"],
  ["KRW-LTC", "라이트코인", "Litecoin"],
  ["KRW-AVAX", "아발란체", "Avalanche"],
  ["KRW-ATOM", "코스모스", "Cosmos"],
  ["KRW-SAND", "샌드박스", "The Sandbox"],
  ["KRW-NEAR", "니어", "NEAR Protocol"],
  ["KRW-APT", "앱토스", "Aptos"],
  ["KRW-ARB", "아비트럼", "Arbitrum"],
  ["KRW-TRX", "트론", "TRON"],
  ["KRW-XLM", "스텔라루멘", "Stellar"],
  ["KRW-ETC", "이더리움클래식", "Ethereum Classic"],
  ["KRW-FTM", "팬텀", "Fantom"],
  ["KRW-AXS", "엑시인피니티", "Axie Infinity"],
  ["KRW-ALGO", "알고랜드", "Algorand"],
  ["KRW-STX", "스택스", "Stacks"],
  ["KRW-SHIB", "시바이누", "Shiba Inu"],
  ["KRW-PEPE", "페페", "PEPE"],
  ["KRW-MANA", "디센트럴랜드", "Decentraland"],
  ["KRW-SXP", "솔라", "Solar"],
  ["KRW-CHZ", "칠리즈", "Chiliz"],
  ["KRW-AAVE", "에이브", "Aave"],
];

const normaliseMarketEntry = (entry) => {
  if (!entry) return null;

  let market;
  let korean;
  let english;
  let warning = "NONE";
  let suspended = false;
  let base;
  let quote;

  if (Array.isArray(entry)) {
    [market, korean, english] = entry;
  } else if (typeof entry === "string") {
    market = entry;
    korean = entry;
    english = entry;
  } else if (typeof entry === "object") {
    market = entry.market;
    korean = entry.korean_name ?? entry.korean ?? entry.name;
    english = entry.english_name ?? entry.english ?? entry.display ?? entry.market;
    warning = (entry.market_warning || entry.warning || "NONE").toString();
    suspended = toBooleanFlag(entry.trading_suspended ?? entry.suspended);
    base = entry.base_currency;
    quote = entry.quote_currency;
  } else {
    return null;
  }

  if (!market) return null;

  const code = market.toString().trim().toUpperCase();
  if (!code) return null;

  const [derivedBase, derivedQuote] = code.includes("-") ? code.split("-", 2) : ["KRW", code];
  const baseCurrency = (base || derivedBase || "KRW").toString().toUpperCase();
  const quoteCurrency = (quote || derivedQuote || code).toString().toUpperCase();

  return {
    market: code,
    korean_name: (korean || quoteCurrency || code).toString(),
    english_name: (english || quoteCurrency || code).toString(),
    base_currency: baseCurrency,
    quote_currency: quoteCurrency,
    market_warning: warning.toString().toUpperCase() || "NONE",
    trading_suspended: toBooleanFlag(suspended),
  };
};

const toBooleanFlag = (value) => {
  if (value === null || value === undefined) return false;
  if (typeof value === "boolean") return value;
  const normalised = String(value).trim().toLowerCase();
  if (!normalised) return false;
  return !["0", "false", "no", "off", "none", "null"].includes(normalised);
};

const FALLBACK_MARKETS = FALLBACK_MARKET_ROWS.map((row) => normaliseMarketEntry(row)).filter(Boolean);

const replaceFallbackMarkets = (rows) => {
  if (!Array.isArray(rows) || !rows.length) {
    return;
  }

  const converted = rows.map((row) => normaliseMarketEntry(row)).filter(Boolean);
  if (!converted.length) {
    return;
  }

  FALLBACK_MARKETS.splice(0, FALLBACK_MARKETS.length, ...converted);
  cachedMarkets = [...FALLBACK_MARKETS];
  renderMarketOptions(cachedMarkets);
  renderMarketResults();
};

const loadFallbackMarketAsset = async () => {
  try {
    const response = await fetch("./assets/upbit_markets_krw.json", { cache: "no-store" });
    if (!response.ok) {
      return;
    }
    const payload = await response.json();
    replaceFallbackMarkets(payload);
  } catch (error) {
    console.warn("fallback market asset load failed", error);
  }
};

const PAPER_SOURCE_LABELS = {
  upbit: "업비트 실시간 시세 연동",
  upbit_stale: "업비트 지연 시세 (최근 데이터 재사용)",
  synthetic: "업비트 연결 실패 - 시뮬레이션 시세 사용",
  manual: "수동 시세 입력 (테스트/주문 반영)",
};

const PRICE_SOURCE_LABELS = {
  upbit: "업비트 실시간 시세",
  upbit_stale: "업비트 지연 시세",
  synthetic: "시뮬레이션 시세 (성과 지표 비활성)",
  manual: "사용자 제공 시세",
};

const FACTUAL_SOURCES = new Set(["upbit", "upbit_stale", "manual"]);

const isFactualReport = (report) => {
  if (!report) return false;
  const source = (report.price_source || "manual").toLowerCase();
  return FACTUAL_SOURCES.has(source);
};

const disableMetric = (element, text) => {
  if (!element) return;
  element.textContent = text;
  element.classList.add("metric-disabled");
};

const enableMetric = (element, text) => {
  if (!element) return;
  element.textContent = text;
  element.classList.remove("metric-disabled");
};

let cachedMarkets = [...FALLBACK_MARKETS];
let cachedMarketGroups = [];
let selectedBaseCurrency = "KRW";
let selectedMarketGroup = null;
let recommendationsState = { base: "KRW", interval: "minute60", limit: 5 };
let recommendationsTimer = null;
let tradeHistoryCache = [];
let assistantHistory = [];

const volatilityEl = document.getElementById("metric-volatility");
const sharpeEl = document.getElementById("metric-sharpe");
const sortinoEl = document.getElementById("metric-sortino");
const calmarEl = document.getElementById("metric-calmar");
const varEl = document.getElementById("metric-var");
const exposureEl = document.getElementById("metric-exposure");
const profitFactorEl = document.getElementById("metric-profit-factor");
const expectancyEl = document.getElementById("metric-expectancy");
const holdEl = document.getElementById("metric-hold");
const ulcerEl = document.getElementById("metric-ulcer");
const downsideEl = document.getElementById("metric-downside");
const recoveryEl = document.getElementById("metric-recovery");
const avgWinEl = document.getElementById("metric-avg-win");
const avgLossEl = document.getElementById("metric-avg-loss");
const winLossEl = document.getElementById("metric-winloss");
const tailEl = document.getElementById("metric-tail");
const omegaEl = document.getElementById("metric-omega");
const kellyEl = document.getElementById("metric-kelly");
const streakWinEl = document.getElementById("metric-streak-win");
const streakLossEl = document.getElementById("metric-streak-loss");
const skewnessEl = document.getElementById("metric-skewness");
const kurtosisEl = document.getElementById("metric-kurtosis");
const avgDrawdownEl = document.getElementById("metric-avg-drawdown");
const painEl = document.getElementById("metric-pain");
const runupEl = document.getElementById("metric-runup");
const mcMedianEl = document.getElementById("metric-mc-median");
const mcP05El = document.getElementById("metric-mc-p05");
const mcP95El = document.getElementById("metric-mc-p95");
const mcAvgEl = document.getElementById("metric-mc-avg");

const apiEndpointInput = document.getElementById("api-endpoint");
const apiStatusEl = document.getElementById("api-status");
const apiRefreshBtn = document.getElementById("api-refresh");
const riskSlider = document.getElementById("blueprint-risk");
const riskLabel = document.getElementById("blueprint-risk-label");
const chatTestBtn = document.getElementById("chat-test-btn");
const chatTestMessageInput = document.getElementById("chat-test-message");
const chatTestStatusEl = document.getElementById("chat-test-status");
const chatStatusDetailEl = document.getElementById("chat-status-detail");
const chatStatusHostEl = document.getElementById("chat-status-host");
const marketDirectoryStatusEl = document.getElementById("market-directory-status");
const marketDirectoryNoteEl = document.getElementById("market-directory-note");
const paperStatusMarketInput = document.getElementById("paper-status-market");
const paperStatusIntervalSelect = document.getElementById("paper-status-interval");
const paperStatusApplyBtn = document.getElementById("paper-status-apply");
const strategyMarketInput = document.getElementById("strategy-market");
const strategyIntervalSelect = document.getElementById("strategy-interval");
const strategyLiveDataInput = document.getElementById("strategy-live-data");

const orderForm = document.getElementById("order-form");
const orderResultEl = document.getElementById("order-result");
const paperSummaryEl = document.getElementById("paper-summary");
const paperResetForm = document.getElementById("paper-reset-form");
const paperInitialCashInput = document.getElementById("paper-initial-cash");
const paperRefreshBtn = document.getElementById("paper-refresh-btn");
const paperMarkForm = document.getElementById("paper-mark-form");
const paperMarkMarketInput = document.getElementById("paper-mark-market");
const paperMarkPriceInput = document.getElementById("paper-mark-price");
const paperHeartbeatEl = document.getElementById("paper-heartbeat");
const paperPriceSourceEl = document.getElementById("paper-price-source");
const paperHardResetBtn = document.getElementById("paper-hard-reset-btn");
const paperResetDialog = document.getElementById("paper-reset-dialog");
const paperResetConfirmBtn = document.getElementById("paper-reset-confirm");
const paperResetCancelBtn = document.getElementById("paper-reset-cancel");
const paperResetDialogMessage = paperResetDialog?.querySelector("p");
const liveBalanceBtn = document.getElementById("live-balance-btn");
const liveBalanceOutput = document.getElementById("live-balance-output");
const alphaBriefingEl = document.getElementById("alpha-briefing");
const liveMarketInput = document.getElementById("live-market");
const liveIntervalSelect = document.getElementById("live-interval");
const liveRefreshBtn = document.getElementById("live-refresh");
const liveSourceEl = document.getElementById("live-source");
const liveChartCanvas = document.getElementById("live-chart");
const liveRegimeEl = document.getElementById("live-regime");
const liveConfidenceEl = document.getElementById("live-confidence");
const liveUpdatedEl = document.getElementById("live-updated");
const liveSummaryEl = document.getElementById("live-summary");
const liveEmaFastEl = document.getElementById("live-ema-fast");
const liveEmaSlowEl = document.getElementById("live-ema-slow");
const liveEmaSignalEl = document.getElementById("live-ema-signal");
const liveRsiEl = document.getElementById("live-rsi");
const liveMacdEl = document.getElementById("live-macd");
const liveMacdHistEl = document.getElementById("live-macd-hist");
const liveVolatilityEl = document.getElementById("live-volatility");
const liveTrendEl = document.getElementById("live-trend");
const liveActionEl = document.getElementById("live-action");
const newsListEl = document.getElementById("news-list");
const aiActionEl = document.getElementById("ai-action");
const aiConfidenceEl = document.getElementById("ai-confidence");
const aiRegimeEl = document.getElementById("ai-regime");
const aiSummaryEl = document.getElementById("ai-summary");
const aiInstitutionalEl = document.getElementById("ai-institutional");
const aiConfluenceScoreEl = document.getElementById("ai-confluence-score");
const aiConfluenceLabelEl = document.getElementById("ai-confluence-label");
const aiConfluenceDriversEl = document.getElementById("ai-confluence-drivers");
const aiSignalsEl = document.getElementById("ai-signals");
const aiStopEl = document.getElementById("ai-stop");
const aiTakeEl = document.getElementById("ai-take");
const aiTrailingEl = document.getElementById("ai-trailing");
const aiSizeEl = document.getElementById("ai-size");
const aiRiskNoteEl = document.getElementById("ai-risk-note");
const aiRiskNotesEl = document.getElementById("ai-risk-notes");
const aiNewsEl = document.getElementById("ai-news");
const aiRefreshBtn = document.getElementById("ai-refresh");
const aiConsensusDominantEl = document.getElementById("ai-consensus-dominant");
const aiConsensusAgreementEl = document.getElementById("ai-consensus-agreement");
const aiConsensusDetailsEl = document.getElementById("ai-consensus-details");
const aiPortfolioForm = document.getElementById("ai-portfolio-form");
const aiRiskSlider = document.getElementById("ai-risk");
const aiRiskLabel = document.getElementById("ai-risk-label");
const aiExpectedReturnEl = document.getElementById("ai-expected-return");
const aiExpectedVolEl = document.getElementById("ai-expected-vol");
const aiSharpeEl = document.getElementById("ai-sharpe");
const aiDiversificationEl = document.getElementById("ai-diversification");
const aiTailRiskEl = document.getElementById("ai-tail-risk");
const aiHedgesEl = document.getElementById("ai-hedges");
const aiAllocationsBody = document.getElementById("ai-allocations-body");
const aiBriefingsEl = document.getElementById("ai-briefings");
const aiIncludeCashEl = document.getElementById("ai-include-cash");
const aiCapitalEl = document.getElementById("ai-capital");
const aiPreferredEl = document.getElementById("ai-preferred");
const copilotForm = document.getElementById("copilot-form");
const copilotQuestionInput = document.getElementById("copilot-question");
const copilotRiskSlider = document.getElementById("copilot-risk");
const copilotRiskLabel = document.getElementById("copilot-risk-label");
const copilotCapitalInput = document.getElementById("copilot-capital");
const copilotIncludePortfolioInput = document.getElementById("copilot-include-portfolio");
const copilotAnswerEl = document.getElementById("copilot-answer");
const copilotSummaryEl = document.getElementById("copilot-summary");
const copilotActionsEl = document.getElementById("copilot-actions");
const copilotRiskNoticesEl = document.getElementById("copilot-risk-notices");
const copilotHighlightsEl = document.getElementById("copilot-highlights");
const autopilotBiasEl = document.getElementById("autopilot-bias");
const autopilotSideEl = document.getElementById("autopilot-side");
const autopilotConfidenceEl = document.getElementById("autopilot-confidence");
const autopilotSizeEl = document.getElementById("autopilot-size");
const autopilotStopsEl = document.getElementById("autopilot-stops");
const autopilotTrailingEl = document.getElementById("autopilot-trailing");
const autopilotReasoningEl = document.getElementById("autopilot-reasoning");
const autopilotMonitoringEl = document.getElementById("autopilot-monitoring");
const autopilotHoldReasonEl = document.getElementById("autopilot-hold-reason");
const autopilotForm = document.getElementById("autopilot-form");
const autopilotModeSelect = document.getElementById("autopilot-mode");
const autopilotMarketInput = document.getElementById("autopilot-market");
const autopilotIntervalSelect = document.getElementById("autopilot-interval");
const autopilotAutoMarketInput = document.getElementById("autopilot-auto-market");
const autopilotBaseSelect = document.getElementById("autopilot-base");
const autopilotRecommendationIntervalSelect = document.getElementById(
  "autopilot-recommendation-interval",
);
const autopilotMaxMarketsInput = document.getElementById("autopilot-max-markets");
const autopilotRiskInput = document.getElementById("autopilot-risk");
const autopilotRiskLabel = document.getElementById("autopilot-risk-label");
const autopilotCapitalInput = document.getElementById("autopilot-capital");
const autopilotPollInput = document.getElementById("autopilot-poll");
const autopilotMaxPositionInput = document.getElementById("autopilot-max-position");
const autopilotConfidenceInput = document.getElementById("autopilot-confidence");
const autopilotIncludePortfolioInput = document.getElementById("autopilot-include-portfolio");
const autopilotIncludeWarningsInput = document.getElementById("autopilot-include-warnings");
const autopilotStateEl = document.getElementById("autopilot-state");
const autopilotLastRunEl = document.getElementById("autopilot-last-run");
const autopilotLastCompletedEl = document.getElementById("autopilot-last-completed");
const autopilotLastTradeEl = document.getElementById("autopilot-last-trade");
const autopilotLastErrorEl = document.getElementById("autopilot-last-error");
const autopilotLogList = document.getElementById("autopilot-log");
const autopilotStartBtn = document.getElementById("autopilot-start");
const autopilotStopBtn = document.getElementById("autopilot-stop");
const autopilotNextCountdownEl = document.getElementById("autopilot-next-countdown");
const autopilotRecommendationsList = document.getElementById("autopilot-recommendations");
const autopilotRecommendationSourceEl = document.getElementById("autopilot-recommendation-source");
const autopilotCandidatesEl = document.getElementById("autopilot-candidates");
const autopilotAnalysisEl = document.getElementById("autopilot-analysis");
const copilotLogEl = document.getElementById("copilot-log");
const diagnosticsListEl = document.getElementById("diagnostics-list");
const diagnosticsRefreshBtn = document.getElementById("diagnostics-refresh");
const selfCheckSummaryEl = document.getElementById("selfcheck-summary");
const selfCheckIssuesEl = document.getElementById("selfcheck-issues");
const selfCheckRefreshBtn = document.getElementById("selfcheck-refresh");
const backToTopBtn = document.getElementById("back-to-top");
const blueprintForm = document.getElementById("blueprint-form");
const blueprintStableInput = document.getElementById("blueprint-stable");
const blueprintAggressiveInput = document.getElementById("blueprint-aggressive");
const currentPositionsInput = document.getElementById("current-positions");
const targetAllocationsInput = document.getElementById("target-allocations");
const blueprintCapitalInput = document.getElementById("blueprint-capital");
const portfolioValueInput = document.getElementById("portfolio-value");

const DEFAULT_BLUEPRINT_STABLE = [
  {
    symbol: "BND",
    weight: 0.55,
    expected_return_pct: 4.2,
    expected_volatility_pct: 5.5,
  },
  {
    symbol: "JEPI",
    weight: 0.45,
    expected_return_pct: 6.1,
    expected_volatility_pct: 7.0,
  },
];

const DEFAULT_BLUEPRINT_AGGRESSIVE = [
  {
    symbol: "BTC",
    weight: 0.5,
    expected_return_pct: 32,
    expected_volatility_pct: 60,
  },
  {
    symbol: "ETH",
    weight: 0.3,
    expected_return_pct: 26,
    expected_volatility_pct: 52,
  },
  {
    symbol: "SOL",
    weight: 0.2,
    expected_return_pct: 42,
    expected_volatility_pct: 78,
  },
];

const DEFAULT_PORTFOLIO_POSITIONS = {
  SPY: 2_500_000,
  QQQ: 1_500_000,
  BTC: 1_200_000,
};

const DEFAULT_TARGET_ALLOCATIONS = {
  SPY: 0.35,
  QQQ: 0.25,
  BTC: 0.25,
  ETH: 0.15,
};

const ensureTextareaDefaults = (element, defaultValue) => {
  if (!element) return;
  const existing = element.value.trim();
  if (!existing) {
    element.value = typeof defaultValue === "string" ? defaultValue : JSON.stringify(defaultValue, null, 2);
  } else {
    element.value = existing;
  }
};

ensureTextareaDefaults(blueprintStableInput, DEFAULT_BLUEPRINT_STABLE);
ensureTextareaDefaults(blueprintAggressiveInput, DEFAULT_BLUEPRINT_AGGRESSIVE);
ensureTextareaDefaults(currentPositionsInput, DEFAULT_PORTFOLIO_POSITIONS);
ensureTextareaDefaults(targetAllocationsInput, DEFAULT_TARGET_ALLOCATIONS);

if (copilotQuestionInput && !copilotQuestionInput.value) {
  copilotQuestionInput.value = "지금 시장 전략을 요약해줘";
}

yearEl.textContent = new Date().getFullYear();

let chartInstance;
let liveChartInstance;
const paperSyncState = new Map();
const PAPER_STATUS_INTERVAL = 30_000;
const AUTOPILOT_STATUS_INTERVAL = 45_000;
let lastSimulationContext = {
  market: null,
  interval: null,
  useLiveData: false,
};
let autopilotNextCycleAt = null;
let lastAutopilotExecutionToken = null;
let lastTradeHistoryToken = null;

const createTradeSignature = (entry) => {
  if (!entry) return null;
  const executed = entry.executed_at ? new Date(entry.executed_at).getTime() : "";
  const market = (entry.market || "").toString().toUpperCase();
  const side = entry.side || "";
  const mode = entry.mode || "";
  const value = Number.isFinite(Number(entry.value))
    ? Number(entry.value).toFixed(6)
    : Number.isFinite(Number(entry.price))
    ? Number(entry.price).toFixed(6)
    : "";
  return [executed, market, side, mode, value].join("|");
};

const describeInterval = (value) => {
  const mapping = {
    minute1: "1분",
    minute3: "3분",
    minute5: "5분",
    minute15: "15분",
    minute30: "30분",
    minute60: "60분",
    minute240: "4시간",
    day: "일간",
    week: "주간",
    month: "월간",
  };
  return mapping[value] || value;
};

const ensureUppercase = (input) => {
  if (!input) return;
  const value = input.value || "";
  input.value = value.toUpperCase();
};

const renderMarketOptions = (markets = FALLBACK_MARKETS) => {
  if (!marketOptionsEl) return;
  const source = markets && markets.length ? markets : FALLBACK_MARKETS;
  marketOptionsEl.innerHTML = source
    .map((item) => {
      const marketCode = (item.market || "").toUpperCase();
      const label = item.korean_name || item.english_name || marketCode;
      return `<option value="${marketCode}">${marketCode} · ${label}</option>`;
    })
    .join("");
};

const MARKET_RESULTS_LIMIT = 24;

const describeMarketStatus = (market) => {
  if (market.trading_suspended) {
    return { text: "거래 중지", tone: "warning" };
  }
  if (market.market_warning && market.market_warning !== "NONE") {
    return { text: "투자 유의", tone: "warning" };
  }
  if (market.base_currency !== "KRW") {
    return { text: `${market.base_currency} 마켓`, tone: "ghost" };
  }
  return { text: "정상", tone: "ghost" };
};

const renderMarketGroups = (groups = []) => {
  if (!marketGroupsEl) return;
  if (!groups.length) {
    marketGroupsEl.innerHTML = "";
    return;
  }

  marketGroupsEl.innerHTML = groups
    .map((group) => {
      const isActive = selectedMarketGroup === group.key;
      const activeClass = isActive ? " chip--active" : "";
      return `
        <button type="button" class="chip chip--ghost${activeClass}" data-market-group="${group.key}" title="${group.description}">
          ${group.label}
          <span class="chip-count">${group.markets.length}</span>
        </button>
      `;
    })
    .join("");
};

const applyMarketToForms = (marketCode, target) => {
  if (!marketCode) return;
  if (target === "strategy" && strategyMarketInput) {
    strategyMarketInput.value = marketCode;
    ensureUppercase(strategyMarketInput);
    handleSimulation().catch(() => {});
  } else if (target === "paper" && paperStatusMarketInput) {
    paperStatusMarketInput.value = marketCode;
    ensureUppercase(paperStatusMarketInput);
    fetchPaperStatus().catch(() => {});
  } else if (target === "autopilot" && autopilotMarketInput) {
    autopilotMarketInput.value = marketCode;
    ensureUppercase(autopilotMarketInput);
    fetchAutopilotStatus().catch(() => {});
  } else if (target === "live" && liveMarketInput) {
    liveMarketInput.value = marketCode;
    ensureUppercase(liveMarketInput);
    refreshLiveMarket().catch(() => {});
  } else if (target === "order" && orderMarketInput) {
    orderMarketInput.value = marketCode;
    ensureUppercase(orderMarketInput);
  }
};

const marketMatchesFilters = (market, query) => {
  if (selectedBaseCurrency !== "ALL" && market.base_currency !== selectedBaseCurrency) {
    return false;
  }
  if (selectedMarketGroup) {
    const group = cachedMarketGroups.find((item) => item.key === selectedMarketGroup);
    if (group && !group.markets.includes(market.market)) {
      return false;
    }
  }
  if (!query) return true;
  const lowered = query.toLowerCase();
  return (
    market.market.toLowerCase().includes(lowered) ||
    (market.korean_name || "").toLowerCase().includes(lowered) ||
    (market.english_name || "").toLowerCase().includes(lowered) ||
    (market.quote_currency || "").toLowerCase().includes(lowered)
  );
};

const renderMarketResults = () => {
  if (!marketResultsEl) return;

  const query = (marketSearchInput?.value || "").trim();
  const source = cachedMarkets && cachedMarkets.length ? cachedMarkets : FALLBACK_MARKETS;
  const filtered = source.filter((item) => marketMatchesFilters(item, query));
  if (!filtered.length) {
    marketResultsEl.innerHTML = `
      <li class="market-result market-result--placeholder">
        <strong>조건에 맞는 마켓을 찾지 못했습니다.</strong>
        <span>검색어 또는 필터를 조정해 다시 시도해 주세요.</span>
      </li>
    `;
    return;
  }

  const limited = filtered.slice(0, MARKET_RESULTS_LIMIT);
  marketResultsEl.innerHTML = limited
    .map((market) => {
      const status = describeMarketStatus(market);
      const koreanLabel = market.korean_name || market.english_name || market.market;
      const englishLabel = market.english_name && market.english_name !== koreanLabel ? market.english_name : "";
      const warningBadge =
        market.market_warning && market.market_warning !== "NONE"
          ? '<span class="badge badge--warning">투자 유의</span>'
          : "";
      const suspendedBadge = market.trading_suspended
        ? '<span class="badge badge--danger">거래 중지</span>'
        : "";
      return `
        <li class="market-result">
          <div class="market-result__header">
            <strong>${market.market}</strong>
            <span>${koreanLabel}${englishLabel ? ` · ${englishLabel}` : ""}</span>
          </div>
          <div class="market-result__meta">
            <span class="badge badge--${status.tone}">${status.text}</span>
            ${warningBadge}${suspendedBadge}
          </div>
          <div class="market-result__actions">
            <button type="button" class="chip chip--ghost" data-market-action="strategy" data-market-code="${market.market}">전략</button>
            <button type="button" class="chip chip--ghost" data-market-action="paper" data-market-code="${market.market}">페이퍼</button>
            <button type="button" class="chip chip--ghost" data-market-action="autopilot" data-market-code="${market.market}">오토파일럿</button>
            <button type="button" class="chip chip--ghost" data-market-action="live" data-market-code="${market.market}">시황</button>
            <button type="button" class="chip chip--ghost" data-market-action="order" data-market-code="${market.market}">수동 주문</button>
          </div>
        </li>
      `;
    })
    .join("");
};


const describeRecommendationSource = (value) => {
  switch ((value || "").toLowerCase()) {
    case "upbit":
      return "업비트 실시간";
    case "upbit_stale":
      return "업비트 지연";
    case "synthetic":
      return "시뮬레이션 시세";
    case "mixed":
      return "실시간+시뮬레이션";
    default:
      return "데이터 확인 필요";
  }
};

const setRecommendationsLoading = (loading) => {
  if (!recommendationsPanelEl) return;
  recommendationsPanelEl.classList.toggle("panel--loading", Boolean(loading));
  if (recommendationsRefreshBtn) {
    if (loading) {
      recommendationsRefreshBtn.setAttribute("disabled", "true");
    } else {
      recommendationsRefreshBtn.removeAttribute("disabled");
    }
  }
};

const setRecommendationsError = (message) => {
  if (!recommendationsErrorsEl) return;
  const hasMessage = Boolean(message);
  recommendationsErrorsEl.textContent = message || "";
  recommendationsErrorsEl.classList.toggle("is-visible", hasMessage);
  if (hasMessage) {
    recommendationsPanelEl?.classList.add("panel--error");
  } else {
    recommendationsPanelEl?.classList.remove("panel--error");
  }
};

const renderRecommendationsList = (items = []) => {
  if (!recommendationsListEl) return;
  if (!items.length) {
    recommendationsListEl.innerHTML = `
      <li class="recommendation-card recommendation-card--placeholder">
        <strong>추천 결과가 없습니다.</strong>
        <span>필터를 변경하거나 잠시 후 다시 시도해 주세요.</span>
      </li>
    `;
    return;
  }

  recommendationsListEl.innerHTML = items
    .map((item, index) => {
      const englishName =
        item.english_name && item.english_name !== item.korean_name ? ` · ${item.english_name}` : "";
      const sentiment = formatPercentNumber(item.institutional_sentiment_pct);
      const breakout = formatPercentNumber(item.breakout_probability_pct);
      const trend = formatPercentNumber(item.trend_strength_pct);
      const volatility = formatPercentNumber(item.volatility_pct);
      const confidence = formatPercent(item.confidence_pct);
      const priceChange = formatPercentNumber(item.price_change_pct);
      return `
        <li class="recommendation-card">
          <div class="recommendation-card__header">
            <div>
              <span class="recommendation-rank">#${index + 1}</span>
              <strong>${item.market}</strong>
              <span class="recommendation-name">${item.korean_name}${englishName}</span>
            </div>
            <div class="recommendation-score">${ratioFormatter.format(item.score)}</div>
          </div>
          <div class="recommendation-card__meta">
            <span class="badge">${item.recommended_action}</span>
            <span>신뢰도 ${confidence}</span>
            <span>추세 ${trend}</span>
            <span>변동성 ${volatility}</span>
            <span>기관 ${sentiment}</span>
            <span>돌파 ${breakout}</span>
            <span>최근 변동 ${priceChange}</span>
            <span>종가 ${formatCurrency(item.last_price)} KRW</span>
          </div>
          <p class="recommendation-card__reason">${item.reason}</p>
          <p class="recommendation-card__summary">${item.summary}</p>
        </li>
      `;
    })
    .join("");
};

const updateRecommendationsMeta = (payload = {}) => {
  if (!recommendationsPanelEl) return;
  const baseCurrency = (payload.base_currency || recommendationsState.base || "KRW").toUpperCase();
  const interval = payload.interval || recommendationsState.interval || "minute60";
  if (recommendationsFilterEl) {
    const baseLabel = baseCurrency === "ALL" ? "전체 마켓" : `${baseCurrency} 마켓`;
    const intervalLabel = describeInterval(interval);
    recommendationsFilterEl.textContent = `${baseLabel} · ${intervalLabel}`;
  }
  if (recommendationsUpdatedEl) {
    if (payload.generated_at) {
      const generatedAt = new Date(payload.generated_at);
      if (!Number.isNaN(generatedAt.getTime())) {
        const relative = formatRelativeTime(generatedAt);
        recommendationsUpdatedEl.textContent = `${formatDateTime(generatedAt)} (${relative})`;
      } else {
        recommendationsUpdatedEl.textContent = "갱신 대기";
      }
    } else {
      recommendationsUpdatedEl.textContent = "갱신 대기";
    }
  }
  if (recommendationsSourceEl && payload.analysis_source) {
    recommendationsSourceEl.textContent = describeRecommendationSource(payload.analysis_source);
    recommendationsSourceEl.classList.toggle(
      "badge--ghost",
      payload.analysis_source === "synthetic"
    );
  }
};

const scheduleRecommendationsRefresh = (delay = 180000) => {
  if (recommendationsTimer) {
    clearTimeout(recommendationsTimer);
  }
  recommendationsTimer = window.setTimeout(() => {
    loadRecommendations().catch(() => {
      // swallow
    });
  }, delay);
};

const loadRecommendations = async (override = {}) => {
  if (!recommendationsListEl) return null;

  const nextBase = (
    override.base ||
    (selectedBaseCurrency ? selectedBaseCurrency : recommendationsState.base)
  ).toUpperCase();
  const nextInterval = override.interval || strategyIntervalSelect?.value || recommendationsState.interval;
  const nextLimit = override.limit || recommendationsState.limit || 5;

  recommendationsState = {
    ...recommendationsState,
    base: nextBase,
    interval: nextInterval,
    limit: nextLimit,
  };

  updateRecommendationsMeta({ base_currency: nextBase, interval: nextInterval });

  const params = new URLSearchParams({
    base: recommendationsState.base,
    interval: recommendationsState.interval,
    limit: String(recommendationsState.limit),
  });
  if (override.includeWarnings) {
    params.set("include_warnings", "true");
  }

  setRecommendationsLoading(true);
  setRecommendationsError("");
  recommendationsListEl.classList.add("is-loading");

  try {
    const payload = await requestApi(`/market/recommendations?${params.toString()}`);
    renderRecommendationsList(payload?.recommendations || []);
    updateRecommendationsMeta(payload);
    if (payload?.errors?.length) {
      setRecommendationsError(payload.errors.slice(0, 3).join(" | "));
    } else {
      setRecommendationsError("");
    }
    scheduleRecommendationsRefresh();
    return payload;
  } catch (error) {
    recommendationsListEl.innerHTML = `
      <li class="recommendation-card recommendation-card--placeholder">
        <strong>추천을 불러오지 못했습니다.</strong>
        <span>${error.message}</span>
      </li>
    `;
    setRecommendationsError(error.message);
    scheduleRecommendationsRefresh(90000);
    throw error;
  } finally {
    recommendationsListEl.classList.remove("is-loading");
    setRecommendationsLoading(false);
  }
};

const fetchMarketDirectory = async () => {
  renderMarketOptions(cachedMarkets);
  renderMarketGroups(cachedMarketGroups);
  renderMarketResults();
  setMarketDirectoryStatus("loading", "불러오는 중", "업비트 마켓 정보를 불러오는 중입니다.");
  try {
    const response = await requestApi("/market/list?only_krw=false");
    if (!response?.markets?.length) {
      setMarketDirectoryStatus(
        "warning",
        "데이터 확인 필요",
        "마켓 목록을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."
      );
      return;
    }
    cachedMarkets = response.markets.map((item) => ({
      market: (item.market || "").toUpperCase(),
      korean_name: item.korean_name || item.english_name || item.market,
      english_name: item.english_name || item.korean_name || item.market,
      base_currency: (item.base_currency || "KRW").toUpperCase(),
      quote_currency: (item.quote_currency || "").toUpperCase(),
      warning: item.market_warning,
      suspended: toBooleanFlag(item.trading_suspended),
      market_warning: (item.market_warning || "NONE").toUpperCase(),
      trading_suspended: toBooleanFlag(item.trading_suspended),
    }));
    cachedMarketGroups = Array.isArray(response.groups)
      ? response.groups.map((group) => ({
          key: group.key,
          label: group.label,
          description: group.description,
          markets: (group.markets || []).map((code) => code.toUpperCase()),
        }))
      : [];
    renderMarketOptions(cachedMarkets);
    renderMarketGroups(cachedMarketGroups);
    renderMarketResults();
    const statusRaw = (response.status || "").toLowerCase();
    const isLiveSource = response.source === "upbit";
    const state =
      statusRaw === "up"
        ? "online"
        : statusRaw === "down"
        ? "fallback"
        : isLiveSource
        ? "warning"
        : "fallback";
    const label =
      statusRaw === "up"
        ? "실시간 연동"
        : statusRaw === "down"
        ? "안전 모드"
        : "상태 점검 필요";
    let note = response.message || "";
    if (!note) {
      note = isLiveSource
        ? "업비트 실시간 데이터로 최신 목록을 표시합니다."
        : "업비트 연결이 원활하지 않아 내장 디렉터리를 사용 중입니다.";
    }
    const detail = response.detail || (response.backoff_seconds_remaining
      ? `재시도까지 약 ${Math.round(response.backoff_seconds_remaining)}초`
      : "");
    setMarketDirectoryStatus(state, label, note, {
      detail,
      checkedAt: response.checked_at || null,
    });
  } catch (error) {
    setMarketDirectoryStatus(
      "error",
      "연결 실패",
      error?.message || "업비트 API 응답을 확인할 수 없습니다.",
      { detail: "네트워크 상태를 다시 확인해 주세요." }
    );
    // Keep fallback options when live directory is unavailable.
  }
};

const initialApiBase = loadInitialApiBase();
buildApiBaseCandidates(initialApiBase);

const applyApiBase = (value, { persist = true, silent = false } = {}) => {
  const normalised = registerApiCandidate(value, { front: true });
  if (!normalised) {
    return;
  }
  apiBase = normalised;
  if (persist) {
    localStorage.setItem(STORAGE_KEY, normalised);
  }
  if (apiEndpointInput && apiEndpointInput.value !== normalised) {
    apiEndpointInput.value = normalised;
  }
  if (!silent) {
    refreshApiStatus();
    handleSimulation();
  }
};

const getApiBase = () => apiBase;

const setApiBase = (value) => {
  applyApiBase(value, { persist: true, silent: false });
};

applyApiBase(initialApiBase, { persist: false, silent: true });

if (apiEndpointInput && !apiEndpointInput.value) {
  apiEndpointInput.value = apiBase;
}

if (paperStatusIntervalSelect && !paperStatusIntervalSelect.value) {
  paperStatusIntervalSelect.value = "minute1";
}
if (strategyIntervalSelect && strategyIntervalSelect.value) {
  lastSimulationContext.interval = strategyIntervalSelect.value;
}
strategyIntervalSelect?.addEventListener("change", (event) => {
  const intervalValue = event.target.value;
  loadRecommendations({ interval: intervalValue }).catch(() => {});
});
if (strategyLiveDataInput) {
  lastSimulationContext.useLiveData = strategyLiveDataInput.checked;
}

const orderMarketInput = orderForm?.elements?.market || null;
const marketInputs = [
  paperStatusMarketInput,
  paperMarkMarketInput,
  strategyMarketInput,
  autopilotMarketInput,
  liveMarketInput,
  orderMarketInput,
];

marketInputs.forEach((input) => {
  if (!input) return;
  ensureUppercase(input);
  input.addEventListener("blur", () => ensureUppercase(input));
});

if (strategyMarketInput) {
  lastSimulationContext.market = strategyMarketInput.value;
}

renderMarketOptions();
renderMarketGroups(cachedMarketGroups);
renderMarketResults();
loadFallbackMarketAsset().catch(() => {});
fetchMarketDirectory().catch(() => {});
loadRecommendations().catch(() => {});

const setPaperHeartbeat = (state, message) => {
  if (paperHeartbeatEl) {
    paperHeartbeatEl.dataset.status = state;
    paperHeartbeatEl.textContent = message;
  }
  if (toplinePaperHeartbeatEl) {
    toplinePaperHeartbeatEl.dataset.status = state;
    toplinePaperHeartbeatEl.textContent = message;
  }
};

const updatePaperSourceLabel = (balance) => {
  if (!paperPriceSourceEl) return;
  if (!balance || !balance.price_source) {
    paperPriceSourceEl.textContent = "데이터 출처 확인 중";
    paperPriceSourceEl.classList.remove("status-note--highlight", "status-note--warning");
    paperPriceSourceEl.classList.add("muted");
    return;
  }

  const label = PAPER_SOURCE_LABELS[balance.price_source] || PAPER_SOURCE_LABELS.manual;
  paperPriceSourceEl.textContent = label;
  paperPriceSourceEl.classList.remove("muted", "status-note--highlight", "status-note--warning");
  if (balance.price_source === "upbit") {
    paperPriceSourceEl.classList.add("status-note--highlight");
  } else if (balance.price_source === "upbit_stale") {
    paperPriceSourceEl.classList.add("status-note--warning");
  } else if (balance.price_source === "synthetic") {
    paperPriceSourceEl.classList.add("status-note--warning");
  } else {
    paperPriceSourceEl.classList.add("muted");
  }
};

const updateChatTestStatus = (state, message) => {
  if (!chatTestStatusEl) return;
  chatTestStatusEl.dataset.status = state;
  chatTestStatusEl.textContent = message;
};

const setMarketDirectoryStatus = (state, label, note = "", extra) => {
  if (!marketDirectoryStatusEl) return;
  const pill = marketDirectoryStatusEl.querySelector(".status-pill");
  if (pill) {
    pill.dataset.status = state;
    pill.textContent = label;
  }
  if (marketDirectoryNoteEl) {
    const detailText = extra?.detail ? String(extra.detail) : "";
    const checkedAt = extra?.checkedAt ? new Date(extra.checkedAt) : null;
    const hasCheckedAt = checkedAt && !Number.isNaN(checkedAt.getTime());
    const parts = [];
    if (note) {
      parts.push(note);
    }
    if (detailText) {
      parts.push(detailText);
    }
    if (hasCheckedAt) {
      parts.push(
        `마지막 확인 ${formatDateTime(checkedAt)} (${formatRelativeTime(checkedAt)})`
      );
    }
    if (parts.length) {
      marketDirectoryNoteEl.textContent = parts.join(" · ");
      marketDirectoryNoteEl.classList.remove("muted");
      if (["fallback", "error", "warning"].includes(state)) {
        marketDirectoryNoteEl.classList.add("status-note--warning");
      } else {
        marketDirectoryNoteEl.classList.remove("status-note--warning");
      }
    } else {
      marketDirectoryNoteEl.textContent = "";
      marketDirectoryNoteEl.classList.add("muted");
      marketDirectoryNoteEl.classList.remove("status-note--warning");
    }
  }
  if (marketDirectoryStatusEl) {
    const detailText = extra?.detail ? String(extra.detail) : "";
    marketDirectoryStatusEl.dataset.detail = detailText;
  }
};

const formatDateTime = (date) =>
  date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

const formatRelativeTime = (date) => {
  const diffMs = Date.now() - date.getTime();
  if (diffMs < 45_000) return "방금 전";
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 60) return `${minutes}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  return `${days}일 전`;
};

const formatCountdown = (value) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const diffMs = date.getTime() - Date.now();
  if (diffMs <= 0) {
    return "곧 실행";
  }
  const minutes = Math.floor(diffMs / 60_000);
  if (minutes < 1) {
    return "1분 이내";
  }
  if (minutes < 60) {
    return `${minutes}분 후`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const remainingMinutes = minutes % 60;
    return remainingMinutes
      ? `${hours}시간 ${remainingMinutes}분 후`
      : `${hours}시간 후`;
  }
  const days = Math.floor(hours / 24);
  return `${days}일 후`;
};

const formatShortTime = (value) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
};

const renderList = (element, items, placeholder) => {
  if (!element) return;
  element.innerHTML = "";
  if (!items || !items.length) {
    const li = document.createElement("li");
    li.className = "copilot-list__placeholder";
    li.textContent = placeholder;
    element.appendChild(li);
    return;
  }
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    element.appendChild(li);
  });
};

const updateCopilotRiskLabel = (value) => {
  if (!copilotRiskLabel) return;
  const numeric = Number(value);
  let profile = "균형형";
  if (numeric <= 0.3) profile = "안정형";
  else if (numeric >= 0.7) profile = "공격형";
  copilotRiskLabel.textContent = `${profile} (${numeric.toFixed(2)})`;
};

const updateEquityNote = (values) => {
  if (!equityNoteEl) return;

  if (!Array.isArray(values) || values.length === 0) {
    equityNoteEl.innerHTML =
      "<strong>시뮬레이션 대기 중</strong><span>EMA 전략을 실행하면 자본 곡선의 변동폭과 상승·하락 구간이 여기에 표시됩니다.</span>";
    return;
  }

  const numericValues = values.map((value) => Number(value));
  if (numericValues.some((value) => !Number.isFinite(value))) {
    equityNoteEl.innerHTML =
      "<strong>에퀴티 데이터를 해석할 수 없습니다.</strong><span>전략을 다시 실행해 정확한 곡선을 생성해주세요.</span>";
    return;
  }

  const start = numericValues[0];
  const end = numericValues[numericValues.length - 1];
  const high = Math.max(...numericValues);
  const low = Math.min(...numericValues);
  const change = end - start;
  const changePct = start !== 0 ? (change / start) * 100 : 0;
  const direction = change >= 0 ? "상승" : "하락";
  const arrow = change >= 0 ? "▲" : "▼";
  const summary = `${direction} ${formatPercent(Math.abs(changePct))} · 최고 ${formatCurrency(high)} KRW · 최저 ${formatCurrency(low)} KRW · ${values.length}봉 누적`;
  const contextBits = [];
  if (lastSimulationContext.market) {
    contextBits.push(`마켓 ${lastSimulationContext.market}`);
  }
  if (lastSimulationContext.interval) {
    contextBits.push(`${describeInterval(lastSimulationContext.interval)} 캔들`);
  }
  const sourceLabel = lastSimulationContext.useLiveData ? "Upbit 실시간 데이터" : "시뮬레이션 데이터";
  contextBits.push(sourceLabel);
  const contextSummary = contextBits.join(" · ");

  equityNoteEl.innerHTML = `
    <strong>${formatCurrency(start)} KRW → ${formatCurrency(end)} KRW ${arrow}</strong>
    <span>${summary}</span>
    <small>${contextSummary}</small>
  `;
};

const updatePaperHeartbeat = (balance) => {
  if (!paperHeartbeatEl) return;
  updatePaperSourceLabel(balance);

  if (!balance) {
    setPaperHeartbeat("offline", "상태 미확인");
    return;
  }

  const updated = balance.last_updated ? new Date(balance.last_updated) : null;
  const hasValidTimestamp = updated && !Number.isNaN(updated.getTime());
  const relative = hasValidTimestamp ? formatRelativeTime(updated) : "시간 확인 필요";

  const state = balance.heartbeat_state || "offline";
  let message;
  switch (balance.heartbeat_reason) {
    case "live":
      message = `실시간 연동 (${relative})`;
      break;
    case "delayed":
      message = `연동 지연 (${relative})`;
      break;
    case "synthetic":
      message = `시뮬레이션 시세 (${relative})`;
      break;
    case "manual":
      message = `수동 동기화 (${relative})`;
      break;
    case "stale":
      message = `지연된 실시간 (${relative})`;
      break;
    default:
      message = `상태 확인 필요 (${relative})`;
      break;
  }

  if (!hasValidTimestamp) {
    message = "업데이트 시간 확인 필요";
  }

  setPaperHeartbeat(state, message);
};

const updateApiStatus = (state, message) => {
  if (!apiStatusEl) return;
  apiStatusEl.dataset.status = state;
  apiStatusEl.textContent = message;
};

const formatRatio = (value) =>
  Number.isFinite(value) && Math.abs(value) !== Infinity
    ? ratioFormatter.format(value)
    : value > 0
    ? "∞"
    : "0.00";

const parseNumeric = (value) => {
  if (value === undefined || value === null) {
    return null;
  }
  const trimmed = String(value).trim();
  if (!trimmed) {
    return null;
  }
  const numeric = Number(trimmed);
  return Number.isFinite(numeric) ? numeric : null;
};

function applyDefaultCapitalValues() {
  const defaultValue = String(DEFAULT_CAPITAL_KRW);
  const targets = [
    paperInitialCashInput,
    autopilotCapitalInput,
    copilotCapitalInput,
    aiCapitalEl,
    document.querySelector('input[name="initial_capital"]'),
    document.getElementById("blueprint-capital"),
    document.getElementById("portfolio-value"),
  ];

  const seen = new Set();
  targets.forEach((element) => {
    if (!element || seen.has(element)) {
      return;
    }
    seen.add(element);
    if (element instanceof HTMLInputElement) {
      const current = parseNumeric(element.value);
      const defaultNumeric = parseNumeric(element.defaultValue);
      if (current === null || current === defaultNumeric) {
        element.value = defaultValue;
      }
    }
  });
}

function setupCollapsible(element, limit) {
  if (!element) {
    return;
  }

  const resolvedLimit = Number.isFinite(limit) && limit > 0 ? limit : COLLAPSIBLE_DEFAULT_LIMIT;
  element.style.setProperty("--collapsible-max-height", `${resolvedLimit}px`);

  if (collapsibleMetadata.has(element)) {
    const meta = collapsibleMetadata.get(element);
    if (meta) {
      meta.limit = resolvedLimit;
      element.style.setProperty("--collapsible-max-height", `${meta.limit}px`);
      meta.update?.();
    }
    return;
  }

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "btn btn--ghost btn--compact collapsible-toggle";

  const metadata = {
    limit: resolvedLimit,
    toggle,
    update: null,
    mutationObserver: null,
    resizeObserver: null,
  };

  const update = () => {
    const threshold = metadata.limit + 12;
    const shouldCollapse = element.scrollHeight > threshold;
    if (!shouldCollapse) {
      element.setAttribute("data-collapsed", "false");
      toggle.style.display = "none";
      toggle.setAttribute("aria-expanded", "true");
      return;
    }

    const collapsed = element.getAttribute("data-collapsed") !== "false";
    element.style.setProperty("--collapsible-max-height", `${metadata.limit}px`);
    toggle.style.display = "inline-flex";
    toggle.textContent = collapsed ? "더 보기" : "간단히";
    toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  };

  metadata.update = update;

  toggle.addEventListener("click", () => {
    const collapsed = element.getAttribute("data-collapsed") !== "false";
    element.setAttribute("data-collapsed", collapsed ? "false" : "true");
    update();
  });

  element.parentNode?.insertBefore(toggle, element.nextSibling);
  element.setAttribute("data-collapsed", "true");

  const mutationObserver = new MutationObserver(update);
  mutationObserver.observe(element, { childList: true, subtree: true, characterData: true });
  metadata.mutationObserver = mutationObserver;

  let resizeObserver = null;
  if (typeof ResizeObserver !== "undefined") {
    resizeObserver = new ResizeObserver(update);
    resizeObserver.observe(element);
  }
  metadata.resizeObserver = resizeObserver;

  collapsibleMetadata.set(element, metadata);
  update();
}

function initCollapsibles() {
  document.querySelectorAll(COLLAPSIBLE_SELECTOR).forEach((element) => {
    const limitAttr = Number(element.getAttribute("data-collapsible"));
    setupCollapsible(element, limitAttr);
  });
}

const renderLiveChart = (candles) => {
  if (!liveChartCanvas || !candles?.length) return;
  const context = liveChartCanvas.getContext("2d");
  const labels = candles.map((item) =>
    new Date(item.timestamp).toLocaleString("ko-KR", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    })
  );
  const data = candles.map((item) => item.close);

  const dataset = {
    labels,
    datasets: [
      {
        label: "종가",
        data,
        borderColor: "#7ae1ff",
        backgroundColor: "rgba(122, 225, 255, 0.18)",
        tension: 0.2,
        fill: true,
      },
    ],
  };

  if (liveChartInstance) {
    liveChartInstance.data = dataset;
    liveChartInstance.update();
    return;
  }

  liveChartInstance = new Chart(context, {
    type: "line",
    data: dataset,
    options: {
      responsive: true,
      plugins: {
        legend: { display: false },
      },
      scales: {
        x: {
          ticks: { color: "#98a1c3" },
          grid: { color: "rgba(122, 225, 255, 0.08)" },
        },
        y: {
          ticks: { color: "#98a1c3" },
          grid: { color: "rgba(122, 225, 255, 0.08)" },
        },
      },
    },
  });
};

const updateLiveInsights = (insights, source) => {
  if (!insights) return;
  if (liveEmaFastEl) liveEmaFastEl.textContent = formatCurrency(insights.ema_fast);
  if (liveEmaSlowEl) liveEmaSlowEl.textContent = formatCurrency(insights.ema_slow);
  if (liveEmaSignalEl) liveEmaSignalEl.textContent = insights.ema_signal;
  if (liveRsiEl) liveRsiEl.textContent = ratioFormatter.format(insights.rsi);
  if (liveMacdEl) liveMacdEl.textContent = insights.macd.toFixed(3);
  if (liveMacdHistEl) liveMacdHistEl.textContent = insights.macd_histogram.toFixed(3);
  if (liveVolatilityEl) liveVolatilityEl.textContent = formatPercent(insights.volatility_pct);
  if (liveTrendEl) liveTrendEl.textContent = `${ratioFormatter.format(insights.trend_strength)}%`;
  if (liveActionEl) liveActionEl.textContent = insights.recommended_action;
  if (liveRegimeEl) liveRegimeEl.textContent = `${insights.regime}`;
  if (liveConfidenceEl)
    liveConfidenceEl.textContent = `신뢰도 ${ratioFormatter.format(insights.confidence_pct)}%`;
  if (liveSummaryEl) liveSummaryEl.textContent = insights.insight_summary;
  if (liveUpdatedEl)
    liveUpdatedEl.textContent = `${new Date(insights.latest_timestamp).toLocaleString("ko-KR")}`;
  if (liveSourceEl)
    liveSourceEl.textContent =
      source === "synthetic"
        ? "시뮬레이터 데이터"
        : source === "upbit_stale"
        ? "업비트 지연 데이터"
        : "업비트 실시간";
  if (toplineLiveStatusEl) {
    const state =
      source === "upbit"
        ? "online"
        : source === "upbit_stale"
        ? "warning"
        : source === "synthetic"
        ? "warning"
        : "loading";
    const label =
      source === "upbit"
        ? "실시간"
        : source === "upbit_stale"
        ? "지연"
        : source === "synthetic"
        ? "시뮬레이션"
        : "동기화";
    toplineLiveStatusEl.dataset.status = state;
    toplineLiveStatusEl.textContent = label;
  }
  if (toplineLiveNoteEl) {
    const updated = insights.latest_timestamp ? new Date(insights.latest_timestamp) : null;
    const relative = updated && !Number.isNaN(updated.getTime()) ? formatRelativeTime(updated) : "시간 확인 필요";
    const marketLabel = insights.market || liveMarketInput?.value || "KRW-BTC";
    const intervalLabel = insights.interval ? describeInterval(insights.interval) : describeInterval(liveIntervalSelect?.value || "minute1");
    toplineLiveNoteEl.textContent = `${marketLabel} · ${intervalLabel} · ${relative}`;
    toplineLiveNoteEl.classList.add("muted");
  }
};

const syncPaperWithLivePrice = async (market, price, latestTimestamp, source) => {
  if (!paperSummaryEl || !market || (source !== "upbit" && source !== "upbit_stale")) return;
  const numericPrice = Number(price);
  if (!Number.isFinite(numericPrice) || numericPrice <= 0) return;
  const marketKey = market.toUpperCase();
  const timestampKey =
    typeof latestTimestamp === "string"
      ? latestTimestamp
      : new Date(latestTimestamp).toISOString();

  if (paperSyncState.get(marketKey) === timestampKey) {
    return;
  }

  try {
    const balance = await requestApi("/trading/paper/mark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ market: marketKey, price: numericPrice }),
    });
    paperSyncState.set(marketKey, timestampKey);
    updatePaperSummary(balance);
  } catch (error) {
    setPaperHeartbeat("warning", "시세 동기화 실패");
  }
};

const refreshLiveMarket = async () => {
  if (!liveMarketInput || !liveIntervalSelect) return;
  const market = liveMarketInput.value.trim() || "KRW-BTC";
  const interval = liveIntervalSelect.value || "minute1";
  try {
    const candleResponse = await requestApi(
      `/market/upbit/candles?market=${encodeURIComponent(market)}&interval=${interval}&count=160`
    );
    renderLiveChart(candleResponse.candles);
    const insightResponse = await requestApi(
      `/market/upbit/insights?market=${encodeURIComponent(market)}&interval=${interval}&count=200`
    );
    const source = insightResponse.source || candleResponse.source;
    updateLiveInsights(insightResponse, source);
    await syncPaperWithLivePrice(market, insightResponse.latest_close, insightResponse.latest_timestamp, source);
    refreshMarketIntelligence(market, interval).catch(() => {});
  } catch (error) {
    if (liveSummaryEl) {
      liveSummaryEl.textContent = error.message;
    }
    if (liveSourceEl) {
      liveSourceEl.textContent = "연결 실패";
    }
    if (toplineLiveStatusEl) {
      toplineLiveStatusEl.dataset.status = "offline";
      toplineLiveStatusEl.textContent = "연결 실패";
    }
    if (toplineLiveNoteEl) {
      toplineLiveNoteEl.textContent = error.message || "시세 데이터를 불러오지 못했습니다.";
      toplineLiveNoteEl.classList.remove("muted");
    }
  }
};

const renderMarketIntelligence = (insight) => {
  if (!aiActionEl) return;
  aiActionEl.textContent = insight.recommended_action;
  aiConfidenceEl.textContent = formatPercent(insight.confidence_pct);
  aiRegimeEl.textContent = insight.regime;
  aiSummaryEl.textContent = insight.summary;
  if (aiInstitutionalEl) {
    aiInstitutionalEl.textContent = `기관 신뢰도 ${formatPercent(
      insight.institutional_confidence_pct
    )} · ${insight.institutional_commentary}`;
  }

  if (aiConfluenceScoreEl) {
    aiConfluenceScoreEl.textContent = `${ratioFormatter.format(
      insight.technical_confluence.score
    )}점`;
  }
  if (aiConfluenceLabelEl) {
    const label = insight.technical_confluence.label;
    const volume = ratioFormatter.format(
      insight.technical_confluence.volume_confirmation
    );
    aiConfluenceLabelEl.textContent = `${label} · 거래량 확인 ${volume}x`;
  }
  if (aiConfluenceDriversEl) {
    aiConfluenceDriversEl.innerHTML = "";
    if (!insight.technical_confluence.drivers.length) {
      const li = document.createElement("li");
      li.textContent = "추가 컨플루언스 신호가 없습니다.";
      aiConfluenceDriversEl.appendChild(li);
    } else {
      insight.technical_confluence.drivers.forEach((driver) => {
        const li = document.createElement("li");
        li.textContent = driver;
        aiConfluenceDriversEl.appendChild(li);
      });
    }
  }

  if (aiSignalsEl) {
    aiSignalsEl.innerHTML = "";
    insight.signals.forEach((signal) => {
      const li = document.createElement("li");
      li.textContent = signal;
      aiSignalsEl.appendChild(li);
    });
  }

  if (aiStopEl) aiStopEl.textContent = formatPercent(insight.risk.stop_loss_pct);
  if (aiTakeEl) aiTakeEl.textContent = formatPercent(insight.risk.take_profit_pct);
  if (aiTrailingEl)
    aiTrailingEl.textContent =
      insight.risk.trailing_stop_pct !== null && insight.risk.trailing_stop_pct !== undefined
        ? formatPercent(insight.risk.trailing_stop_pct)
        : "-";
  if (aiSizeEl) aiSizeEl.textContent = formatPercent(insight.risk.position_size_pct);
  if (aiRiskNoteEl) aiRiskNoteEl.textContent = insight.risk.confidence_note;
  if (aiRiskNotesEl) {
    aiRiskNotesEl.innerHTML = "";
    insight.risk.notes.forEach((note) => {
      const li = document.createElement("li");
      li.textContent = note;
      aiRiskNotesEl.appendChild(li);
    });
  }

  if (aiNewsEl) {
    aiNewsEl.innerHTML = "";
    if (!insight.news.length) {
      const li = document.createElement("li");
      li.className = "ai-news__placeholder";
      li.textContent = "AI가 참고할 기관 뉴스가 없습니다.";
      aiNewsEl.appendChild(li);
    } else {
      insight.news.forEach((item) => {
        const li = document.createElement("li");
        const link = document.createElement("a");
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noreferrer";
        link.textContent = item.title;
        const meta = document.createElement("span");
        meta.className = "ai-news__meta";
        meta.textContent = `${item.source} · ${item.published_at}`;
        li.appendChild(link);
        li.appendChild(meta);
        aiNewsEl.appendChild(li);
      });
    }
  }

  const consensus = insight.timeframe_consensus;
  if (consensus && aiConsensusDominantEl && aiConsensusAgreementEl && aiConsensusDetailsEl) {
    aiConsensusDominantEl.textContent = consensus.dominant_trend;
    aiConsensusAgreementEl.textContent = `일치율 ${formatPercent(consensus.agreement_pct)}`;
    aiConsensusDetailsEl.innerHTML = "";
    if (Array.isArray(consensus.details) && consensus.details.length) {
      consensus.details.forEach((detail) => {
        const li = document.createElement("li");
        li.textContent = detail;
        aiConsensusDetailsEl.appendChild(li);
      });
    } else {
      aiConsensusDetailsEl.innerHTML = '<li class="ai-consensus__placeholder">컨센서스 세부 정보가 없습니다.</li>';
    }
  }
};

const refreshMarketIntelligence = async (marketOverride, intervalOverride) => {
  if (!aiActionEl) return;
  const market = (marketOverride || liveMarketInput?.value || "KRW-BTC").trim().toUpperCase();
  const interval = intervalOverride || liveIntervalSelect?.value || "minute60";
  try {
    const insight = await requestApi(
      `/ai/market/intelligence?market=${encodeURIComponent(market)}&interval=${interval}&count=200`
    );
    renderMarketIntelligence(insight);
  } catch (error) {
    aiActionEl.textContent = "AI 분석 실패";
    if (aiSummaryEl) aiSummaryEl.textContent = error.message;
  }
};

const setAutopilotBadge = (bias) => {
  if (!autopilotBiasEl) return;
  autopilotBiasEl.classList.remove("badge--long", "badge--short", "badge--neutral");
  let label = "관망";
  let badgeClass = "badge--neutral";
  if (bias === "long") {
    label = "롱 바이어스";
    badgeClass = "badge--long";
  } else if (bias === "short") {
    label = "숏 바이어스";
    badgeClass = "badge--short";
  }
  autopilotBiasEl.classList.add(badgeClass);
  autopilotBiasEl.textContent = label;
};

const renderAutopilotPlan = (plan) => {
  if (!autopilotSideEl) return;

  if (!plan) {
    setAutopilotBadge("neutral");
    autopilotSideEl.textContent = "관망";
    autopilotConfidenceEl.textContent = "-";
    autopilotSizeEl.textContent = "-";
    autopilotStopsEl.textContent = "- / -";
    autopilotTrailingEl.textContent = "-";
    renderList(autopilotReasoningEl, [], "자동 신호를 수집하고 있습니다.");
    renderList(autopilotMonitoringEl, [], "시장 데이터를 준비 중입니다.");
    return;
  }

  setAutopilotBadge(plan.bias);
  autopilotSideEl.textContent =
    plan.side === "bid" ? "매수" : plan.side === "ask" ? "매도" : "관망";
  autopilotConfidenceEl.textContent = formatPercent(plan.confidence_pct);
  const sizeValue = Number.isFinite(plan.position_size_pct)
    ? plan.position_size_pct
    : 0;
  autopilotSizeEl.textContent = formatPercent(sizeValue);
  const stopLoss = plan.stop_loss_pct ? formatPercent(plan.stop_loss_pct) : "-";
  const takeProfit = plan.take_profit_pct ? formatPercent(plan.take_profit_pct) : "-";
  autopilotStopsEl.textContent = `${stopLoss} / ${takeProfit}`;
  autopilotTrailingEl.textContent =
    plan.trailing_stop_pct !== null && plan.trailing_stop_pct !== undefined
      ? formatPercent(plan.trailing_stop_pct)
      : "-";
  renderList(autopilotReasoningEl, plan.reasoning, "근거 데이터가 없습니다.");
  renderList(autopilotMonitoringEl, plan.monitoring, "모니터링 항목이 비어 있습니다.");
};

const renderAutopilotHoldReason = (status) => {
  if (!autopilotHoldReasonEl) return;
  const reason = status?.last_skip_reason;
  autopilotHoldReasonEl.classList.remove("autopilot-note--active");

  if (reason) {
    autopilotHoldReasonEl.textContent = reason;
    autopilotHoldReasonEl.classList.remove("muted");
    autopilotHoldReasonEl.classList.add("autopilot-note--active");
    return;
  }

  if (status?.running) {
    autopilotHoldReasonEl.textContent = "AI가 다음 매매 기회를 탐색 중입니다.";
    autopilotHoldReasonEl.classList.add("muted");
  } else {
    autopilotHoldReasonEl.textContent =
      "오토파일럿을 시작하면 관망 사유와 진행 상태가 여기에 표시됩니다.";
    autopilotHoldReasonEl.classList.add("muted");
  }
};

const renderAutopilotLogs = (logs) => {
  if (!autopilotLogList) return;
  autopilotLogList.innerHTML = "";
  if (!logs || !logs.length) {
    autopilotLogList.innerHTML = '<li class="autopilot-log__placeholder">로그가 준비 중입니다.</li>';
    return;
  }
  logs
    .slice()
    .reverse()
    .forEach((entry) => {
      const li = document.createElement("li");
      const timeEl = document.createElement("time");
      timeEl.textContent = formatShortTime(entry.timestamp);
      const messageEl = document.createElement("span");
      const level = entry.level ? entry.level.toUpperCase() : "INFO";
      messageEl.textContent = `[${level}] ${entry.message}`;
      li.appendChild(timeEl);
      li.appendChild(messageEl);
      autopilotLogList.appendChild(li);
    });
};

const renderAutopilotStatus = (status) => {
  if (!autopilotStateEl) return;
  const running = Boolean(status?.running);
  const hasError = Boolean(status?.last_error);
  const stateLabel = running ? (hasError ? "주의" : "운영 중") : "대기";
  const pillState = running ? (hasError ? "warning" : "online") : "offline";
  autopilotStateEl.dataset.status = pillState;
  autopilotStateEl.textContent = stateLabel;

  autopilotNextCycleAt = status?.next_cycle_due_at ? new Date(status.next_cycle_due_at) : null;
  updateAutopilotCountdown();

  if (toplineAutopilotStatusEl) {
    toplineAutopilotStatusEl.dataset.status = pillState;
    toplineAutopilotStatusEl.textContent = stateLabel;
  }

  if (autopilotLastRunEl) {
    autopilotLastRunEl.textContent = status?.last_cycle_started_at
      ? formatDateTime(new Date(status.last_cycle_started_at))
      : "-";
  }

  if (autopilotLastCompletedEl) {
    autopilotLastCompletedEl.textContent = status?.last_cycle_completed_at
      ? formatDateTime(new Date(status.last_cycle_completed_at))
      : "-";
  }

  if (autopilotLastErrorEl) {
    autopilotLastErrorEl.textContent = status?.last_error || "";
    autopilotLastErrorEl.classList.toggle("muted", !status?.last_error);
  }

  if (toplineAutopilotNoteEl) {
    const noteParts = [];
    if (status?.config?.market) {
      noteParts.push(status.config.market);
    }
    if (status?.config?.interval) {
      noteParts.push(describeInterval(status.config.interval));
    }
    if (Array.isArray(status?.recent_recommendations) && status.recent_recommendations.length) {
      noteParts.push(`추천 ${status.recent_recommendations[0]}`);
    }
    if (status?.last_cycle_started_at) {
      noteParts.push(`최근 ${formatRelativeTime(new Date(status.last_cycle_started_at))}`);
    } else if (running) {
      noteParts.push("초기 분석 준비 중");
    } else if (status?.last_error) {
      noteParts.push(status.last_error);
    } else {
      noteParts.push("최근 실행 정보 없음");
    }
    if (status?.next_cycle_due_at) {
      const countdown = formatCountdown(status.next_cycle_due_at);
      if (countdown) {
        noteParts.push(`다음 ${countdown}`);
      }
    }
    toplineAutopilotNoteEl.textContent = noteParts.join(" · ");
    toplineAutopilotNoteEl.classList.toggle("muted", noteParts.length === 0);
  }

  if (status?.config) {
    if (autopilotModeSelect) autopilotModeSelect.value = status.config.mode;
    if (autopilotMarketInput) autopilotMarketInput.value = status.config.market;
    if (autopilotIntervalSelect) autopilotIntervalSelect.value = status.config.interval;
    if (autopilotRecommendationIntervalSelect)
      autopilotRecommendationIntervalSelect.value =
        status.config.recommendation_interval || status.config.interval;
    if (autopilotAutoMarketInput)
      autopilotAutoMarketInput.checked = Boolean(status.config.auto_select_market);
    if (autopilotBaseSelect && status.config.recommendation_base)
      autopilotBaseSelect.value = status.config.recommendation_base;
    if (autopilotMaxMarketsInput && status.config.recommendation_max_markets)
      autopilotMaxMarketsInput.value = status.config.recommendation_max_markets;
    if (autopilotIncludeWarningsInput)
      autopilotIncludeWarningsInput.checked = Boolean(
        status.config.recommendation_include_warnings,
      );
    if (autopilotRiskInput) {
      autopilotRiskInput.value = status.config.risk_appetite;
      updateAutopilotRiskLabel(status.config.risk_appetite);
    }
    if (autopilotCapitalInput) autopilotCapitalInput.value = status.config.capital;
    if (autopilotPollInput) autopilotPollInput.value = status.config.poll_interval;
    if (autopilotMaxPositionInput) autopilotMaxPositionInput.value = status.config.max_position_pct;
    if (autopilotConfidenceInput)
      autopilotConfidenceInput.value = status.config.min_confidence_pct;
    if (autopilotIncludePortfolioInput)
      autopilotIncludePortfolioInput.checked = Boolean(status.config.include_portfolio);
  }

  if (autopilotLastTradeEl) {
    if (status?.last_execution) {
      const exec = status.last_execution;
      const direction = exec.side === "bid" ? "매수" : "매도";
      const modeLabel = exec.mode === "live" ? "실거래" : "페이퍼";
      const executedAt = formatDateTime(new Date(exec.executed_at));
      const detail = exec.detail ? ` · ${exec.detail}` : "";
      autopilotLastTradeEl.textContent = `${executedAt} · ${modeLabel} · ${exec.market} ${direction} ${ratioFormatter.format(
        exec.volume,
      )} @ ${formatCurrency(exec.price)} KRW${detail}`;
    } else {
      autopilotLastTradeEl.textContent = "실행 내역이 없습니다.";
    }
  }

  if (Array.isArray(status?.recent_executions) && status.recent_executions.length) {
    const latestExecution = status.recent_executions[0];
    const executionToken = createTradeSignature(latestExecution);
    if (executionToken && executionToken !== lastAutopilotExecutionToken) {
      lastAutopilotExecutionToken = executionToken;
      if (executionToken !== lastTradeHistoryToken) {
        refreshTradeHistory().catch(() => {});
      }
    }
  }

  renderAutopilotLogs(status?.logs || []);
  renderAutopilotPlan(status?.last_plan || null);
  renderAutopilotHoldReason(status);
  if (autopilotRecommendationsList) {
    renderList(
      autopilotRecommendationsList,
      status?.recent_recommendations,
      "AI 추천이 아직 없습니다.",
    );
  }
  if (autopilotCandidatesEl) {
    const preview = Array.isArray(status?.candidate_markets)
      ? status.candidate_markets.slice(0, 6)
      : [];
    const candidateCount = Number(
      status?.candidate_market_count ?? status?.candidate_markets?.length ?? 0
    );
    if (candidateCount > 0) {
      const extraCount = Math.max(0, candidateCount - preview.length);
      const suffix = extraCount ? ` 외 ${extraCount}종목` : "";
      autopilotCandidatesEl.textContent = `분석 후보 (${candidateCount}): ${preview.join(", ")}${suffix}`;
      autopilotCandidatesEl.classList.remove("muted");
    } else {
      autopilotCandidatesEl.textContent = "분석 후보: -";
      autopilotCandidatesEl.classList.add("muted");
    }
  }
  if (autopilotAnalysisEl) {
    const batch = Array.isArray(status?.analysis_markets)
      ? status.analysis_markets.slice(0, 6)
      : [];
    const analysedCount = Number(
      status?.analysis_market_count ?? status?.analysis_markets?.length ?? 0
    );
    const totalCandidates = Number(
      status?.candidate_market_count ?? status?.candidate_markets?.length ?? analysedCount
    );
    if (analysedCount > 0) {
      const extraBatch = Math.max(0, analysedCount - batch.length);
      const suffix = extraBatch ? ` 외 ${extraBatch}종목` : "";
      const ratioLabel = totalCandidates > 0 ? `${analysedCount}/${totalCandidates}` : `${analysedCount}`;
      autopilotAnalysisEl.textContent = `이번 사이클 분석 (${ratioLabel}): ${batch.join(", ")}${suffix}`;
      autopilotAnalysisEl.classList.remove("muted");
    } else {
      autopilotAnalysisEl.textContent = "이번 사이클 분석: -";
      autopilotAnalysisEl.classList.add("muted");
    }
  }
  if (autopilotRecommendationSourceEl) {
    if (status?.recommendation_source) {
      autopilotRecommendationSourceEl.textContent = `분석 출처: ${status.recommendation_source}`;
      autopilotRecommendationSourceEl.classList.remove("muted");
    } else {
      autopilotRecommendationSourceEl.textContent = "분석 출처: -";
      autopilotRecommendationSourceEl.classList.add("muted");
    }
  }
};

const updateAutopilotCountdown = () => {
  if (!autopilotNextCycleAt) {
    if (autopilotNextCountdownEl) {
      autopilotNextCountdownEl.textContent = "예정 정보 없음";
      autopilotNextCountdownEl.classList.add("muted");
    }
    if (toplineAutopilotCountdownEl) {
      toplineAutopilotCountdownEl.textContent = "예정 정보 없음";
      toplineAutopilotCountdownEl.classList.add("muted");
    }
    return;
  }

  const countdown = formatCountdown(autopilotNextCycleAt);
  if (autopilotNextCountdownEl) {
    autopilotNextCountdownEl.textContent = countdown || "예정 정보 없음";
    autopilotNextCountdownEl.classList.toggle("muted", !countdown);
  }
  if (toplineAutopilotCountdownEl) {
    toplineAutopilotCountdownEl.textContent = countdown || "예정 정보 없음";
    toplineAutopilotCountdownEl.classList.toggle("muted", !countdown);
  }
};

const fetchAutopilotStatus = async () => {
  if (!autopilotStateEl) return;
  try {
    const status = await requestApi("/trading/autopilot/status");
    renderAutopilotStatus(status);
  } catch (error) {
    autopilotStateEl.dataset.status = "offline";
    autopilotStateEl.textContent = "오프라인";
    if (autopilotLastErrorEl) autopilotLastErrorEl.textContent = error.message;
    if (toplineAutopilotStatusEl) {
      toplineAutopilotStatusEl.dataset.status = "offline";
      toplineAutopilotStatusEl.textContent = "오프라인";
    }
    if (toplineAutopilotNoteEl) {
      toplineAutopilotNoteEl.textContent = error.message || "연결 실패";
      toplineAutopilotNoteEl.classList.remove("muted");
    }
    autopilotNextCycleAt = null;
    updateAutopilotCountdown();
    renderAutopilotHoldReason({ running: false, last_skip_reason: null });
  }
};

const handleAutopilotStart = async (event) => {
  event?.preventDefault();
  if (!autopilotForm) return;

  const autoSelectEnabled = Boolean(autopilotAutoMarketInput?.checked);
  let market = (autopilotMarketInput?.value || "").trim().toUpperCase();
  if (!market) {
    if (autoSelectEnabled) {
      market = DEFAULT_MARKET_CODE;
      if (autopilotMarketInput) {
        autopilotMarketInput.value = market;
      }
    } else {
      if (autopilotLastErrorEl) {
        autopilotLastErrorEl.textContent = "먼저 마켓을 입력해주세요.";
      }
      autopilotMarketInput?.focus();
      return;
    }
  }

  const riskAppetite = sanitiseDecimalInput(autopilotRiskInput, DEFAULT_AUTOPILOT_RISK, {
    min: 0,
    max: 1,
    precision: 3,
  });
  if (autopilotRiskInput) {
    updateAutopilotRiskLabel(autopilotRiskInput.value);
  }

  const capital = sanitiseIntegerInput(autopilotCapitalInput, DEFAULT_AUTOPILOT_CAPITAL, {
    min: AUTOPILOT_MIN_CAPITAL,
  });
  const pollInterval = sanitiseIntegerInput(
    autopilotPollInput,
    DEFAULT_AUTOPILOT_POLL_INTERVAL,
    {
      min: AUTOPILOT_MIN_POLL_INTERVAL,
      max: AUTOPILOT_MAX_POLL_INTERVAL,
    }
  );
  const maxPositionPct = sanitiseDecimalInput(
    autopilotMaxPositionInput,
    DEFAULT_AUTOPILOT_MAX_POSITION,
    {
      min: AUTOPILOT_MIN_MAX_POSITION,
      max: AUTOPILOT_MAX_MAX_POSITION,
      precision: 4,
    }
  );
  const minConfidencePct = sanitiseIntegerInput(
    autopilotConfidenceInput,
    DEFAULT_AUTOPILOT_CONFIDENCE,
    {
      min: AUTOPILOT_MIN_CONFIDENCE,
      max: AUTOPILOT_MAX_CONFIDENCE,
    }
  );
  const recommendationMaxMarkets = sanitiseIntegerInput(
    autopilotMaxMarketsInput,
    DEFAULT_AUTOPILOT_MAX_MARKETS,
    {
      min: AUTOPILOT_MIN_RECOMMENDATION_MARKETS,
      max: AUTOPILOT_MAX_RECOMMENDATION_MARKETS,
    }
  );
  const recommendationInterval =
    autopilotRecommendationIntervalSelect?.value ||
    autopilotIntervalSelect?.value ||
    DEFAULT_AUTOPILOT_INTERVAL;

  const payload = {
    mode: autopilotModeSelect?.value || "paper",
    market,
    interval: autopilotIntervalSelect?.value || DEFAULT_AUTOPILOT_INTERVAL,
    risk_appetite: riskAppetite,
    capital,
    poll_interval: pollInterval,
    max_position_pct: maxPositionPct,
    min_confidence_pct: minConfidencePct,
    include_portfolio: Boolean(autopilotIncludePortfolioInput?.checked),
    auto_select_market: autoSelectEnabled,
    recommendation_base: (autopilotBaseSelect?.value || "KRW").toUpperCase(),
    recommendation_interval: recommendationInterval,
    recommendation_max_markets: recommendationMaxMarkets,
    recommendation_include_warnings: Boolean(autopilotIncludeWarningsInput?.checked),
  };

  if (autopilotLastErrorEl) autopilotLastErrorEl.textContent = "";

  try {
    autopilotStartBtn?.setAttribute("disabled", "true");
    if (autopilotStateEl) {
      autopilotStateEl.dataset.status = "warning";
      autopilotStateEl.textContent = "시작 중";
    }
    const status = await requestApi("/trading/autopilot/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderAutopilotStatus(status);
    window.setTimeout(() => {
      fetchAutopilotStatus().catch(() => {});
    }, 1500);
  } catch (error) {
    autopilotStateEl.dataset.status = "warning";
    autopilotStateEl.textContent = "오류";
    if (autopilotLastErrorEl) autopilotLastErrorEl.textContent = error.message;
  } finally {
    autopilotStartBtn?.removeAttribute("disabled");
  }
};

const handleAutopilotStop = async () => {
  if (!autopilotStateEl) return;
  try {
    autopilotStopBtn?.setAttribute("disabled", "true");
    const status = await requestApi("/trading/autopilot/stop", { method: "POST" });
    renderAutopilotStatus(status);
  } catch (error) {
    autopilotStateEl.dataset.status = "warning";
    autopilotStateEl.textContent = "중지 실패";
    if (autopilotLastErrorEl) autopilotLastErrorEl.textContent = error.message;
  } finally {
    autopilotStopBtn?.removeAttribute("disabled");
  }
};

const appendCopilotLog = (payload) => {
  if (!copilotLogEl) return;
  const time = new Date(payload.generated_at);
  const entry = document.createElement("li");
  entry.className = "copilot-log__entry";
  const headline = payload.summary_points?.[0] || payload.answer;
  entry.innerHTML = `
    <time>${formatDateTime(time)}</time>
    <span>${headline}</span>
  `;
  if (copilotLogEl.firstElementChild?.classList.contains("copilot-log__placeholder")) {
    copilotLogEl.innerHTML = "";
  }
  copilotLogEl.prepend(entry);
  const maxEntries = 6;
  while (copilotLogEl.children.length > maxEntries) {
    copilotLogEl.removeChild(copilotLogEl.lastElementChild);
  }
};

const renderCopilotResponse = (payload) => {
  if (!payload) return;
  if (copilotAnswerEl) {
    copilotAnswerEl.textContent = payload.answer;
  }
  renderList(copilotSummaryEl, payload.summary_points, "요약 정보가 없습니다.");
  renderList(copilotActionsEl, payload.action_items, "실행 항목이 없습니다.");
  renderList(copilotRiskNoticesEl, payload.risk_notices, "리스크 주의가 없습니다.");
  renderList(copilotHighlightsEl, payload.highlights, "포트폴리오 하이라이트가 없습니다.");
  renderAutopilotPlan(payload.autopilot);
  if (payload.insight) {
    renderMarketIntelligence(payload.insight);
  }
  appendCopilotLog(payload);
};

const setAssistantStatus = (text, variant = "ghost") => {
  if (!assistantStatusEl) return;
  const variants = {
    ghost: "badge--ghost",
    warning: "badge--warning",
    danger: "badge--danger",
    neutral: "badge--neutral",
  };
  Object.values(variants).forEach((klass) => assistantStatusEl.classList.remove(klass));
  assistantStatusEl.classList.add(variants[variant] || variants.ghost);
  assistantStatusEl.textContent = text;
};

const renderAssistantHistoryList = (entries) => {
  if (!assistantHistoryEl) return;
  assistantHistoryEl.innerHTML = "";
  if (!entries.length) {
    assistantHistoryEl.innerHTML =
      '<li class="assistant-history__placeholder">질문을 입력하면 분석과 답변이 여기에 쌓입니다.</li>';
    return;
  }

  entries.forEach((entry) => {
    const item = document.createElement("li");
    const generatedAt = entry.generated_at ? new Date(entry.generated_at) : null;
    const timestamp = generatedAt && !Number.isNaN(generatedAt.getTime()) ? formatDateTime(generatedAt) : "-";
    const statusLabel = entry.pushed_to_chat ? "Synology Chat 전송" : "대시보드 전용";

    const insights = Array.isArray(entry.insights) ? entry.insights : [];
    const nextSteps = Array.isArray(entry.next_steps) ? entry.next_steps : [];
    const riskNotices = Array.isArray(entry.risk_notices) ? entry.risk_notices : [];

    const renderList = (values, extraClass = "") =>
      values.length
        ? `<ul class="assistant-list ${extraClass}">${values
            .map((value) => `<li>${value}</li>`)
            .join("")}</ul>`
        : "";

    item.innerHTML = `
      <div class="assistant-meta"><strong>${timestamp}</strong><span>${statusLabel}</span></div>
      <p><strong>질문:</strong> ${entry.question || "-"}</p>
      <p>${entry.answer || "-"}</p>
      ${renderList(insights)}
      ${nextSteps.length ? `<div class="assistant-meta">다음 단계</div>${renderList(nextSteps, "assistant-list--steps")}` : ""}
      ${riskNotices.length ? `<div class="assistant-meta">위험 경고</div>${renderList(riskNotices, "assistant-list--risk")}` : ""}
    `;

    assistantHistoryEl.appendChild(item);
  });
};

const appendAssistantHistoryEntry = (payload) => {
  if (!payload) return;
  const entry = {
    question: payload.question,
    answer: payload.answer,
    insights: payload.insights || [],
    next_steps: payload.next_steps || [],
    risk_notices: payload.risk_notices || [],
    generated_at: payload.generated_at,
    pushed_to_chat: Boolean(payload.pushed_to_chat),
  };
  assistantHistory.unshift(entry);
  assistantHistory = assistantHistory.slice(0, 6);
  renderAssistantHistoryList(assistantHistory);
};

const handleAssistant = async (event) => {
  event?.preventDefault();

  if (!copilotQuestionInput) return;
  let question = copilotQuestionInput.value.trim();
  if (!question) {
    question = "지금 시장 전략을 요약해줘";
    copilotQuestionInput.value = question;
  }

  const market = (liveMarketInput?.value || "KRW-BTC").trim().toUpperCase();
  const interval = liveIntervalSelect?.value || "minute60";
  const riskAppetite = Number(copilotRiskSlider?.value || 0.55);
  const capital = Number(copilotCapitalInput?.value || 20_000_000);

  setAssistantStatus("분석 중...", "warning");

  const body = {
    question,
    market,
    interval,
    risk_appetite: Number.isFinite(riskAppetite) ? riskAppetite : 0.55,
    capital: Number.isFinite(capital) && capital > 0 ? capital : 20_000_000,
    include_autopilot: true,
    include_chat_push: assistantChatToggle ? assistantChatToggle.checked : false,
  };

  try {
    const response = await requestApi("/ai/assistant", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setAssistantStatus(response.pushed_to_chat ? "Synology Chat 전송" : "분석 완료", "neutral");
    appendAssistantHistoryEntry(response);
    if (response.autopilot) {
      renderAutopilotPlan(response.autopilot);
    }
    if (response.insight) {
      renderMarketIntelligence(response.insight);
    }
  } catch (error) {
    console.error("Assistant request failed", error);
    setAssistantStatus(`오류: ${error.message}`, "danger");
  }
};

const handleCopilot = async (event, options = {}) => {
  event?.preventDefault();
  const { silent = false } = options;
  if (!copilotForm) return;

  let question = copilotQuestionInput?.value.trim();
  if (!question) {
    question = "지금 시장 전략을 요약해줘";
    if (copilotQuestionInput) copilotQuestionInput.value = question;
  }

  const selectedMode = document.querySelector('input[name="order-mode"]:checked')?.value || "paper";
  const market = (liveMarketInput?.value || "KRW-BTC").trim().toUpperCase();
  const interval = liveIntervalSelect?.value || "minute60";
  const riskAppetite = Number(copilotRiskSlider?.value || 0.55);
  const capital = Number(copilotCapitalInput?.value || 0);

  const body = {
    question,
    market,
    interval,
    mode: selectedMode,
    risk_appetite: Number.isFinite(riskAppetite) ? riskAppetite : 0.55,
    capital: Number.isFinite(capital) && capital > 0 ? capital : 20_000_000,
    include_portfolio: copilotIncludePortfolioInput ? copilotIncludePortfolioInput.checked : true,
  };

  try {
    const response = await requestApi("/ai/copilot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    renderCopilotResponse(response);
  } catch (error) {
    if (copilotAnswerEl) {
      copilotAnswerEl.textContent = `코파일럿 분석 실패: ${error.message}`;
    }
    if (!silent) {
      console.error("Copilot request failed", error);
    }
  }
};

const renderAiPortfolioPlan = (plan) => {
  if (!aiExpectedReturnEl) return;
  aiExpectedReturnEl.textContent = formatPercent(plan.expected_return_pct);
  aiExpectedVolEl.textContent = formatPercent(plan.expected_volatility_pct);
  aiSharpeEl.textContent = ratioFormatter.format(plan.sharpe_estimate);
  aiDiversificationEl.textContent = formatPercent(plan.diversification_score_pct);
  aiTailRiskEl.textContent = formatPercent(plan.tail_risk_guard_pct);

  if (aiHedgesEl) {
    aiHedgesEl.innerHTML = "";
    plan.hedging_notes.forEach((note) => {
      const li = document.createElement("li");
      li.textContent = note;
      aiHedgesEl.appendChild(li);
    });
  }

  if (aiAllocationsBody) {
    aiAllocationsBody.innerHTML = "";
    if (!plan.allocations.length) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.className = "ai-allocations__placeholder";
      cell.textContent = "AI 포트폴리오를 계산하지 못했습니다.";
      row.appendChild(cell);
      aiAllocationsBody.appendChild(row);
    } else {
      plan.allocations.forEach((allocation) => {
        const row = document.createElement("tr");
        row.innerHTML = `
          <td>${allocation.symbol}</td>
          <td>${allocation.asset_type}</td>
          <td>${percentFormatter.format(allocation.weight * 100)}%</td>
          <td>${formatCurrency(allocation.allocation_krw)}</td>
          <td>${formatPercent(allocation.expected_return_pct)}</td>
          <td>${formatPercent(allocation.expected_volatility_pct)}</td>
          <td>${allocation.rationale}</td>
        `;
        aiAllocationsBody.appendChild(row);
      });
    }
  }

  if (aiBriefingsEl) {
    if (!plan.market_briefings.length) {
      aiBriefingsEl.innerHTML = '<p class="ai-briefings__placeholder">시장 브리핑이 없습니다.</p>';
    } else {
      const cards = plan.market_briefings
        .map(
          (item) => `
            <article class="ai-briefing-card">
              <header>
                <h4>${item.market}</h4>
                <span class="badge">${item.regime}</span>
              </header>
              <p class="ai-briefing-card__summary">${item.summary}</p>
              <footer>
                <span>권장 액션: <strong>${item.action}</strong></span>
                <span>신뢰도 ${item.confidence_pct}%</span>
              </footer>
            </article>
          `
        )
        .join("");
      aiBriefingsEl.innerHTML = cards;
    }
  }
};

const handleAiPortfolio = async (event, options = {}) => {
  event?.preventDefault();
  const { silent = false } = options;
  if (!aiPortfolioForm) return;

  const capital = Number(aiCapitalEl?.value || 0);
  if (!Number.isFinite(capital) || capital <= 0) {
    if (!silent) {
      alert("투자 자본을 올바르게 입력해주세요.");
    }
    return;
  }

  const riskAppetite = Number(aiRiskSlider?.value || 0.5);
  const includeCash = aiIncludeCashEl ? aiIncludeCashEl.checked : true;
  const preferredRaw = aiPreferredEl?.value || "";
  const preferred = preferredRaw
    .split(",")
    .map((item) => item.trim().toUpperCase())
    .filter((item) => item.length > 0);

  const payload = {
    risk_appetite: riskAppetite,
    capital,
    include_cash: includeCash,
  };
  if (preferred.length) {
    payload.preferred_markets = preferred;
  }

  try {
    const plan = await requestApi("/ai/portfolio/optimize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    renderAiPortfolioPlan(plan);
  } catch (error) {
    if (!silent) {
      alert(error.message);
    } else {
      console.error("AI portfolio optimization failed", error);
    }
  }
};

const refreshNews = async () => {
  if (!newsListEl) return;
  try {
    const response = await requestApi("/market/news");
    newsListEl.innerHTML = "";
    response.items.forEach((item) => {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = item.url;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.textContent = item.title;
      const meta = document.createElement("div");
      meta.className = "news-meta";
      meta.textContent = `${item.source} · ${item.published_at}`;
      li.appendChild(link);
      li.appendChild(meta);
      newsListEl.appendChild(li);
    });
    if (!response.items.length) {
      newsListEl.innerHTML = '<li class="news-placeholder">표시할 뉴스가 없습니다.</li>';
    }
  } catch (error) {
    newsListEl.innerHTML = `<li class="news-placeholder">${error.message}</li>`;
  }
};

const renderDiagnostics = (checks) => {
  if (!diagnosticsListEl) return;
  diagnosticsListEl.innerHTML = "";
  if (!checks || !checks.length) {
    diagnosticsListEl.innerHTML = '<li class="diagnostics-placeholder">진단 결과가 비어 있습니다.</li>';
    return;
  }

  checks.forEach((check) => {
    const li = document.createElement("li");
    li.dataset.status = check.status || "ok";
    const nameEl = document.createElement("span");
    nameEl.className = "diagnostic-name";
    nameEl.textContent = check.name;
    const detailEl = document.createElement("span");
    detailEl.className = "diagnostic-detail";
    detailEl.textContent = check.detail;
    const latencyEl = document.createElement("span");
    latencyEl.className = "diagnostic-latency";
    const latency = Number(check.latency_ms);
    latencyEl.textContent = Number.isFinite(latency) ? `${latency.toFixed(1)}ms` : "-";
    li.appendChild(nameEl);
    li.appendChild(detailEl);
    li.appendChild(latencyEl);
    diagnosticsListEl.appendChild(li);
  });
};

const refreshDiagnostics = async () => {
  if (!diagnosticsListEl) return;
  diagnosticsListEl.innerHTML = '<li class="diagnostics-placeholder">진단 실행 중...</li>';
  try {
    const payload = await requestApi("/diagnostics/full");
    renderDiagnostics(payload.checks);
  } catch (error) {
    diagnosticsListEl.innerHTML = `<li class="diagnostics-placeholder">${error.message}</li>`;
  }
};

const renderSelfCheck = (payload) => {
  if (!selfCheckSummaryEl || !selfCheckIssuesEl) return;
  const overall = payload?.overall_severity || "pending";
  selfCheckSummaryEl.dataset.severity = overall;
  selfCheckSummaryEl.textContent = payload?.summary || "AI 자가 진단 결과를 불러오지 못했습니다.";

  const issues = Array.isArray(payload?.issues) ? payload.issues : [];
  selfCheckIssuesEl.innerHTML = "";
  if (!issues.length) {
    selfCheckIssuesEl.innerHTML = '<li class="selfcheck-placeholder">표시할 이슈가 없습니다.</li>';
    return;
  }

  issues.forEach((issue) => {
    const li = document.createElement("li");
    li.dataset.severity = issue.severity || "info";
    const title = document.createElement("div");
    title.className = "issue-title";
    title.textContent = `${issue.component} · ${issue.summary}`;
    li.appendChild(title);

    if (issue.detail) {
      const detail = document.createElement("div");
      detail.className = "issue-detail";
      detail.textContent = issue.detail;
      li.appendChild(detail);
    }

    if (issue.suggested_action) {
      const action = document.createElement("div");
      action.className = "issue-action";
      action.textContent = issue.suggested_action;
      li.appendChild(action);
    }

    selfCheckIssuesEl.appendChild(li);
  });
};

const refreshSelfCheck = async ({ force = false } = {}) => {
  if (!selfCheckSummaryEl || !selfCheckIssuesEl) return;
  selfCheckSummaryEl.dataset.severity = "pending";
  selfCheckSummaryEl.textContent = "AI 자가 진단 실행 중...";
  selfCheckIssuesEl.innerHTML = '<li class="selfcheck-placeholder">자가 진단을 실행하는 중...</li>';
  const suffix = force ? "?force=true" : "";
  try {
    const payload = await requestApi(`/diagnostics/ai/self-check${suffix}`);
    renderSelfCheck(payload);
  } catch (error) {
    selfCheckSummaryEl.dataset.severity = "critical";
    selfCheckSummaryEl.textContent = error.message;
    selfCheckIssuesEl.innerHTML = '<li class="selfcheck-placeholder">자가 진단을 불러오지 못했습니다.</li>';
  }
};

async function executeApiRequest(base, path, buildConfig) {
  const url = joinApiUrl(base, path);
  const config = buildConfig();
  let response;
  try {
    response = await fetch(url, config);
  } catch (error) {
    const baseLabel = base || "/api";
    const message = `API 연결에 실패했습니다. 현재 엔드포인트: ${baseLabel}`;
    const fetchError = new Error(
      error && error.message ? `${message} · ${error.message}` : message
    );
    fetchError.__retry = true;
    throw fetchError;
  }

  const contentType = response.headers.get("content-type") || "";
  const rawPayload = await response.text();
  let parsedPayload = rawPayload;
  let parsedFromJson = false;

  if (contentType.includes("application/json")) {
    try {
      parsedPayload = rawPayload ? JSON.parse(rawPayload) : {};
      parsedFromJson = true;
    } catch (error) {
      const parseError = new Error("API 응답을 해석하지 못했습니다.");
      parseError.__retry = true;
      throw parseError;
    }
  }

  if (!response.ok) {
    let detail = null;
    if (parsedFromJson && parsedPayload && typeof parsedPayload === "object") {
      detail = parsedPayload.detail || null;
    }
    const error = new Error(detail || response.statusText || "요청에 실패했습니다.");
    if (response.status >= 500 || response.status === 404) {
      error.__retry = true;
    }
    throw error;
  }

  if (typeof parsedPayload === "string") {
    const trimmed = parsedPayload.trim();
    if (trimmed.startsWith("<!DOCTYPE") || trimmed.startsWith("<html")) {
      const htmlError = new Error(
        "HTML 응답을 수신했습니다. 대시보드의 API 엔드포인트가 FastAPI 백엔드로 연결되는지 확인해주세요."
      );
      htmlError.__retry = true;
      throw htmlError;
    }
    try {
      return JSON.parse(trimmed);
    } catch (error) {
      const textError = new Error(
        "예상과 다른 텍스트 응답을 받았습니다. API 연결 또는 역방향 프록시 구성을 점검해주세요."
      );
      textError.__retry = true;
      throw textError;
    }
  }

  return parsedPayload;
}

async function requestApi(path, options = {}) {
  const attempted = new Set();
  const buildConfig = () => {
    const config = { ...options };
    config.headers = {
      ...(options.headers || {}),
    };
    return config;
  };

  let lastError = null;

  while (true) {
    const base = getApiBase();
    try {
      return await executeApiRequest(base, path, buildConfig);
    } catch (error) {
      lastError = error;
      const retriable = Boolean(error && error.__retry);
      attempted.add(base);

      if (!retriable) {
        throw error;
      }

      if (shouldResetStoredBase(base)) {
        applyApiBase(DEFAULT_API_BASE, { persist: true, silent: true });
        continue;
      }

      const alternate = deriveAlternateBase(base);
      if (alternate && !attempted.has(alternate)) {
        applyApiBase(alternate, { persist: true, silent: true });
        continue;
      }

      const next = pickNextApiCandidate(attempted);
      if (next) {
        applyApiBase(next, { persist: true, silent: true });
        continue;
      }

      throw lastError || error;
    }
  }
}

const renderPaperSummary = (balance) => {
  if (!balance) {
    return "페이퍼 계좌 정보를 불러오지 못했습니다.";
  }

  const updatedAt = balance.last_updated ? new Date(balance.last_updated) : null;
  const hasValidTimestamp = updatedAt && !Number.isNaN(updatedAt.getTime());
  const lastUpdatedText = hasValidTimestamp ? formatDateTime(updatedAt) : "확인 필요";
  const lastUpdatedRelative = hasValidTimestamp ? formatRelativeTime(updatedAt) : "";
  const sourceDescription = PAPER_SOURCE_LABELS[balance.price_source] || PAPER_SOURCE_LABELS.manual;
  const marketLabel = balance.market || "KRW-BTC";
  const intervalLabel = describeInterval(balance.interval || "minute1");

  const headline = `
    <div class="paper-balance__headline">
      <div>
        <span>현금</span>
        <strong>${formatCurrency(balance.cash)} KRW</strong>
      </div>
      <div>
        <span>총자산 가치</span>
        <strong>${formatCurrency(balance.portfolio_value)} KRW</strong>
      </div>
      <div>
        <span>마지막 업데이트</span>
        <strong>${lastUpdatedText}</strong>
        ${lastUpdatedRelative ? `<small>${lastUpdatedRelative}</small>` : ""}
      </div>
      <div>
        <span>모니터링</span>
        <strong>${marketLabel}</strong>
        <small>${intervalLabel}</small>
      </div>
    </div>
  `;

  const positions = balance.positions.length
    ? `<table class="paper-table">
        <thead>
          <tr>
            <th>마켓</th>
            <th>보유수량</th>
            <th>평단가</th>
            <th>현재가</th>
            <th>평가손익</th>
          </tr>
        </thead>
        <tbody>
          ${balance.positions
            .map(
              (position) => `
                <tr>
                  <td>${position.market}</td>
                  <td>${ratioFormatter.format(position.volume)}</td>
                  <td>${formatCurrency(position.average_price)}</td>
                  <td>${formatCurrency(position.market_price)}</td>
                  <td class="${position.unrealized_pnl >= 0 ? "positive" : "negative"}">
                    ${formatCurrency(position.unrealized_pnl)}
                  </td>
                </tr>
              `,
            )
            .join("")}
        </tbody>
      </table>`
    : '<p class="muted">보유 중인 포지션이 없습니다.</p>';

  const orders = balance.orders.length
    ? `<table class="paper-table paper-table--compact">
        <thead>
          <tr>
            <th>시간</th>
            <th>마켓</th>
            <th>구분</th>
            <th>가격</th>
            <th>수량</th>
            <th>수수료</th>
            <th>실현손익</th>
          </tr>
        </thead>
        <tbody>
          ${balance.orders
            .map((order) => {
              const timestamp = new Date(order.executed_at).toLocaleString("ko-KR");
              const pnlClass = order.realized_pnl >= 0 ? "positive" : "negative";
              return `
                <tr>
                  <td>${timestamp}</td>
                  <td>${order.market}</td>
                  <td>${order.side === "bid" ? "매수" : "매도"}</td>
                  <td>${formatCurrency(order.price)}</td>
                  <td>${ratioFormatter.format(order.volume)}</td>
                  <td>${formatCurrency(order.fee)}</td>
                  <td class="${pnlClass}">${formatCurrency(order.realized_pnl)}</td>
                </tr>
              `;
            })
            .join("")}
        </tbody>
      </table>`
    : '<p class="muted">아직 체결된 주문이 없습니다.</p>';

  const sourceNote = `<div class="paper-balance__source-note">데이터 출처 · ${sourceDescription}</div>`;

  return `${headline}${sourceNote}<div class="paper-balance__section"><h4>보유 자산</h4>${positions}</div><div class="paper-balance__section"><h4>최근 주문</h4>${orders}</div>`;
};

const updatePaperSummary = (balance) => {
  if (!paperSummaryEl) return;
  paperSummaryEl.innerHTML = renderPaperSummary(balance);
  updatePaperHeartbeat(balance);
  if (balance) {
    const initial = Number(balance.initial_cash ?? DEFAULT_CAPITAL_KRW);
    const ending = Number(balance.portfolio_value ?? initial);
    const profit = ending - initial;
    const totalReturnPct = initial > 0 ? (profit / initial) * 100 : 0;
    const source = balance.price_source || "manual";
    updateCapitalSummary({
      initial_capital: initial,
      ending_equity: ending,
      profit_krw: profit,
      total_return_pct: totalReturnPct,
      price_source: source,
      price_message: PAPER_SOURCE_LABELS[source] || PAPER_SOURCE_LABELS.manual,
      market: balance.market,
    });
  }
  if (paperStatusMarketInput && balance?.market) {
    paperStatusMarketInput.value = balance.market;
  }
  if (paperStatusIntervalSelect && balance?.interval) {
    paperStatusIntervalSelect.value = balance.interval;
  }
  if (toplinePaperNoteEl) {
    if (!balance) {
      toplinePaperNoteEl.textContent = "상태 확인 필요";
    } else {
      const updatedAt = balance.last_updated ? new Date(balance.last_updated) : null;
      const relative = updatedAt && !Number.isNaN(updatedAt.getTime()) ? formatRelativeTime(updatedAt) : "시간 확인 필요";
      const labels = [];
      if (balance.market) {
        labels.push(balance.market);
      }
      if (balance.interval) {
        labels.push(describeInterval(balance.interval));
      }
      toplinePaperNoteEl.textContent = `${labels.join(" · ") || "모니터링 미설정"} · ${relative}`;
    }
    toplinePaperNoteEl.classList.add("muted");
  }
};

const fetchPaperStatus = async () => {
  if (!paperSummaryEl) return;
  setPaperHeartbeat("loading", "새로 고치는 중...");
  updatePaperSourceLabel(null);
  try {
    const market = (paperStatusMarketInput?.value || "KRW-BTC").trim().toUpperCase();
    const interval = paperStatusIntervalSelect?.value || "minute1";
    const params = new URLSearchParams({ market, interval });
    const balance = await requestApi(`/trading/paper/status?${params.toString()}`);
    updatePaperSummary(balance);
  } catch (error) {
    paperSummaryEl.innerHTML = `<p class="error">${error.message}</p>`;
    setPaperHeartbeat("offline", "연결 실패");
    if (paperPriceSourceEl) {
      paperPriceSourceEl.textContent = "연결 실패 - 상태 확인 필요";
      paperPriceSourceEl.classList.remove("muted", "status-note--highlight", "status-note--warning");
      paperPriceSourceEl.classList.add("status-note--warning");
    }
    if (toplinePaperNoteEl) {
      toplinePaperNoteEl.textContent = error.message || "페이퍼 계좌 상태를 확인하지 못했습니다.";
      toplinePaperNoteEl.classList.remove("muted");
    }
  }
};

const refreshApiStatus = async () => {
  if (!apiStatusEl) return;
  updateApiStatus("loading", "체크 중...");
  try {
    const health = await requestApi("/health");
    if (health.status === "ok") {
      updateApiStatus("online", "연결됨");
    } else {
      updateApiStatus("warning", "응답 확인 필요");
    }
  } catch (error) {
    updateApiStatus("offline", "오프라인");
  }
};

const refreshChatStatus = async () => {
  if (!chatStatusDetailEl) return;
  try {
    const status = await requestApi("/notifications/chat/status");
    if (chatStatusHostEl) {
      if (status.webhook_host) {
        chatStatusHostEl.textContent = `호스트: ${status.webhook_host}`;
        chatStatusHostEl.classList.add("muted");
      } else {
        chatStatusHostEl.textContent = "웹훅 호스트를 확인할 수 없습니다.";
        chatStatusHostEl.classList.remove("muted");
      }
    }
    if (!status.configured) {
      chatStatusDetailEl.textContent = "웹훅 URL이 설정되지 않았습니다.";
      chatStatusDetailEl.classList.remove("muted");
      if (chatStatusHostEl) {
        chatStatusHostEl.textContent = "웹훅 미설정";
        chatStatusHostEl.classList.remove("muted");
      }
      return;
    }

    if (status.last_error) {
      chatStatusDetailEl.textContent = `최근 오류: ${status.last_error}`;
      chatStatusDetailEl.classList.remove("muted");
      if (chatStatusHostEl && status.webhook_host) {
        chatStatusHostEl.textContent = `호스트: ${status.webhook_host}`;
        chatStatusHostEl.classList.remove("muted");
      }
      return;
    }

    if (status.last_success_at) {
      chatStatusDetailEl.textContent = `마지막 성공: ${formatShortTime(status.last_success_at)} · "${status.last_message || "메시지"}"`;
    } else if (status.last_attempt_at) {
      chatStatusDetailEl.textContent = `마지막 시도: ${formatShortTime(status.last_attempt_at)}`;
    } else {
      chatStatusDetailEl.textContent = "웹훅이 준비되었습니다.";
    }
    chatStatusDetailEl.classList.add("muted");
  } catch (error) {
    chatStatusDetailEl.textContent = "웹훅 상태를 불러오지 못했습니다.";
    chatStatusDetailEl.classList.remove("muted");
    if (chatStatusHostEl) {
      chatStatusHostEl.textContent = "호스트 확인 실패";
      chatStatusHostEl.classList.remove("muted");
    }
  }
};

const handleChatTest = async () => {
  if (!chatTestBtn) return;
  const message = (chatTestMessageInput?.value || "").trim() || "Sado Trade Bot 웹훅 테스트";
  updateChatTestStatus("loading", "전송 중...");
  try {
    await requestApi("/notifications/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    updateChatTestStatus("online", "전송 성공");
  } catch (error) {
    updateChatTestStatus("warning", error.message);
  } finally {
    await refreshChatStatus().catch(() => {});
  }
};

const riskDescriptors = [
  { threshold: 0.2, label: "초안정" },
  { threshold: 0.5, label: "안정형" },
  { threshold: 0.8, label: "균형형" },
  { threshold: 1.01, label: "공격형" },
];

const updateRiskLabel = (value) => {
  if (!riskLabel) return;
  const numeric = Number(value);
  const descriptor = describeRiskLevel(numeric);
  riskLabel.textContent = `${descriptor} (${percentFormatter.format(numeric * 100)}%)`;
};

const describeRiskLevel = (value) => {
  const numeric = Number(value);
  return riskDescriptors.find((item) => numeric <= item.threshold)?.label || "커스텀";
};

const updateAIRiskLabel = (value) => {
  if (!aiRiskLabel) return;
  const numeric = Number(value);
  const descriptor = describeRiskLevel(numeric);
  aiRiskLabel.textContent = `${descriptor} (${percentFormatter.format(numeric * 100)}%)`;
};

const updateAutopilotRiskLabel = (value) => {
  if (!autopilotRiskLabel) return;
  const numeric = Number(value);
  const descriptor = describeRiskLevel(numeric);
  autopilotRiskLabel.textContent = `${descriptor} (${percentFormatter.format(numeric * 100)}%)`;
};

const updateAlphaBriefing = (report, context = {}) => {
  if (!alphaBriefingEl) return;

  if (!isFactualReport(report)) {
    alphaBriefingEl.textContent = "실시간 시세 없음 - 브리핑 비활성화";
    alphaBriefingEl.classList.add("metric-disabled");
    return;
  }

  alphaBriefingEl.classList.remove("metric-disabled");
  const payoff = formatRatio(report.trade_summary.payoff_ratio || report.win_loss_ratio);
  const lines = [];
  if (context.market) {
    lines.push(`• 분석 마켓: ${context.market}${context.interval ? ` (${describeInterval(context.interval)})` : ''}`);
  }
  if (context.useLiveData !== undefined) {
    lines.push(`• 데이터 출처: ${context.useLiveData ? 'Upbit 실시간' : '시뮬레이션'}`);
  }
  lines.push(`• 켈리 권장 비중: ${formatPercent(report.kelly_fraction_pct)}`);
  lines.push(`• 평균 낙폭: ${formatPercent(report.average_drawdown_pct)} | 페인 인덱스: ${formatPercent(report.pain_index)}`);
  lines.push(`• 왜도/첨도: ${ratioFormatter.format(report.skewness)} / ${ratioFormatter.format(report.kurtosis)}`);
  lines.push(`• 최대 반등폭: ${formatPercent(report.max_runup_pct)} | 시장 노출: ${formatPercent(report.exposure_time_pct)}`);
  lines.push(`• 페이오프 비율: ${payoff}`);

  alphaBriefingEl.textContent = lines.join("\n");
};

async function simulateStrategy(formValues) {
  return requestApi("/strategies/simulate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(formValues),
  });
}

function updateCapitalSummary(report) {
  if (!report) return;

  const initial = Number(report.initial_capital ?? 0);
  const ending = Number(report.ending_equity ?? initial);
  const profit = Number(report.profit_krw ?? ending - initial);
  const factual = isFactualReport(report);

  if (initialCapitalEl) {
    enableMetric(initialCapitalEl, formatCurrencyWithSymbol(initial));
  }
  if (endingEquityEl) {
    enableMetric(endingEquityEl, formatCurrencyWithSymbol(ending));
  }
  if (profitKrwEl) {
    profitKrwEl.classList.remove("profit-positive", "profit-negative", "metric-disabled");
    if (!factual) {
      disableMetric(profitKrwEl, "실시간 시세 없음 - 수익률 표시 불가");
    } else {
      const sign = profit > 0 ? "+" : profit < 0 ? "-" : "";
      const profitLabel = `${sign}${formatCurrencyWithSymbol(Math.abs(profit))} (${formatPercent(
        report.total_return_pct ?? 0,
      )})`;
      profitKrwEl.textContent = profitLabel;
      profitKrwEl.classList.toggle("profit-positive", profit >= 0);
      profitKrwEl.classList.toggle("profit-negative", profit < 0);
    }
  }
  if (marketNoteEl) {
    if (report.market) {
      enableMetric(marketNoteEl, `전략 마켓: ${report.market}`);
      marketNoteEl.classList.remove("muted");
    } else {
      disableMetric(marketNoteEl, "전략 마켓: -");
      marketNoteEl.classList.add("muted");
    }
  }
  if (priceSourceEl) {
    const source = report.price_source || "manual";
    const label = PRICE_SOURCE_LABELS[source] || "데이터 출처 미확인";
    const detail = report.price_message || report.price_detail || "";
    priceSourceEl.dataset.source = source;
    priceSourceEl.textContent = detail ? `${label} · ${detail}` : label;
    const shouldMute = (source === "upbit" || source === "upbit_stale") && !detail;
    priceSourceEl.classList.toggle("muted", shouldMute);
  }
}

function updateMetrics(report) {
  updateCapitalSummary(report);
  updateIntegrityBadge(report);
  const factual = isFactualReport(report);
  if (!factual) {
    disableMetric(totalReturnEl, "실시간 시세 없음");
    disableMetric(annualReturnEl, "-");
    disableMetric(drawdownEl, "-");
    disableMetric(tradeCountEl, "-");
    disableMetric(tradeWinrateEl, "-");
    disableMetric(tradeAvgEl, "-");
    disableMetric(tradeExpectancyEl, "-");
    disableMetric(tradeMedianEl, "-");
    disableMetric(tradeWinLossEl, "-");
    if (tradePayoffEl) disableMetric(tradePayoffEl, "-");
    disableMetric(tradeBestEl, "-");
    disableMetric(tradeWorstEl, "-");
    disableMetric(volatilityEl, "-");
    disableMetric(sharpeEl, "-");
    disableMetric(sortinoEl, "-");
    disableMetric(calmarEl, "-");
    disableMetric(varEl, "-");
    disableMetric(exposureEl, "-");
    disableMetric(profitFactorEl, "-");
    disableMetric(expectancyEl, "-");
    disableMetric(holdEl, "-");
    disableMetric(ulcerEl, "-");
    disableMetric(downsideEl, "-");
    disableMetric(recoveryEl, "-");
    disableMetric(avgWinEl, "-");
    disableMetric(avgLossEl, "-");
    disableMetric(winLossEl, "-");
    disableMetric(tailEl, "-");
    disableMetric(omegaEl, "-");
    disableMetric(kellyEl, "-");
    disableMetric(streakWinEl, "-");
    disableMetric(streakLossEl, "-");
    disableMetric(skewnessEl, "-");
    disableMetric(kurtosisEl, "-");
    disableMetric(avgDrawdownEl, "-");
    disableMetric(painEl, "-");
    disableMetric(runupEl, "-");
    disableMetric(mcMedianEl, "-");
    disableMetric(mcP05El, "-");
    disableMetric(mcP95El, "-");
    disableMetric(mcAvgEl, "-");
    updateAlphaBriefing(report, lastSimulationContext);
    return;
  }

  enableMetric(totalReturnEl, formatPercent(report.total_return_pct));
  enableMetric(annualReturnEl, formatPercent(report.annualized_return_pct));
  enableMetric(drawdownEl, formatPercent(report.max_drawdown_pct));
  enableMetric(tradeCountEl, `${report.trade_summary.count}건`);
  enableMetric(tradeWinrateEl, `승률 ${ratioFormatter.format(report.trade_summary.win_rate)}%`);
  enableMetric(tradeAvgEl, `평균 ${ratioFormatter.format(report.trade_summary.avg_return_pct)}%`);
  enableMetric(tradeExpectancyEl, `기대 ${ratioFormatter.format(report.trade_summary.expectancy_pct)}%`);
  enableMetric(tradeMedianEl, `중앙값 ${ratioFormatter.format(report.trade_summary.median_return_pct)}%`);
  enableMetric(tradeWinLossEl, `승패비 ${formatRatio(report.trade_summary.win_loss_ratio)}`);
  if (tradePayoffEl) enableMetric(tradePayoffEl, `페이오프 ${formatRatio(report.trade_summary.payoff_ratio)}`);
  enableMetric(tradeBestEl, `최대수익 ${ratioFormatter.format(report.trade_summary.largest_win_pct)}%`);
  enableMetric(tradeWorstEl, `최대손실 ${ratioFormatter.format(report.trade_summary.largest_loss_pct)}%`);

  enableMetric(volatilityEl, formatPercent(report.volatility_pct));
  enableMetric(sharpeEl, ratioFormatter.format(report.sharpe_ratio));
  enableMetric(sortinoEl, ratioFormatter.format(report.sortino_ratio));
  enableMetric(calmarEl, ratioFormatter.format(report.calmar_ratio));
  enableMetric(varEl, formatPercent(report.value_at_risk_pct));
  enableMetric(exposureEl, formatPercent(report.exposure_time_pct));
  enableMetric(profitFactorEl, ratioFormatter.format(report.profit_factor));
  enableMetric(expectancyEl, formatPercent(report.expectancy_pct));
  enableMetric(holdEl, `${ratioFormatter.format(report.avg_trade_duration_bars)}봉`);
  enableMetric(ulcerEl, ratioFormatter.format(report.ulcer_index));
  enableMetric(downsideEl, formatPercent(report.downside_deviation_pct));
  enableMetric(recoveryEl, formatRatio(report.recovery_factor));
  enableMetric(avgWinEl, formatPercent(report.average_win_pct));
  enableMetric(avgLossEl, formatPercent(report.average_loss_pct));
  enableMetric(winLossEl, formatRatio(report.win_loss_ratio));
  enableMetric(tailEl, formatRatio(report.tail_ratio));
  enableMetric(omegaEl, formatRatio(report.omega_ratio));
  enableMetric(kellyEl, formatPercent(report.kelly_fraction_pct));
  enableMetric(streakWinEl, `${report.max_consecutive_wins}회`);
  enableMetric(streakLossEl, `${report.max_consecutive_losses}회`);
  enableMetric(skewnessEl, ratioFormatter.format(report.skewness));
  enableMetric(kurtosisEl, ratioFormatter.format(report.kurtosis));
  enableMetric(avgDrawdownEl, formatPercent(report.average_drawdown_pct));
  enableMetric(painEl, formatPercent(report.pain_index));
  enableMetric(runupEl, formatPercent(report.max_runup_pct));

  enableMetric(mcMedianEl, formatPercent(report.monte_carlo_summary.median_return_pct));
  enableMetric(mcP05El, formatPercent(report.monte_carlo_summary.p05_return_pct));
  enableMetric(mcP95El, formatPercent(report.monte_carlo_summary.p95_return_pct));
  enableMetric(mcAvgEl, formatPercent(report.monte_carlo_summary.average_return_pct));

  updateAlphaBriefing(report, lastSimulationContext);
}

function renderTradeHistory(entries) {
  if (!tradeTableBody) return;

  tradeHistoryCache = Array.isArray(entries) ? entries.slice() : [];
  tradeTableBody.innerHTML = "";
  if (!Array.isArray(entries) || entries.length === 0) {
    tradeTableBody.innerHTML =
      '<tr class="table-placeholder"><td colspan="10">최근 거래 기록이 없습니다.</td></tr>';
    lastTradeHistoryToken = null;
    return;
  }

  const sourceLabels = {
    paper: "페이퍼", 
    autopilot: "오토파일럿",
  };

  entries.forEach((entry) => {
    const row = document.createElement("tr");
    const executedAt = entry.executed_at ? new Date(entry.executed_at) : null;
    const executedLabel = executedAt && !Number.isNaN(executedAt.getTime())
      ? executedAt.toLocaleString("ko-KR")
      : "-";
    const market = (entry.market || "-").toString().toUpperCase();
    const modeLabel = entry.mode === "live" ? "실거래" : "페이퍼";
    const sideLabel = entry.side === "bid" ? "매수" : "매도";
    let tradeValue = parseNumeric(entry.value);
    if (tradeValue === null) {
      const price = parseNumeric(entry.price);
      const volume = parseNumeric(entry.volume);
      if (price !== null && volume !== null) {
        tradeValue = price * volume;
      } else {
        tradeValue = 0;
      }
    }
    const value = Number.isFinite(tradeValue) ? tradeValue : 0;
    const fee = parseNumeric(entry.fee) ?? 0;
    const pnl = parseNumeric(entry.realized_pnl) ?? 0;
    const noteParts = [];
    if (entry.note) {
      noteParts.push(entry.note);
    }
    if (entry.source) {
      noteParts.push(sourceLabels[entry.source] || entry.source);
    }
    const noteText = noteParts.length ? noteParts.join(" · ") : "-";

    row.innerHTML = `
      <td>${executedLabel}</td>
      <td>${market}</td>
      <td>${modeLabel}</td>
      <td>${sideLabel}</td>
      <td>${formatCurrency(entry.price)}</td>
      <td>${ratioFormatter.format(entry.volume ?? 0)}</td>
      <td>${formatCurrency(value)}</td>
      <td>${fee ? formatCurrency(fee) : "-"}</td>
      <td>${formatCurrency(pnl)}</td>
      <td>${noteText}</td>
    `;

    const pnlCell = row.children[8];
    if (pnlCell) {
      pnlCell.classList.add(pnl >= 0 ? "trade-positive" : "trade-negative");
    }
    const noteCell = row.children[9];
    if (noteCell) {
      noteCell.classList.add("trade-note");
    }

    tradeTableBody.appendChild(row);
  });

  lastTradeHistoryToken = createTradeSignature(entries[0]);
}

async function refreshTradeHistory() {
  if (!tradeTableBody) return;
  if (tradeHistoryRefreshBtn) {
    tradeHistoryRefreshBtn.disabled = true;
    tradeHistoryRefreshBtn.setAttribute("aria-busy", "true");
  }
  try {
    const response = await requestApi("/trading/history?limit=80");
    renderTradeHistory(response?.items ?? []);
  } catch (error) {
    tradeTableBody.innerHTML = `<tr class="table-placeholder"><td colspan="10">거래 이력 불러오기 실패: ${error.message}</td></tr>`;
  } finally {
    if (tradeHistoryRefreshBtn) {
      tradeHistoryRefreshBtn.disabled = false;
      tradeHistoryRefreshBtn.removeAttribute("aria-busy");
      tradeHistoryRefreshBtn.setAttribute(
        "title",
        `마지막 새로고침: ${new Intl.DateTimeFormat("ko-KR", {
          hour12: false,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }).format(new Date())}`,
      );
    }
  }
}

function renderEquityCurve(values) {
  const canvas = document.getElementById("equity-chart");
  if (!canvas) return;

  if (!Array.isArray(values) || values.length === 0) {
    if (chartInstance) {
      chartInstance.destroy();
      chartInstance = null;
    }
    updateEquityNote([]);
    return;
  }

  const labels = values.map((_, idx) => idx + 1);
  const dataset = {
    labels,
    datasets: [
      {
        label: "Equity",
        data: values,
        fill: true,
        borderColor: "#7ae1ff",
        backgroundColor: "rgba(122, 225, 255, 0.15)",
        tension: 0.35,
        pointRadius: 0,
      },
    ],
  };

  if (chartInstance) {
    chartInstance.data = dataset;
    chartInstance.update();
    updateEquityNote(values);
    return;
  }

  chartInstance = new Chart(canvas, {
    type: "line",
    data: dataset,
    options: {
      maintainAspectRatio: false,
      layout: {
        padding: { top: 8, bottom: 8, left: 4, right: 4 },
      },
      scales: {
        x: {
          display: false,
        },
        y: {
          ticks: {
            color: "#98a1c3",
            callback: (value) =>
              new Intl.NumberFormat("ko-KR", { notation: "compact" }).format(value),
          },
          grid: {
            color: "rgba(122, 225, 255, 0.1)",
          },
        },
      },
      plugins: {
        legend: {
          display: false,
        },
        tooltip: {
          backgroundColor: "rgba(10, 17, 36, 0.85)",
          borderColor: "rgba(122, 225, 255, 0.4)",
          borderWidth: 1,
          titleColor: "#7ae1ff",
          bodyColor: "#f6f7fb",
          displayColors: false,
          callbacks: {
            label: (context) =>
              `Equity: ${new Intl.NumberFormat("ko-KR").format(context.parsed.y)} KRW`,
          },
        },
      },
      elements: {
        line: {
          borderWidth: 2,
        },
        point: {
          radius: 0,
        },
      },
    },
  });
  updateEquityNote(values);
}

async function handleSimulation(event) {
  event?.preventDefault();

  const form = document.getElementById("strategy-form");
  if (!form) return;
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());

  const marketValue = (payload.market || "").trim().toUpperCase();
  if (marketValue) {
    payload.market = marketValue;
  } else {
    delete payload.market;
  }

  const intervalValue = (payload.interval || "").trim();
  if (intervalValue) {
    payload.interval = intervalValue;
  } else {
    delete payload.interval;
  }

  payload.use_live_data = formData.has("use_live_data");

  ["fast_period", "slow_period"].forEach((key) => {
    payload[key] = Number.parseInt(payload[key], 10);
  });
  [
    "initial_capital",
    "fee_rate",
    "seed",
    "risk_per_trade_pct",
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_stop_pct",
  ].forEach((key) => {
    if (payload[key] === "" || payload[key] === null) {
      delete payload[key];
      return;
    }
    payload[key] = Number(payload[key]);
  });

  lastSimulationContext = {
    market: payload.market || null,
    interval: payload.interval || null,
    useLiveData: payload.use_live_data,
  };

  try {
    const report = await simulateStrategy(payload);
    updateMetrics(report);
    renderEquityCurve(report.equity_curve);
  } catch (error) {
    updateEquityNote([]);
    resetIntegrityBadge();
    alert(error.message);
  }
}

async function handleSyntheticData() {
  const seed = Math.floor(Math.random() * 10_000);
  try {
    const data = await requestApi(`/prices/synthetic?seed=${seed}`);
    if (!Array.isArray(data) || !data.length) {
      alert("샘플 시세 생성에 실패했습니다. 다시 시도해주세요.");
      return;
    }

    const closes = data.map((item) => Number(item.close) || 0);
    lastSimulationContext = {
      market: "SYNTHETIC",
      interval: `${data.length} 샘플`,
      useLiveData: false,
    };
    renderEquityCurve(closes);
    alert(
      `샘플 시세 ${data.length}건을 불러왔습니다. 전략 파라미터에서 랜덤 시드를 ${seed}로 설정해 곡선 반응을 비교해보세요.`,
    );
  } catch (error) {
    alert(error.message);
  }
}

const parseJsonInput = (elementId, description) => {
  const raw = document.getElementById(elementId).value.trim();
  if (!raw) {
    return {};
  }
  try {
    const parsed = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      throw new Error();
    }
    return parsed;
  } catch (error) {
    throw new Error(`${description} JSON 형식을 확인해주세요.`);
  }
};

async function handleRebalance(event, options = {}) {
  event?.preventDefault();
  const { silent = false } = options;
  let current;
  let target;

  try {
    current = parseJsonInput("current-positions", "현재 포지션");
    target = parseJsonInput("target-allocations", "목표 비중");
  } catch (error) {
    if (silent && rebalanceOutputEl) {
      rebalanceOutputEl.textContent = error.message;
    } else {
      alert(error.message);
    }
    return;
  }

  const portfolioValue = Number(portfolioValueInput?.value ?? 0);
  if (!Number.isFinite(portfolioValue) || portfolioValue <= 0) {
    if (silent && rebalanceOutputEl) {
      rebalanceOutputEl.textContent = "포트폴리오 가치를 올바르게 입력해주세요.";
    } else {
      alert("포트폴리오 가치를 올바르게 입력해주세요.");
    }
    return;
  }

  try {
    const data = await requestApi("/portfolio/rebalance", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        current_positions: current,
        target_allocations: target,
        portfolio_value: portfolioValue,
      }),
    });
    rebalanceOutputEl.textContent = JSON.stringify(data.orders, null, 2);
  } catch (error) {
    if (silent && rebalanceOutputEl) {
      rebalanceOutputEl.textContent = error.message;
    } else {
      alert(error.message);
    }
  }
}

const parseAssetArray = (elementId, description) => {
  const raw = document.getElementById(elementId).value.trim();
  if (!raw) {
    return [];
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new Error(`${description} 형식을 JSON 배열로 입력해주세요.`);
  }

  if (!Array.isArray(parsed)) {
    throw new Error(`${description}은(는) 배열 형태여야 합니다.`);
  }

  parsed.forEach((asset, index) => {
    if (
      !asset ||
      typeof asset.symbol !== "string" ||
      typeof asset.weight !== "number" ||
      typeof asset.expected_return_pct !== "number" ||
      typeof asset.expected_volatility_pct !== "number"
    ) {
      throw new Error(`${description} ${index + 1}번째 항목을 확인해주세요.`);
    }
  });
  return parsed;
};

const renderBlueprint = (plan) => {
  if (!blueprintOutputEl) return;
  if (!plan.allocations.length) {
    blueprintOutputEl.textContent = "설계 결과가 비어 있습니다. 입력 값을 확인해주세요.";
    return;
  }

  const { summary } = plan;
  const bucketWeights = summary.bucket_weights_pct;
  const bucketAmounts = summary.bucket_amounts;

  const rows = plan.allocations
    .map(
      (allocation) => `
        <tr>
          <td>${allocation.symbol}</td>
          <td>${allocation.bucket === "stable" ? "안정" : "공격"}</td>
          <td>${percentFormatter.format(allocation.weight_pct)}%</td>
          <td>${formatCurrency(allocation.amount)} KRW</td>
          <td>${percentFormatter.format(allocation.expected_return_pct)}%</td>
          <td>${percentFormatter.format(allocation.expected_volatility_pct)}%</td>
        </tr>
      `,
    )
    .join("");

  blueprintOutputEl.innerHTML = `
    <div class="blueprint-summary">
      <div>
        <span>예상 연 수익률</span>
        <strong>${formatPercent(summary.expected_return_pct)}</strong>
      </div>
      <div>
        <span>예상 연 변동성</span>
        <strong>${formatPercent(summary.expected_volatility_pct)}</strong>
      </div>
      <div>
        <span>예상 수익 (KRW)</span>
        <strong>${formatCurrency(summary.expected_return_currency)}</strong>
      </div>
      <div>
        <span>다변화 지수</span>
        <strong>${ratioFormatter.format(summary.diversification_ratio)}</strong>
      </div>
      <div>
        <span>안정 버킷</span>
        <strong>${formatPercent(bucketWeights.stable)} / ${formatCurrency(bucketAmounts.stable)} KRW</strong>
      </div>
      <div>
        <span>공격 버킷</span>
        <strong>${formatPercent(bucketWeights.aggressive)} / ${formatCurrency(bucketAmounts.aggressive)} KRW</strong>
      </div>
    </div>
    <div class="blueprint-table-wrapper">
      <table class="blueprint-table">
        <thead>
          <tr>
            <th>티커</th>
            <th>버킷</th>
            <th>비중</th>
            <th>금액</th>
            <th>예상 수익률</th>
            <th>예상 변동성</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
};

async function handleBlueprint(event, options = {}) {
  event?.preventDefault();
  const { silent = false } = options;

  const capitalInput = blueprintCapitalInput || document.getElementById("blueprint-capital");
  const capital = Number(capitalInput?.value ?? 0);
  const riskInput = document.getElementById("blueprint-risk");
  const riskProfile = Number(riskInput?.value ?? 0.5);

  if (!Number.isFinite(capital) || capital <= 0) {
    if (silent && blueprintOutputEl) {
      blueprintOutputEl.innerHTML = '<p class="error">투자 자본을 올바르게 입력해주세요.</p>';
    } else {
      alert("투자 자본을 올바르게 입력해주세요.");
    }
    return;
  }

  let stableAssets;
  let aggressiveAssets;
  try {
    stableAssets = parseAssetArray("blueprint-stable", "안정 자산");
    aggressiveAssets = parseAssetArray("blueprint-aggressive", "공격 자산");
  } catch (error) {
    if (silent && blueprintOutputEl) {
      blueprintOutputEl.innerHTML = `<p class="error">${error.message}</p>`;
    } else {
      alert(error.message);
    }
    return;
  }

  try {
    const plan = await requestApi("/portfolio/blueprint", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        capital,
        risk_profile: riskProfile,
        stable_assets: stableAssets,
        aggressive_assets: aggressiveAssets,
      }),
    });
    renderBlueprint(plan);
  } catch (error) {
    if (silent && blueprintOutputEl) {
      blueprintOutputEl.innerHTML = `<p class="error">${error.message}</p>`;
    } else {
      alert(error.message);
    }
  }
}

const handleOrderSubmit = async (event) => {
  event.preventDefault();
  if (!orderForm || !orderResultEl) return;
  const formData = new FormData(orderForm);
  const payload = {
    mode: formData.get("order-mode") || "paper",
    market: formData.get("market"),
    side: formData.get("side") || "bid",
    ord_type: formData.get("ord_type") || "limit",
    price: parseNumeric(formData.get("price")),
    volume: parseNumeric(formData.get("volume")),
  };

  if (payload.volume === null || payload.volume <= 0) {
    orderResultEl.textContent = "유효한 수량을 입력하세요.";
    return;
  }

  if (payload.price === null || payload.price <= 0) {
    delete payload.price;
    if (payload.ord_type === "limit") {
      payload.ord_type = "market";
    }
  } else if (payload.ord_type === "market") {
    delete payload.price;
  }

  const isPaperMode = (payload.mode || "paper") === "paper";
  if (isPaperMode) {
    setPaperHeartbeat("loading", "주문 처리 중...");
  }

  try {
    const response = await requestApi("/trading/order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    orderResultEl.textContent = JSON.stringify(response, null, 2);
    if (response.balance) {
      updatePaperSummary(response.balance);
    }
    refreshTradeHistory().catch(() => {});
  } catch (error) {
    orderResultEl.textContent = error.message;
    if (isPaperMode) {
      setPaperHeartbeat("warning", "주문 실패");
    }
  }
};

const performPaperReset = async (initialCash, { successMessage = "페이퍼 계좌가 초기화되었습니다." } = {}) => {
  if (!initialCash || initialCash <= 0) {
    throw new Error("초기 자본을 올바르게 입력하세요.");
  }

  setPaperHeartbeat("loading", "리셋 중...");
  try {
    const balance = await requestApi("/trading/paper/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial_cash: initialCash }),
    });
    updatePaperSummary(balance);
    if (orderResultEl) {
      orderResultEl.textContent = successMessage;
    }
    return balance;
  } catch (error) {
    if (paperSummaryEl) {
      paperSummaryEl.innerHTML = `<p class="error">${error.message}</p>`;
    }
    setPaperHeartbeat("warning", "리셋 실패");
    throw error;
  }
};

let pendingPaperResetAmount = null;

const openPaperResetDialog = (amount) => {
  pendingPaperResetAmount = amount;
  if (!paperResetDialog) return;
  if (paperResetDialogMessage) {
    paperResetDialogMessage.innerHTML = `현재 페이퍼 계좌를 <strong>${formatCurrencyWithSymbol(Math.round(amount))}</strong> 기준으로 초기화합니다. 계속하시겠습니까?`;
  }
  paperResetDialog.hidden = false;
  paperResetDialog.classList.add("is-open");
  paperResetConfirmBtn?.focus();
};

const closePaperResetDialog = () => {
  pendingPaperResetAmount = null;
  if (!paperResetDialog) return;
  paperResetDialog.classList.remove("is-open");
  paperResetDialog.hidden = true;
};

const handlePaperReset = async (event) => {
  event.preventDefault();
  if (!paperInitialCashInput) return;
  const initialCash = parseNumeric(paperInitialCashInput.value);
  if (!initialCash || initialCash <= 0) {
    if (paperSummaryEl) {
      paperSummaryEl.innerHTML = '<p class="error">초기 자본을 올바르게 입력하세요.</p>';
    }
    return;
  }
  try {
    await performPaperReset(initialCash);
  } catch (error) {
    alert(error.message);
  }
};

const handlePaperHardReset = () => {
  const initialCash = parseNumeric(paperInitialCashInput?.value) || 20000000;
  openPaperResetDialog(initialCash);
};

const handlePaperResetConfirm = async () => {
  if (pendingPaperResetAmount === null) {
    closePaperResetDialog();
    return;
  }
  const amount = pendingPaperResetAmount;
  closePaperResetDialog();
  try {
    await performPaperReset(amount, { successMessage: "페이퍼 계좌 전체 초기화가 완료되었습니다." });
  } catch (error) {
    alert(error.message);
  }
};

const handlePaperMark = async (event) => {
  event.preventDefault();
  if (!paperMarkMarketInput || !paperMarkPriceInput) return;
  const market = paperMarkMarketInput.value.trim();
  const price = parseNumeric(paperMarkPriceInput.value);
  if (!market || !price) {
    if (orderResultEl) {
      orderResultEl.textContent = "마켓과 시세를 모두 입력하세요.";
    }
    return;
  }
  setPaperHeartbeat("loading", "시세 반영 중...");
  try {
    const balance = await requestApi("/trading/paper/mark", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ market, price }),
    });
    updatePaperSummary(balance);
    if (orderResultEl) {
      orderResultEl.textContent = `${market} 시세가 ${formatCurrency(price)}으로 갱신되었습니다.`;
    }
  } catch (error) {
    if (orderResultEl) {
      orderResultEl.textContent = error.message;
    }
    setPaperHeartbeat("warning", "시세 반영 실패");
  }
};

const handleLiveBalance = async () => {
  if (!liveBalanceOutput) return;
  try {
    const response = await requestApi("/trading/live/balances");
    liveBalanceOutput.textContent = JSON.stringify(response, null, 2);
  } catch (error) {
    liveBalanceOutput.textContent = error.message;
  }
};

const scheduleBlueprintAuto = createAutoRunner(() => handleBlueprint(undefined, { silent: true }));
const scheduleRebalanceAuto = createAutoRunner(() => handleRebalance(undefined, { silent: true }));

marketSearchInput?.addEventListener("input", () => {
  renderMarketResults();
});

marketBaseButtons.forEach((button) => {
  button.addEventListener("click", () => {
    marketBaseButtons.forEach((chip) => chip.classList.remove("chip--active"));
    button.classList.add("chip--active");
    selectedBaseCurrency = (button.dataset.marketBase || "ALL").toUpperCase();
    renderMarketResults();
    loadRecommendations({ base: selectedBaseCurrency }).catch(() => {});
  });
});

blueprintForm?.addEventListener("input", scheduleBlueprintAuto);
blueprintForm?.addEventListener("change", scheduleBlueprintAuto);

[currentPositionsInput, targetAllocationsInput, portfolioValueInput].forEach((element) => {
  element?.addEventListener("input", scheduleRebalanceAuto);
  element?.addEventListener("change", scheduleRebalanceAuto);
});

marketGroupsEl?.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-market-group]");
  if (!trigger) return;
  const key = trigger.dataset.marketGroup;
  selectedMarketGroup = selectedMarketGroup === key ? null : key;
  renderMarketGroups(cachedMarketGroups);
  renderMarketResults();
});

marketResultsEl?.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-market-action]");
  if (!trigger) return;
  const marketCode = (trigger.dataset.marketCode || "").toUpperCase();
  const action = trigger.dataset.marketAction;
  applyMarketToForms(marketCode, action);
});

assistantRunBtn?.addEventListener("click", handleAssistant);
assistantClearBtn?.addEventListener("click", () => {
  assistantHistory = [];
  renderAssistantHistoryList(assistantHistory);
  setAssistantStatus("대기 중", "ghost");
});

if (apiEndpointInput) {
  apiEndpointInput.value = getApiBase();
  apiEndpointInput.addEventListener("change", (event) => {
    const next = event.target.value.trim();
    if (!next) return;
    setApiBase(next);
  });
}

apiRefreshBtn?.addEventListener("click", () => {
  refreshApiStatus();
});

recommendationsRefreshBtn?.addEventListener("click", () => {
  loadRecommendations({
    base: recommendationsState.base,
    interval: recommendationsState.interval,
  }).catch(() => {});
});

if (riskSlider) {
  updateRiskLabel(riskSlider.value);
  riskSlider.addEventListener("input", (event) => {
    updateRiskLabel(event.target.value);
  });
}

if (aiRiskSlider) {
  updateAIRiskLabel(aiRiskSlider.value);
  aiRiskSlider.addEventListener("input", (event) => {
    updateAIRiskLabel(event.target.value);
  });
}

if (autopilotRiskInput) {
  updateAutopilotRiskLabel(autopilotRiskInput.value);
  autopilotRiskInput.addEventListener("input", (event) => {
    updateAutopilotRiskLabel(event.target.value);
  });
}

if (copilotRiskSlider) {
  updateCopilotRiskLabel(copilotRiskSlider.value);
  copilotRiskSlider.addEventListener("input", (event) => {
    updateCopilotRiskLabel(event.target.value);
  });
}

document.getElementById("strategy-form")?.addEventListener("submit", handleSimulation);
document.getElementById("simulate-btn")?.addEventListener("click", handleSimulation);
document.getElementById("generate-data-btn")?.addEventListener("click", handleSyntheticData);
document.getElementById("rebalance-btn")?.addEventListener("click", handleRebalance);
document.getElementById("blueprint-form")?.addEventListener("submit", handleBlueprint);
document.getElementById("blueprint-btn")?.addEventListener("click", handleBlueprint);
autopilotForm?.addEventListener("submit", handleAutopilotStart);
autopilotStopBtn?.addEventListener("click", handleAutopilotStop);
aiPortfolioForm?.addEventListener("submit", handleAiPortfolio);
copilotForm?.addEventListener("submit", handleCopilot);
orderForm?.addEventListener("submit", handleOrderSubmit);
paperResetForm?.addEventListener("submit", handlePaperReset);
paperHardResetBtn?.addEventListener("click", handlePaperHardReset);
paperResetConfirmBtn?.addEventListener("click", handlePaperResetConfirm);
paperResetCancelBtn?.addEventListener("click", () => {
  closePaperResetDialog();
});
paperRefreshBtn?.addEventListener("click", fetchPaperStatus);
paperMarkForm?.addEventListener("submit", handlePaperMark);
liveBalanceBtn?.addEventListener("click", handleLiveBalance);
liveRefreshBtn?.addEventListener("click", () => {
  refreshLiveMarket();
});
tradeHistoryRefreshBtn?.addEventListener("click", () => {
  refreshTradeHistory().catch(() => {});
});
paperStatusMarketInput?.addEventListener("blur", () => {
  paperStatusMarketInput.value = paperStatusMarketInput.value.toUpperCase();
  fetchPaperStatus();
});
paperStatusIntervalSelect?.addEventListener("change", fetchPaperStatus);
paperStatusApplyBtn?.addEventListener("click", (event) => {
  event.preventDefault();
  fetchPaperStatus();
});

const handleBackToTopVisibility = () => {
  if (!backToTopBtn) return;
  if (window.scrollY > 400) {
    backToTopBtn.classList.add("is-visible");
  } else {
    backToTopBtn.classList.remove("is-visible");
  }
};

if (backToTopBtn) {
  backToTopBtn.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}

window.addEventListener("scroll", handleBackToTopVisibility, { passive: true });
handleBackToTopVisibility();
chatTestBtn?.addEventListener("click", (event) => {
  event.preventDefault();
  handleChatTest();
});
navToggleBtn?.addEventListener("click", () => {
  const isOpen = navLinksList?.classList.toggle("is-open") ?? false;
  navToggleBtn.setAttribute("aria-expanded", String(isOpen));
});

navLinksList?.addEventListener("click", (event) => {
  if (!navLinksList.classList.contains("is-open")) return;
  const link = event.target.closest("a");
  if (link) {
    navLinksList.classList.remove("is-open");
    navToggleBtn?.setAttribute("aria-expanded", "false");
  }
});

paperResetDialog?.addEventListener("click", (event) => {
  if (event.target === paperResetDialog) {
    closePaperResetDialog();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && paperResetDialog?.classList.contains("is-open")) {
    closePaperResetDialog();
  }
});
aiRefreshBtn?.addEventListener("click", () => {
  refreshMarketIntelligence().catch(() => {});
});
diagnosticsRefreshBtn?.addEventListener("click", () => {
  refreshDiagnostics().catch(() => {});
});
selfCheckRefreshBtn?.addEventListener("click", () => {
  refreshSelfCheck({ force: true }).catch(() => {});
});
liveIntervalSelect?.addEventListener("change", () => {
  refreshLiveMarket();
});
liveMarketInput?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    refreshLiveMarket();
  }
});

applyDefaultCapitalValues();
renderAssistantHistoryList(assistantHistory);
setAssistantStatus("대기 중", "ghost");
initCollapsibles();
refreshApiStatus();
refreshChatStatus().catch(() => {});
fetchPaperStatus();
fetchAutopilotStatus().catch(() => {});
refreshLiveMarket().catch(() => {});
refreshMarketIntelligence().catch(() => {});
refreshDiagnostics().catch(() => {});
refreshSelfCheck().catch(() => {});
handleAiPortfolio().catch(() => {});
handleBlueprint(undefined, { silent: true }).catch(() => {});
handleRebalance(undefined, { silent: true }).catch(() => {});
refreshNews().catch(() => {});
handleCopilot().catch(() => {});
refreshTradeHistory().catch(() => {});
startIntervalTask(
  () => handleBlueprint(undefined, { silent: true }),
  AUTO_REFRESH_INTERVALS.blueprint
);
startIntervalTask(
  () => handleRebalance(undefined, { silent: true }),
  AUTO_REFRESH_INTERVALS.rebalance
);
startIntervalTask(
  () => handleAiPortfolio(undefined, { silent: true }),
  AUTO_REFRESH_INTERVALS.aiPortfolio
);
startIntervalTask(() => {
  if (copilotQuestionInput?.value.trim()) {
    return handleCopilot(undefined, { silent: true });
  }
  return undefined;
}, AUTO_REFRESH_INTERVALS.copilot);
setInterval(() => {
  refreshLiveMarket().catch(() => {});
}, 60_000);
setInterval(() => {
  refreshMarketIntelligence().catch(() => {});
}, 60_000);
setInterval(() => {
  refreshNews().catch(() => {});
}, 300_000);
setInterval(() => {
  fetchPaperStatus().catch(() => {});
}, PAPER_STATUS_INTERVAL);
setInterval(() => {
  fetchAutopilotStatus().catch(() => {});
}, AUTOPILOT_STATUS_INTERVAL);
setInterval(() => {
  refreshChatStatus().catch(() => {});
}, 300_000);
setInterval(() => {
  refreshDiagnostics().catch(() => {});
}, 300_000);
setInterval(() => {
  refreshSelfCheck().catch(() => {});
}, 240_000);
setInterval(updateAutopilotCountdown, 5_000);
setInterval(() => {
  refreshTradeHistory().catch(() => {});
}, 30_000);
