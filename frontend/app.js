const API_BASE = "http://127.0.0.1:8000";

const totalReturnEl = document.getElementById("metric-total-return");
const annualReturnEl = document.getElementById("metric-annual-return");
const drawdownEl = document.getElementById("metric-drawdown");
const tradeCountEl = document.getElementById("metric-trade-count");
const tradeWinrateEl = document.getElementById("metric-trade-winrate");
const tradeAvgEl = document.getElementById("metric-trade-avg");
const tradeTableBody = document.getElementById("trade-table");
const rebalanceOutputEl = document.getElementById("rebalance-output");
const yearEl = document.getElementById("year");

yearEl.textContent = new Date().getFullYear();

let chartInstance;

const formatPercent = (value) => `${value.toFixed(2)}%`;

async function simulateStrategy(formValues) {
  const response = await fetch(`${API_BASE}/strategies/simulate`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(formValues),
  });

  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "시뮬레이션에 실패했습니다.");
  }

  return response.json();
}

function updateMetrics(report) {
  totalReturnEl.textContent = formatPercent(report.total_return_pct);
  annualReturnEl.textContent = formatPercent(report.annualized_return_pct);
  drawdownEl.textContent = formatPercent(report.max_drawdown_pct);
  tradeCountEl.textContent = `${report.trade_summary.count}건`;
  tradeWinrateEl.textContent = `승률 ${report.trade_summary.win_rate.toFixed(1)}%`;
  tradeAvgEl.textContent = `평균 ${report.trade_summary.avg_return_pct.toFixed(2)}%`;
}

function renderTrades(trades) {
  tradeTableBody.innerHTML = "";
  const formatter = new Intl.NumberFormat("ko-KR", {
    maximumFractionDigits: 2,
  });

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
    `;
    tradeTableBody.appendChild(row);
  });
}

function renderEquityCurve(values) {
  const ctx = document.getElementById("equity-chart");
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
            callback: (value) => new Intl.NumberFormat("ko-KR", { notation: "compact" }).format(value),
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
            label: (context) => `Equity: ${new Intl.NumberFormat("ko-KR").format(context.parsed.y)} KRW`,
          },
        },
      },
    },
  });
}

async function handleSimulation(event) {
  event?.preventDefault();

  const form = document.getElementById("strategy-form");
  const formData = new FormData(form);
  const payload = Object.fromEntries(formData.entries());

  ["fast_period", "slow_period"].forEach((key) => {
    payload[key] = Number.parseInt(payload[key], 10);
  });
  ["initial_capital", "fee_rate", "seed"].forEach((key) => {
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
  const response = await fetch(`${API_BASE}/prices/synthetic?seed=${seed}`);
  if (!response.ok) {
    alert("시세 데이터를 가져오지 못했습니다.");
    return;
  }
  const data = await response.json();
  alert(`랜덤 시세 ${data.length}건이 생성되었습니다. 전략 파라미터의 시드를 ${seed}로 설정해보세요!`);
}

async function handleRebalance() {
  let current;
  let target;

  try {
    current = JSON.parse(document.getElementById("current-positions").value || "{}");
    target = JSON.parse(document.getElementById("target-allocations").value || "{}");
  } catch (error) {
    alert("JSON 형식이 올바른지 확인해주세요.");
    return;
  }

  const portfolioValue = Number(document.getElementById("portfolio-value").value);

  const response = await fetch(`${API_BASE}/portfolio/rebalance`, {
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

  if (!response.ok) {
    const error = await response.json();
    alert(error.detail || "리밸런싱 계산에 실패했습니다.");
    return;
  }

  const data = await response.json();
  rebalanceOutputEl.textContent = JSON.stringify(data.orders, null, 2);
}

document.getElementById("strategy-form").addEventListener("submit", handleSimulation);
document.getElementById("simulate-btn").addEventListener("click", handleSimulation);
document.getElementById("generate-data-btn").addEventListener("click", handleSyntheticData);
document.getElementById("rebalance-btn").addEventListener("click", handleRebalance);

handleSimulation();
