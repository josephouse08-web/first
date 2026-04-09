"""실시간 웹 대시보드 - Flask + WebSocket"""

import json
import logging
import threading
import time
from datetime import datetime

logger = logging.getLogger("crypto_bot.dashboard")

# HTML 템플릿을 인라인으로 포함 (별도 파일 불필요)
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CryptoBot Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', -apple-system, sans-serif;
            background: #0a0e17;
            color: #e1e5eb;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #1a1f2e 0%, #0d1117 100%);
            padding: 20px 30px;
            border-bottom: 1px solid #21262d;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 {
            font-size: 24px;
            background: linear-gradient(135deg, #58a6ff, #bc8cff);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .status-badge {
            padding: 6px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
        }
        .status-running { background: #0d3117; color: #3fb950; border: 1px solid #238636; }
        .status-stopped { background: #3d1f1f; color: #f85149; border: 1px solid #da3633; }
        .container { padding: 20px 30px; max-width: 1600px; margin: auto; }
        .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 16px; margin-bottom: 24px; }
        .card {
            background: #161b22;
            border: 1px solid #21262d;
            border-radius: 12px;
            padding: 20px;
        }
        .card-label { font-size: 12px; color: #8b949e; text-transform: uppercase; letter-spacing: 1px; }
        .card-value { font-size: 28px; font-weight: 700; margin-top: 8px; }
        .positive { color: #3fb950; }
        .negative { color: #f85149; }
        .section { margin-bottom: 24px; }
        .section-title { font-size: 18px; margin-bottom: 12px; color: #c9d1d9; }
        table { width: 100%; border-collapse: collapse; }
        th { text-align: left; padding: 10px 16px; background: #0d1117; color: #8b949e;
             font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
        td { padding: 12px 16px; border-bottom: 1px solid #21262d; }
        tr:hover { background: #1c2333; }
        .side-buy { color: #3fb950; font-weight: 600; }
        .side-sell { color: #f85149; font-weight: 600; }
        .strategies {
            display: flex; gap: 10px; flex-wrap: wrap;
        }
        .strategy-tag {
            background: #1f2937; border: 1px solid #374151; padding: 6px 14px;
            border-radius: 8px; font-size: 13px; color: #93c5fd;
        }
        .chart-container {
            background: #161b22; border: 1px solid #21262d; border-radius: 12px;
            padding: 20px; height: 300px; position: relative; margin-bottom: 24px;
        }
        .chart-canvas { width: 100%; height: 260px; }
        .footer {
            text-align: center; padding: 20px; color: #484f58; font-size: 12px;
            border-top: 1px solid #21262d;
        }
        @media (max-width: 768px) {
            .grid { grid-template-columns: repeat(2, 1fr); }
            .container { padding: 10px 15px; }
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>CryptoBot Dashboard</h1>
        <span id="status-badge" class="status-badge status-stopped">STOPPED</span>
    </div>
    <div class="container">
        <div class="grid">
            <div class="card">
                <div class="card-label">Total Balance</div>
                <div class="card-value" id="total-balance">$0.00</div>
            </div>
            <div class="card">
                <div class="card-label">Total P&L</div>
                <div class="card-value" id="total-pnl">$0.00</div>
            </div>
            <div class="card">
                <div class="card-label">Win Rate</div>
                <div class="card-value" id="win-rate">0%</div>
            </div>
            <div class="card">
                <div class="card-label">Total Trades</div>
                <div class="card-value" id="total-trades">0</div>
            </div>
        </div>

        <div class="chart-container">
            <div class="section-title">Equity Curve</div>
            <canvas id="equity-chart" class="chart-canvas"></canvas>
        </div>

        <div class="section">
            <div class="section-title">Active Positions</div>
            <div class="card">
                <table>
                    <thead><tr>
                        <th>Symbol</th><th>Side</th><th>Entry Price</th>
                        <th>Quantity</th><th>P&L</th><th>P&L %</th><th>SL</th><th>TP</th>
                    </tr></thead>
                    <tbody id="positions-table"></tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Recent Trades</div>
            <div class="card">
                <table>
                    <thead><tr>
                        <th>Time</th><th>Symbol</th><th>Side</th>
                        <th>Entry</th><th>Exit</th><th>P&L</th><th>P&L %</th>
                    </tr></thead>
                    <tbody id="trades-table"></tbody>
                </table>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Active Strategies</div>
            <div class="strategies" id="strategies-list"></div>
        </div>
    </div>
    <div class="footer">CryptoBot v1.0 | Auto-refreshes every 5s</div>

    <script>
        function formatMoney(val) {
            return '$' + parseFloat(val).toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
        }
        function pnlClass(val) { return val >= 0 ? 'positive' : 'negative'; }

        function drawEquityChart(canvas, data) {
            if (!data || data.length < 2) return;
            const ctx = canvas.getContext('2d');
            const w = canvas.width = canvas.offsetWidth;
            const h = canvas.height = canvas.offsetHeight;
            ctx.clearRect(0, 0, w, h);

            const min = Math.min(...data);
            const max = Math.max(...data);
            const range = max - min || 1;
            const padding = 40;

            // Grid
            ctx.strokeStyle = '#21262d';
            ctx.lineWidth = 1;
            for (let i = 0; i < 5; i++) {
                const y = padding + (h - 2 * padding) * i / 4;
                ctx.beginPath(); ctx.moveTo(padding, y); ctx.lineTo(w - padding, y); ctx.stroke();
                ctx.fillStyle = '#484f58';
                ctx.font = '11px monospace';
                ctx.fillText(formatMoney(max - range * i / 4), 0, y + 4);
            }

            // Line
            const gradient = ctx.createLinearGradient(0, 0, 0, h);
            gradient.addColorStop(0, 'rgba(56,189,248,0.3)');
            gradient.addColorStop(1, 'rgba(56,189,248,0)');

            ctx.beginPath();
            data.forEach((val, i) => {
                const x = padding + (w - 2 * padding) * i / (data.length - 1);
                const y = padding + (h - 2 * padding) * (1 - (val - min) / range);
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            });
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 2;
            ctx.stroke();

            // Fill area
            const lastX = padding + (w - 2 * padding);
            ctx.lineTo(lastX, h - padding);
            ctx.lineTo(padding, h - padding);
            ctx.closePath();
            ctx.fillStyle = gradient;
            ctx.fill();
        }

        let equityData = [10000];

        async function refresh() {
            try {
                const resp = await fetch('/api/status');
                const data = await resp.json();

                // Status badge
                const badge = document.getElementById('status-badge');
                badge.textContent = data.running ? 'RUNNING' : 'STOPPED';
                badge.className = 'status-badge ' + (data.running ? 'status-running' : 'status-stopped');

                // Portfolio cards
                const p = data.portfolio;
                document.getElementById('total-balance').textContent = formatMoney(p.total_balance);
                const pnlEl = document.getElementById('total-pnl');
                pnlEl.textContent = formatMoney(p.total_pnl) + ' (' + p.total_pnl_percent.toFixed(2) + '%)';
                pnlEl.className = 'card-value ' + pnlClass(p.total_pnl);
                document.getElementById('win-rate').textContent = (p.win_rate * 100).toFixed(1) + '%';
                document.getElementById('total-trades').textContent = p.total_trades;

                // Equity chart
                if (p.total_balance !== equityData[equityData.length - 1]) {
                    equityData.push(p.total_balance);
                    if (equityData.length > 200) equityData.shift();
                }
                drawEquityChart(document.getElementById('equity-chart'), equityData);

                // Positions
                const posTable = document.getElementById('positions-table');
                posTable.innerHTML = '';
                for (const [sym, pos] of Object.entries(data.positions || {})) {
                    posTable.innerHTML += `<tr>
                        <td>${sym}</td>
                        <td class="side-${pos.side.toLowerCase()}">${pos.side}</td>
                        <td>${formatMoney(pos.entry_price)}</td>
                        <td>${pos.quantity.toFixed(6)}</td>
                        <td class="${pnlClass(pos.pnl)}">${formatMoney(pos.pnl)}</td>
                        <td class="${pnlClass(pos.pnl_percent)}">${pos.pnl_percent.toFixed(2)}%</td>
                        <td>${pos.stop_loss ? formatMoney(pos.stop_loss) : '-'}</td>
                        <td>${pos.take_profit ? formatMoney(pos.take_profit) : '-'}</td>
                    </tr>`;
                }
                if (!Object.keys(data.positions || {}).length) {
                    posTable.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#484f58">No active positions</td></tr>';
                }

                // Recent trades
                const tradesTable = document.getElementById('trades-table');
                tradesTable.innerHTML = '';
                for (const t of (data.recent_trades || []).reverse()) {
                    tradesTable.innerHTML += `<tr>
                        <td>${t.time}</td>
                        <td>${t.symbol}</td>
                        <td class="side-${t.side.toLowerCase()}">${t.side}</td>
                        <td>${formatMoney(t.entry)}</td>
                        <td>${formatMoney(t.exit)}</td>
                        <td class="${pnlClass(t.pnl)}">${formatMoney(t.pnl)}</td>
                        <td class="${pnlClass(t.pnl_percent)}">${t.pnl_percent.toFixed(2)}%</td>
                    </tr>`;
                }
                if (!(data.recent_trades || []).length) {
                    tradesTable.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#484f58">No trades yet</td></tr>';
                }

                // Strategies
                const stratList = document.getElementById('strategies-list');
                stratList.innerHTML = '';
                for (const s of (data.strategies || [])) {
                    stratList.innerHTML += `<span class="strategy-tag">${s}</span>`;
                }

            } catch (e) {
                console.error('Dashboard refresh error:', e);
            }
        }

        refresh();
        setInterval(refresh, 5000);
    </script>
</body>
</html>"""


class Dashboard:
    """Flask 기반 웹 대시보드"""

    def __init__(self, engine, config: dict = None):
        self.engine = engine
        self.config = config or {}
        self.host = self.config.get("dashboard_host", "0.0.0.0")
        self.port = self.config.get("dashboard_port", 8080)
        self._thread = None

    def start(self):
        """백그라운드 스레드에서 대시보드 서버 시작"""
        try:
            from flask import Flask, jsonify
        except ImportError:
            logger.warning("Flask가 설치되지 않아 대시보드를 시작할 수 없습니다. pip install flask")
            return

        app = Flask(__name__)

        @app.route("/")
        def index():
            return DASHBOARD_HTML

        @app.route("/api/status")
        def status():
            return jsonify(self.engine.get_status())

        self._thread = threading.Thread(
            target=lambda: app.run(host=self.host, port=self.port, debug=False, use_reloader=False),
            daemon=True,
        )
        self._thread.start()
        logger.info(f"대시보드 시작: http://{self.host}:{self.port}")

    def stop(self):
        logger.info("대시보드 중지")
