DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KountN Developer Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-color: #0b0f19;
            --card-bg: rgba(17, 24, 39, 0.7);
            --card-border: rgba(255, 255, 255, 0.07);
            --text-main: #f3f4f6;
            --text-muted: #9ca3af;
            --accent-primary: #6366f1;
            --accent-primary-hover: #4f46e5;
            --accent-secondary: #10b981;
            --accent-tertiary: #f59e0b;
            --danger: #ef4444;
            --font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
        }

        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
            font-family: var(--font-family);
            background-color: var(--bg-color);
            background-image: 
                radial-gradient(at 0% 0%, rgba(99, 102, 241, 0.15) 0px, transparent 50%),
                radial-gradient(at 100% 100%, rgba(16, 185, 129, 0.1) 0px, transparent 50%);
            color: var(--text-main);
            min-height: 100vh;
            line-height: 1.5;
            padding: 24px;
            overflow-x: hidden;
        }

        /* Loading Overlay */
        #loading-overlay {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: var(--bg-color);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 9999;
            font-size: 1.5rem;
            font-weight: 500;
        }

        .spinner {
            border: 4px solid rgba(255, 255, 255, 0.1);
            width: 50px;
            height: 50px;
            border-radius: 50%;
            border-left-color: var(--accent-primary);
            animation: spin 1s linear infinite;
            margin-bottom: 16px;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .flex-col-center {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
        }

        /* Lock Screen */
        #lock-screen {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            background: var(--bg-color);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 9000;
            padding: 20px;
        }

        .lock-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            backdrop-filter: blur(16px);
            padding: 40px;
            border-radius: 20px;
            width: 100%;
            max-width: 420px;
            box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3);
            text-align: center;
        }

        .lock-logo {
            font-size: 2.2rem;
            font-weight: 700;
            background: linear-gradient(135deg, #818cf8, #34d399);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 8px;
        }

        .lock-card p {
            color: var(--text-muted);
            margin-bottom: 24px;
            font-size: 0.95rem;
        }

        .lock-input {
            width: 100%;
            padding: 14px;
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid var(--card-border);
            border-radius: 10px;
            color: var(--text-main);
            font-size: 1rem;
            text-align: center;
            margin-bottom: 16px;
            transition: all 0.2s;
        }

        .lock-input:focus {
            outline: none;
            border-color: var(--accent-primary);
            box-shadow: 0 0 0 2px rgba(99, 102, 241, 0.3);
        }

        .lock-btn {
            width: 100%;
            padding: 14px;
            background: var(--accent-primary);
            border: none;
            border-radius: 10px;
            color: white;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }

        .lock-btn:hover {
            background: var(--accent-primary-hover);
        }

        .error-msg {
            color: var(--danger);
            margin-top: 12px;
            font-size: 0.85rem;
            font-weight: 500;
            display: none;
        }

        /* Dashboard Main Layout */
        .dashboard-container {
            max-width: 1400px;
            margin: 0 auto;
            opacity: 0;
            transition: opacity 0.5s ease;
        }

        .dashboard-container.loaded {
            opacity: 1;
        }

        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 32px;
            flex-wrap: wrap;
            gap: 16px;
        }

        .brand {
            display: flex;
            flex-direction: column;
        }

        .brand h1 {
            font-size: 2.2rem;
            font-weight: 700;
            background: linear-gradient(135deg, #a5b4fc, #818cf8, #34d399);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }

        .brand span {
            font-size: 0.9rem;
            color: var(--text-muted);
            font-weight: 400;
        }

        .header-actions {
            display: flex;
            gap: 12px;
        }

        .btn-secondary {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--card-border);
            padding: 10px 18px;
            border-radius: 8px;
            color: var(--text-main);
            font-weight: 500;
            cursor: pointer;
            transition: background 0.2s;
            font-size: 0.9rem;
        }

        .btn-secondary:hover {
            background: rgba(255, 255, 255, 0.1);
        }

        /* Overview Cards */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 20px;
            margin-bottom: 32px;
        }

        .stat-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            backdrop-filter: blur(12px);
            border-radius: 16px;
            padding: 24px;
            display: flex;
            flex-direction: column;
            position: relative;
            overflow: hidden;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            transition: transform 0.2s, box-shadow 0.2s;
        }

        .stat-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.2);
        }

        .stat-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 4px;
            height: 100%;
        }

        .stat-card.indigo::before { background: var(--accent-primary); }
        .stat-card.emerald::before { background: var(--accent-secondary); }
        .stat-card.amber::before { background: var(--accent-tertiary); }
        .stat-card.rose::before { background: var(--danger); }

        .stat-label {
            font-size: 0.85rem;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 8px;
            font-weight: 500;
        }

        .stat-value {
            font-size: 2rem;
            font-weight: 700;
            margin-bottom: 4px;
        }

        .stat-desc {
            font-size: 0.8rem;
            color: var(--text-muted);
        }

        /* Visuals Grid (Charts) */
        .charts-grid {
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            gap: 20px;
            margin-bottom: 32px;
        }

        @media (max-width: 900px) {
            .charts-grid {
                grid-template-columns: 1fr;
            }
        }

        .chart-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            backdrop-filter: blur(12px);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            height: 320px;
            display: flex;
            flex-direction: column;
        }

        .chart-card h3 {
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 16px;
            color: var(--text-main);
        }

        .chart-container {
            position: relative;
            flex-grow: 1;
            height: calc(100% - 40px);
            width: 100%;
        }

        /* Users Table section */
        .table-card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            backdrop-filter: blur(12px);
            border-radius: 16px;
            padding: 24px;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
            margin-bottom: 32px;
        }

        .table-header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            flex-wrap: wrap;
            gap: 12px;
        }

        .table-header-row h3 {
            font-size: 1.25rem;
            font-weight: 600;
        }

        .search-input {
            padding: 10px 16px;
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid var(--card-border);
            border-radius: 8px;
            color: var(--text-main);
            font-size: 0.9rem;
            width: 280px;
        }

        .search-input:focus {
            outline: none;
            border-color: var(--accent-primary);
        }

        .table-wrapper {
            overflow-x: auto;
            width: 100%;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            text-align: left;
            font-size: 0.95rem;
        }

        th {
            padding: 12px 16px;
            color: var(--text-muted);
            font-weight: 500;
            border-bottom: 1px solid var(--card-border);
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        td {
            padding: 16px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            vertical-align: middle;
        }

        tr:hover td {
            background: rgba(255, 255, 255, 0.02);
        }

        /* Badges */
        .badge {
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 9999px;
            font-size: 0.75rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.02em;
        }

        .badge-free {
            background: rgba(156, 163, 175, 0.15);
            color: #d1d5db;
        }

        .badge-pro {
            background: rgba(99, 102, 241, 0.15);
            color: #a5b4fc;
        }

        .badge-premium {
            background: rgba(245, 158, 11, 0.15);
            color: #fde68a;
        }

        .badge-paid {
            background: rgba(16, 185, 129, 0.15);
            color: #34d399;
            margin-left: 6px;
            font-size: 0.65rem;
            padding: 2px 6px;
        }

        /* Progress bars */
        .progress-container {
            display: flex;
            align-items: center;
            gap: 8px;
            width: 140px;
        }

        .progress-bar {
            height: 6px;
            background: rgba(255, 255, 255, 0.08);
            border-radius: 9999px;
            flex-grow: 1;
            overflow: hidden;
        }

        .progress-fill {
            height: 100%;
            background: var(--accent-primary);
            border-radius: 9999px;
        }

        .progress-fill.warning {
            background: var(--accent-tertiary);
        }

        .progress-fill.danger {
            background: var(--danger);
        }

        .progress-text {
            font-size: 0.8rem;
            color: var(--text-muted);
            min-width: 45px;
            text-align: right;
        }

        .btn-view-logs {
            background: transparent;
            border: 1px solid rgba(255, 255, 255, 0.15);
            color: var(--text-main);
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
            font-size: 0.85rem;
            transition: all 0.2s;
        }

        .btn-view-logs:hover {
            border-color: var(--accent-primary);
            color: var(--accent-primary);
        }

        /* Collapsible log table block */
        .logs-row {
            display: none;
        }

        .logs-row.open {
            display: table-row;
        }

        .logs-card {
            background: rgba(0, 0, 0, 0.2);
            border-radius: 12px;
            padding: 20px;
            margin: 10px 0;
            border: 1px dashed rgba(255, 255, 255, 0.08);
        }

        .logs-title {
            font-size: 1rem;
            font-weight: 600;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            color: var(--accent-primary);
        }

        .tx-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 0.85rem;
        }

        .tx-table th {
            padding: 8px 12px;
            background: rgba(255, 255, 255, 0.02);
        }

        .tx-table td {
            padding: 10px 12px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.02);
        }

        .tx-type-badge {
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.7rem;
            font-weight: 600;
        }

        .tx-type-income {
            background: rgba(16, 185, 129, 0.1);
            color: #34d399;
        }

        .tx-type-expense {
            background: rgba(239, 68, 68, 0.1);
            color: #f87171;
        }

        .tx-class-badge {
            background: rgba(255, 255, 255, 0.06);
            color: var(--text-muted);
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 0.7rem;
        }
    </style>
