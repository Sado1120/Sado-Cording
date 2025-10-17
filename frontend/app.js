const STORAGE_KEY = "sado-trade-bot-api-base";

const DEFAULT_API_BASE = (() => {
  const { origin } = window.location;
  if (origin.includes(":8501")) {
    return origin.replace(":8501", ":8000");
  }
  return "http://127.0.0.1:8000";
})();

const percentFormatter = new Intl.NumberFormat("ko-KR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const ratioFormatter = new Intl.NumberFormat("ko-KR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const currencyFormatter = new Intl.NumberFormat("ko-KR");

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
const rebalanceOutputEl = document.getElementById("rebalance-output");
const blueprintOutputEl = document.getElementById("blueprint-output");
const yearEl = document.getElementById("year");

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

const orderForm = document.getElementById("order-form");
const orderResultEl = document.getElementById("order-result");
const paperSummaryEl = document.getElementById("paper-summary");
const paperResetForm = document.getElementById("paper-reset-form");
const paperInitialCashInput = document.getElementById("paper-initial-cash");
const paperRefreshBtn = document.getElementById("paper-refresh-btn");
const paperMarkForm = document.getElementById("paper-mark-form");
const paperMarkMarketInput = document.getElementById("paper-mark-market");
const paperMarkPriceInput = document.getElementById("paper-mark-price");
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

yearEl.textContent = new Date().getFullYear();

let chartInstance;
let liveChartInstance;

const normaliseBase = (value) => {
  if (!value) {
    return DEFAULT_API_BASE;
  }
  return value.replace(/\/+$/, "");
};

let apiBase = normaliseBase(localStorage.getItem(STORAGE_KEY) || DEFAULT_API_BASE);

const getApiBase = () => apiBase;

const setApiBase = (value) => {
  const normalised = normaliseBase(value);
  apiBase = normalised;
  localStorage.setItem(STORAGE_KEY, normalised);
  if (apiEndpointInput && apiEndpointInput.value !== normalised) {
    apiEndpointInput.value = normalised;
  }
  refreshApiStatus();
  handleSimulation();
};

const updateApiStatus = (state, message) => {
  if (!apiStatusEl) return;
  apiStatusEl.dataset.status = state;
  apiStatusEl.textContent = message;
};

const formatPercent = (value) => `${percentFormatter.format(value)}%`;
const formatCurrency = (value) => currencyFormatter.format(Math.round(value));
const formatRatio = (value) =>
  Number.isFinite(value) && Math.abs(value) !== Infinity
    ? ratioFormatter.format(value)
    : value > 0
    ? "∞"
    : "0.00";

const parseNumeric = (value) => {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
};

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
    liveSourceEl.textContent = source === "synthetic" ? "시뮬레이터 데이터" : "업비트 실시간";
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
    updateLiveInsights(insightResponse, insightResponse.source || candleResponse.source);
  } catch (error) {
    if (liveSummaryEl) {
      liveSummaryEl.textContent = error.message;
    }
    if (liveSourceEl) {
      liveSourceEl.textContent = "연결 실패";
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

const requestApi = async (path, options = {}) => {
  const base = getApiBase();
  const url = `${base}${path}`;
  const config = { ...options };
  config.headers = {
    ...(options.headers || {}),
  };

  let response;
  try {
    response = await fetch(url, config);
  } catch (error) {
    throw new Error("API 연결에 실패했습니다. 엔드포인트를 확인해주세요.");
  }

  const isJson = response.headers.get("content-type")?.includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    const detail = typeof payload === "object" && payload !== null ? payload.detail : null;
    throw new Error(detail || response.statusText || "요청에 실패했습니다.");
  }

  return payload;
};

const renderPaperSummary = (balance) => {
  if (!balance) {
    return "페이퍼 계좌 정보를 불러오지 못했습니다.";
  }

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

  return `${headline}<div class="paper-balance__section"><h4>보유 자산</h4>${positions}</div><div class="paper-balance__section"><h4>최근 주문</h4>${orders}</div>`;
};

const updatePaperSummary = (balance) => {
  if (!paperSummaryEl) return;
  paperSummaryEl.innerHTML = renderPaperSummary(balance);
};

const fetchPaperStatus = async () => {
  if (!paperSummaryEl) return;
  try {
    const balance = await requestApi("/trading/paper/status");
    updatePaperSummary(balance);
  } catch (error) {
    paperSummaryEl.innerHTML = `<p class="error">${error.message}</p>`;
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

const riskDescriptors = [
  { threshold: 0.2, label: "초안정" },
  { threshold: 0.5, label: "안정형" },
  { threshold: 0.8, label: "균형형" },
  { threshold: 1.01, label: "공격형" },
];

const updateRiskLabel = (value) => {
  if (!riskLabel) return;
  const numeric = Number(value);
  const descriptor = riskDescriptors.find((item) => numeric <= item.threshold)?.label || "커스텀";
  riskLabel.textContent = `${descriptor} (${percentFormatter.format(numeric * 100)}%)`;
};

const updateAlphaBriefing = (report) => {
  if (!alphaBriefingEl) return;

  const payoff = formatRatio(report.trade_summary.payoff_ratio || report.win_loss_ratio);
  const lines = [
    `• 켈리 권장 비중: ${formatPercent(report.kelly_fraction_pct)}`,
    `• 평균 낙폭: ${formatPercent(report.average_drawdown_pct)} | 페인 인덱스: ${formatPercent(report.pain_index)}`,
    `• 왜도/첨도: ${ratioFormatter.format(report.skewness)} / ${ratioFormatter.format(report.kurtosis)}`,
    `• 최대 반등폭: ${formatPercent(report.max_runup_pct)} | 시장 노출: ${formatPercent(report.exposure_time_pct)}`,
    `• 페이오프 비율: ${payoff}`,
  ];

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

function updateMetrics(report) {
  totalReturnEl.textContent = formatPercent(report.total_return_pct);
  annualReturnEl.textContent = formatPercent(report.annualized_return_pct);
  drawdownEl.textContent = formatPercent(report.max_drawdown_pct);
  tradeCountEl.textContent = `${report.trade_summary.count}건`;
  tradeWinrateEl.textContent = `승률 ${ratioFormatter.format(report.trade_summary.win_rate)}%`;
  tradeAvgEl.textContent = `평균 ${ratioFormatter.format(report.trade_summary.avg_return_pct)}%`;
  tradeExpectancyEl.textContent = `기대 ${ratioFormatter.format(report.trade_summary.expectancy_pct)}%`;
  tradeMedianEl.textContent = `중앙값 ${ratioFormatter.format(report.trade_summary.median_return_pct)}%`;
  tradeWinLossEl.textContent = `승패비 ${formatRatio(report.trade_summary.win_loss_ratio)}`;
  if (tradePayoffEl) {
    tradePayoffEl.textContent = `페이오프 ${formatRatio(report.trade_summary.payoff_ratio)}`;
  }
  tradeBestEl.textContent = `최대수익 ${ratioFormatter.format(report.trade_summary.largest_win_pct)}%`;
  tradeWorstEl.textContent = `최대손실 ${ratioFormatter.format(report.trade_summary.largest_loss_pct)}%`;

  volatilityEl.textContent = formatPercent(report.volatility_pct);
  sharpeEl.textContent = ratioFormatter.format(report.sharpe_ratio);
  sortinoEl.textContent = ratioFormatter.format(report.sortino_ratio);
  calmarEl.textContent = ratioFormatter.format(report.calmar_ratio);
  varEl.textContent = formatPercent(report.value_at_risk_pct);
  exposureEl.textContent = formatPercent(report.exposure_time_pct);
  profitFactorEl.textContent = ratioFormatter.format(report.profit_factor);
  expectancyEl.textContent = formatPercent(report.expectancy_pct);
  holdEl.textContent = `${ratioFormatter.format(report.avg_trade_duration_bars)}봉`;
  ulcerEl.textContent = ratioFormatter.format(report.ulcer_index);
  downsideEl.textContent = formatPercent(report.downside_deviation_pct);
  recoveryEl.textContent = formatRatio(report.recovery_factor);
  avgWinEl.textContent = formatPercent(report.average_win_pct);
  avgLossEl.textContent = formatPercent(report.average_loss_pct);
  winLossEl.textContent = formatRatio(report.win_loss_ratio);
  tailEl.textContent = formatRatio(report.tail_ratio);
  omegaEl.textContent = formatRatio(report.omega_ratio);
  kellyEl.textContent = formatPercent(report.kelly_fraction_pct);
  streakWinEl.textContent = `${report.max_consecutive_wins}회`;
  streakLossEl.textContent = `${report.max_consecutive_losses}회`;
  skewnessEl.textContent = ratioFormatter.format(report.skewness);
  kurtosisEl.textContent = ratioFormatter.format(report.kurtosis);
  avgDrawdownEl.textContent = formatPercent(report.average_drawdown_pct);
  painEl.textContent = formatPercent(report.pain_index);
  runupEl.textContent = formatPercent(report.max_runup_pct);

  mcMedianEl.textContent = formatPercent(report.monte_carlo_summary.median_return_pct);
  mcP05El.textContent = formatPercent(report.monte_carlo_summary.p05_return_pct);
  mcP95El.textContent = formatPercent(report.monte_carlo_summary.p95_return_pct);
  mcAvgEl.textContent = formatPercent(report.monte_carlo_summary.average_return_pct);

  updateAlphaBriefing(report);
}

function renderTrades(trades) {
  if (!tradeTableBody) return;
  tradeTableBody.innerHTML = "";
  const formatter = new Intl.NumberFormat("ko-KR", {
    maximumFractionDigits: 2,
  });

  const exitReasonMap = {
    stop_loss: "스톱로스",
    take_profit: "테이크프로핏",
    trailing_stop: "트레일링 스톱",
    ema_cross: "EMA 크로스",
    end_of_data: "데이터 종료",
  };

  trades.forEach((trade) => {
    const row = document.createElement("tr");
    row.innerHTML = `
      <td>${new Date(trade.entry_time).toLocaleDateString()}</td>
      <td>${new Date(trade.exit_time).toLocaleDateString()}</td>
      <td>${formatter.format(trade.entry_price)}</td>
      <td>${formatter.format(trade.exit_price)}</td>
      <td>${trade.quantity.toFixed(4)}</td>
      <td>${formatter.format(trade.pnl)}</td>
      <td>${trade.return_pct.toFixed(2)}%</td>
      <td>${trade.duration_bars}</td>
      <td>${exitReasonMap[trade.exit_reason] || trade.exit_reason}</td>
    `;
    tradeTableBody.appendChild(row);
  });
}

function renderEquityCurve(values) {
  const ctx = document.getElementById("equity-chart");
  if (!ctx) return;
  const labels = values.map((_, idx) => idx + 1);

  if (chartInstance) {
    chartInstance.destroy();
  }

  chartInstance = new Chart(ctx, {
    type: "line",
    data: {
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
    },
    options: {
      maintainAspectRatio: false,
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
    },
  });
}

async function handleSimulation(event) {
  event?.preventDefault();

  const form = document.getElementById("strategy-form");
  if (!form) return;
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());

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

  try {
    const report = await simulateStrategy(payload);
    updateMetrics(report);
    renderTrades(report.trades);
    renderEquityCurve(report.equity_curve);
  } catch (error) {
    alert(error.message);
  }
}

async function handleSyntheticData() {
  const seed = Math.floor(Math.random() * 10_000);
  try {
    const data = await requestApi(`/prices/synthetic?seed=${seed}`);
    alert(`랜덤 시세 ${data.length}건이 생성되었습니다. 전략 파라미터의 시드를 ${seed}로 설정해보세요!`);
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

async function handleRebalance() {
  let current;
  let target;

  try {
    current = parseJsonInput("current-positions", "현재 포지션");
    target = parseJsonInput("target-allocations", "목표 비중");
  } catch (error) {
    alert(error.message);
    return;
  }

  const portfolioValue = Number(document.getElementById("portfolio-value").value);
  if (!Number.isFinite(portfolioValue) || portfolioValue <= 0) {
    alert("포트폴리오 가치를 올바르게 입력해주세요.");
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
    alert(error.message);
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

async function handleBlueprint(event) {
  event?.preventDefault();

  const capital = Number(document.getElementById("blueprint-capital").value);
  const riskProfile = Number(document.getElementById("blueprint-risk").value);

  if (!Number.isFinite(capital) || capital <= 0) {
    alert("투자 자본을 올바르게 입력해주세요.");
    return;
  }

  let stableAssets;
  let aggressiveAssets;
  try {
    stableAssets = parseAssetArray("blueprint-stable", "안정 자산");
    aggressiveAssets = parseAssetArray("blueprint-aggressive", "공격 자산");
  } catch (error) {
    alert(error.message);
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
    alert(error.message);
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

  if (!payload.volume) {
    orderResultEl.textContent = "유효한 수량을 입력하세요.";
    return;
  }

  if (payload.price === null) {
    delete payload.price;
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
  } catch (error) {
    orderResultEl.textContent = error.message;
  }
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
    const balance = await requestApi("/trading/paper/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial_cash: initialCash }),
    });
    updatePaperSummary(balance);
    if (orderResultEl) {
      orderResultEl.textContent = "페이퍼 계좌가 초기화되었습니다.";
    }
  } catch (error) {
    if (paperSummaryEl) {
      paperSummaryEl.innerHTML = `<p class="error">${error.message}</p>`;
    }
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

if (riskSlider) {
  updateRiskLabel(riskSlider.value);
  riskSlider.addEventListener("input", (event) => {
    updateRiskLabel(event.target.value);
  });
}

document.getElementById("strategy-form")?.addEventListener("submit", handleSimulation);
document.getElementById("simulate-btn")?.addEventListener("click", handleSimulation);
document.getElementById("generate-data-btn")?.addEventListener("click", handleSyntheticData);
document.getElementById("rebalance-btn")?.addEventListener("click", handleRebalance);
document.getElementById("blueprint-form")?.addEventListener("submit", handleBlueprint);
document.getElementById("blueprint-btn")?.addEventListener("click", handleBlueprint);
orderForm?.addEventListener("submit", handleOrderSubmit);
paperResetForm?.addEventListener("submit", handlePaperReset);
paperRefreshBtn?.addEventListener("click", fetchPaperStatus);
paperMarkForm?.addEventListener("submit", handlePaperMark);
liveBalanceBtn?.addEventListener("click", handleLiveBalance);
liveRefreshBtn?.addEventListener("click", () => {
  refreshLiveMarket();
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

refreshApiStatus();
fetchPaperStatus();
handleSimulation().catch(() => {});
refreshLiveMarket().catch(() => {});
refreshNews().catch(() => {});
setInterval(() => {
  refreshLiveMarket().catch(() => {});
}, 60_000);
setInterval(() => {
  refreshNews().catch(() => {});
}, 300_000);