</head>
<body>

    <!-- Loading screen -->
    <div id="loading-overlay" class="flex-col-center">
        <div class="spinner"></div>
        <p>Loading analytics...</p>
    </div>

    <!-- Passkey Form Screen -->
    <div id="lock-screen" class="flex-col-center" style="display: none;">
        <div class="lock-card">
            <div class="lock-logo">KountN</div>
            <p>Enter the CRON_SECRET passkey to unlock the developer dashboard.</p>
            <input type="password" id="passkey-input" class="lock-input" placeholder="••••••••••••••••" onkeydown="if(event.key==='Enter') verifyPasskey()">
            <button class="lock-btn" onclick="verifyPasskey()">Unlock Dashboard</button>
            <div id="error-message" class="error-msg">Invalid passkey. Please try again.</div>
        </div>
    </div>

    <!-- Main Dashboard Container -->
    <div class="dashboard-container" id="main-content">
        <header>
            <div class="brand">
                <h1>KountN Developer Dashboard</h1>
                <span>Cohort testing tracker • Phase 2 validation</span>
            </div>
            <div class="header-actions">
                <button class="btn-secondary" onclick="fetchStats(true)">🔄 Refresh</button>
                <button class="btn-secondary" onclick="logout()">🔒 Lock</button>
            </div>
        </header>

        <!-- Stats row -->
        <div class="stats-grid">
            <div class="stat-card indigo">
                <div class="stat-label">Total Users</div>
                <div class="stat-value" id="stat-total-users">0</div>
                <div class="stat-desc">Registered via WhatsApp</div>
            </div>
            <div class="stat-card emerald">
                <div class="stat-label">Active Users</div>
                <div class="stat-value" id="stat-active-users">0</div>
                <div class="stat-desc">Logged at least 1 expense</div>
            </div>
            <div class="stat-card amber">
                <div class="stat-label">Total Transactions</div>
                <div class="stat-value" id="stat-total-tx">0</div>
                <div class="stat-desc">AI-parsed statements</div>
            </div>
            <div class="stat-card rose">
                <div class="stat-label">Volume (GHS)</div>
                <div class="stat-value" id="stat-volume">0</div>
                <div class="stat-desc" id="stat-volume-desc">Expense: 0 | Income: 0</div>
            </div>
        </div>

        <!-- Charts Row -->
        <div class="charts-grid">
            <div class="chart-card">
                <h3>Daily Transaction Volume</h3>
                <div class="chart-container">
                    <canvas id="chart-daily"></canvas>
                </div>
            </div>
            <div class="chart-card">
                <h3>Category Distribution</h3>
                <div class="chart-container">
                    <canvas id="chart-categories"></canvas>
                </div>
            </div>
            <div class="chart-card" style="display: none;">
                <h3>Subscription Tiers</h3>
                <div class="chart-container">
                    <canvas id="chart-tiers"></canvas>
                </div>
            </div>
        </div>

        <!-- Users Table -->
        <div class="table-card">
            <div class="table-header-row">
                <h3>Cohort Performance (10-User Test Group)</h3>
                <input type="text" id="user-search" class="search-input" placeholder="Search phone number or tier..." oninput="filterUsers()">
            </div>
            <div class="table-wrapper">
                <table>
                    <thead>
                        <tr>
                            <th>Phone Number</th>
                            <th>Tier</th>
                            <th>Monthly Usage</th>
                            <th>Total Logged</th>
                            <th>Registered</th>
                            <th>Last Active</th>
                            <th>Logs</th>
                        </tr>
                    </thead>
                    <tbody id="users-table-body">
                        <!-- Filled by JS -->
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let cachedData = null;
        let chartDailyObj = null;
        let chartCategoriesObj = null;

        document.addEventListener('DOMContentLoaded', () => {
            const savedSecret = sessionStorage.getItem('kountn_dashboard_secret');
            if (savedSecret) {
                fetchStats();
            } else {
                document.getElementById('loading-overlay').style.display = 'none';
                document.getElementById('lock-screen').style.display = 'flex';
            }
        });

        function logout() {
            sessionStorage.removeItem('kountn_dashboard_secret');
            window.location.reload();
        }

        async function verifyPasskey() {
            const input = document.getElementById('passkey-input').value.trim();
            if (!input) return;

            document.getElementById('error-message').style.display = 'none';
            
            try {
                const res = await fetch(`/api/dashboard/stats?secret=${encodeURIComponent(input)}`);
                if (res.ok) {
                    sessionStorage.setItem('kountn_dashboard_secret', input);
                    document.getElementById('lock-screen').style.display = 'none';
                    document.getElementById('loading-overlay').style.display = 'flex';
                    fetchStats();
                } else {
                    document.getElementById('error-message').style.display = 'block';
                }
            } catch (err) {
                document.getElementById('error-message').style.display = 'block';
            }
        }

        async function fetchStats(isManualRefresh = false) {
            const secret = sessionStorage.getItem('kountn_dashboard_secret');
            if (!secret) return;

            if (isManualRefresh) {
                document.getElementById('loading-overlay').style.display = 'flex';
            }

            try {
                const res = await fetch(`/api/dashboard/stats?secret=${encodeURIComponent(secret)}`);
                if (res.status === 401) {
                    logout();
                    return;
                }
                const data = await res.json();
                cachedData = data;
                
                renderDashboard(data);
            } catch (err) {
                console.error("Fetch failed", err);
                alert("Failed to refresh statistics. Please check your network or server status.");
            } finally {
                document.getElementById('loading-overlay').style.display = 'none';
            }
        }

        function getTierLimit(tier) {
            const limits = {
                "free": 15,
                "pro": 75,
                "premium": 300
            };
            return limits[tier.toLowerCase()] || 15;
        }

        function formatDate(isoStr) {
            if (!isoStr || isoStr === "Never") return "Never";
            try {
                const d = new Date(isoStr);
                return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
            } catch (e) {
                return isoStr;
            }
        }

        function renderDashboard(data) {
            // Stats counts
            document.getElementById('stat-total-users').innerText = data.total_users;
            document.getElementById('stat-active-users').innerText = data.active_users_count;
            document.getElementById('stat-total-tx').innerText = data.total_transactions;
            
            const totalVolume = data.income_total + data.expense_total;
            document.getElementById('stat-volume').innerText = `GHS ${totalVolume.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`;
            document.getElementById('stat-volume-desc').innerText = `Exp: GHS ${data.expense_total.toLocaleString()} | Inc: GHS ${data.income_total.toLocaleString()}`;

            // Users Table
            renderUsersTable(data.users);

            // Daily chart
            const dailyCtx = document.getElementById('chart-daily').getContext('2d');
            const dailyLabels = data.daily_trends.map(t => t.date);
            const dailyCounts = data.daily_trends.map(t => t.count);

            if (chartDailyObj) chartDailyObj.destroy();
            chartDailyObj = new Chart(dailyCtx, {
                type: 'line',
                data: {
                    labels: dailyLabels,
                    datasets: [{
                        label: 'Transactions Logged',
                        data: dailyCounts,
                        borderColor: '#6366f1',
                        backgroundColor: 'rgba(99, 102, 241, 0.1)',
                        borderWidth: 2,
                        tension: 0.3,
                        fill: true
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            beginAtZero: true,
                            ticks: { precision: 0, color: '#9ca3af' },
                            grid: { color: 'rgba(255,255,255,0.05)' }
                        },
                        x: {
                            ticks: { color: '#9ca3af' },
                            grid: { display: false }
                        }
                    },
                    plugins: {
                        legend: { display: false }
                    }
                }
            });

            // Category chart
            const categoryCtx = document.getElementById('chart-categories').getContext('2d');
            const categoryLabels = Object.keys(data.categories);
            const categoryCounts = Object.values(data.categories);

            if (chartCategoriesObj) chartCategoriesObj.destroy();
            chartCategoriesObj = new Chart(categoryCtx, {
                type: 'bar',
                data: {
                    labels: categoryLabels,
                    datasets: [{
                        label: 'Transactions',
                        data: categoryCounts,
                        backgroundColor: '#10b981',
                        borderRadius: 6
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    indexAxis: 'y',
                    scales: {
                        x: {
                            beginAtZero: true,
                            ticks: { precision: 0, color: '#9ca3af' },
                            grid: { color: 'rgba(255,255,255,0.05)' }
                        },
                        y: {
                            ticks: { color: '#9ca3af' },
                            grid: { display: false }
                        }
                    },
                    plugins: {
                        legend: { display: false }
                    }
                }
            });

            // Show main layout
            document.getElementById('main-content').classList.add('loaded');
        }

        function renderUsersTable(users) {
            const tbody = document.getElementById('users-table-body');
            tbody.innerHTML = '';

            if (users.length === 0) {
                tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No users found.</td></tr>`;
                return;
            }

            users.forEach(user => {
                const limit = getTierLimit(user.tier);
                const percent = Math.min(Math.round((user.entry_count / limit) * 100), 100);
                
                let progressColorClass = '';
                if (percent >= 90) progressColorClass = 'danger';
                else if (percent >= 75) progressColorClass = 'warning';

                const tierClass = `badge-${user.tier.toLowerCase()}`;
                
                const tr = document.createElement('tr');
                tr.id = `user-row-${user.id}`;
                tr.innerHTML = `
                    <td style="font-weight: 500;">
                        ${user.phone}
                    </td>
                    <td>
                        <span class="badge ${tierClass}">${user.tier}</span>
                        ${user.is_paid ? '<span class="badge badge-paid">PAID</span>' : ''}
                    </td>
                    <td>
                        <div class="progress-container">
                            <div class="progress-bar">
                                <div class="progress-fill ${progressColorClass}" style="width: ${percent}%;"></div>
                            </div>
                            <span class="progress-text">${user.entry_count}/${limit}</span>
                        </div>
                    </td>
                    <td>${user.transactions_count}</td>
                    <td style="color: var(--text-muted); font-size: 0.85rem;">${formatDate(user.created_at)}</td>
                    <td style="color: var(--text-muted); font-size: 0.85rem;">${formatDate(user.last_active)}</td>
                    <td>
                        <button class="btn-view-logs" onclick="toggleLogs('${user.id}')">View Logs (${user.transactions_count})</button>
                    </td>
                `;

                const logsTr = document.createElement('tr');
                logsTr.className = 'logs-row';
                logsTr.id = `user-logs-${user.id}`;
                
                // Render sub-table of recent transaction logs
                let txRowsHtml = '';
                if (user.transactions.length === 0) {
                    txRowsHtml = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 12px;">No transactions logged yet.</td></tr>`;
                } else {
                    user.transactions.forEach(tx => {
                        const typeClass = tx.entry_type === 'Income' ? 'tx-type-income' : 'tx-type-expense';
                        txRowsHtml += `
                            <tr>
                                <td><span class="tx-type-badge ${typeClass}">${tx.entry_type}</span></td>
                                <td style="font-weight: 500;">GHS ${tx.amount.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                                <td><span class="badge badge-free" style="padding: 2px 6px;">${tx.category}</span></td>
                                <td>${tx.merchant || '<span style="color:var(--text-muted)">-</span>'}</td>
                                <td>${tx.description || ''}</td>
                                <td><span class="tx-class-badge">${tx.classification || 'personal'}</span> ${tx.client_tag ? '· ' + tx.client_tag : ''}</td>
                                <td style="color: var(--text-muted); font-size: 0.8rem;">${formatDate(tx.logged_at)}</td>
                            </tr>
                        `;
                    });
                }

                logsTr.innerHTML = `
                    <td colspan="7" style="padding: 0 16px;">
                        <div class="logs-card">
                            <div class="logs-row-container">
                                <div class="logs-title">
                                    <span>Transaction Logs for ${user.phone}</span>
                                    <span style="font-size: 0.8rem; font-weight: 400; color: var(--text-muted);">Decrypted on-the-fly in-memory</span>
                                </div>
                                <table class="tx-table">
                                    <thead>
                                        <tr>
                                            <th style="padding: 8px 12px;">Type</th>
                                            <th style="padding: 8px 12px;">Amount</th>
                                            <th style="padding: 8px 12px;">Category</th>
                                            <th style="padding: 8px 12px;">Merchant</th>
                                            <th style="padding: 8px 12px;">Description</th>
                                            <th style="padding: 8px 12px;">Tag/Class</th>
                                            <th style="padding: 8px 12px;">Logged At</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        ${txRowsHtml}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </td>
                `;

                tbody.appendChild(tr);
                tbody.appendChild(logsTr);
            });
        }

        function toggleLogs(userId) {
            const logsRow = document.getElementById(`user-logs-${userId}`);
            if (logsRow.classList.contains('open')) {
                logsRow.classList.remove('open');
            } else {
                logsRow.classList.add('open');
            }
        }

        function filterUsers() {
            const query = document.getElementById('user-search').value.toLowerCase().trim();
            if (!cachedData) return;

            const filteredUsers = cachedData.users.filter(u => {
                return u.phone.toLowerCase().includes(query) || u.tier.toLowerCase().includes(query);
            });

            renderUsersTable(filteredUsers);
        }
    </script>
</body>
</html>
"""
