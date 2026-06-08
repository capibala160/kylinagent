// Kylin Safe Ops Agent - 前端逻辑

let currentSessionId = StorageManager.getSessionId() || generateSessionId();
let isProcessing = false;
let pendingConfirm = null;
let pendingPrivilege = null;
let currentUserRole = 'user';
let currentUsername = '';

function generateSessionId() {
    const id = 'sess_' + Math.random().toString(36).substr(2, 9);
    StorageManager.setSessionId(id);
    return id;
}

// ===== DOM 元素 =====
const chatMessages = document.getElementById('chatMessages');
const chatInput = document.getElementById('chatInput');
const sendBtn = document.getElementById('sendBtn');
const sessionIdEl = document.getElementById('sessionId');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const confirmModal = document.getElementById('confirmModal');
const chainModal = document.getElementById('chainModal');

// ===== 初始化 =====
async function init() {
    // 先检查登录状态
    try {
        const res = await fetch(API_BASE + '/auth/me', { credentials: 'include' });
        if (!res.ok) {
            window.location.href = '/static/login.html';
            return;
        }
        const data = await res.json();
        currentUsername = data.username || '';
        currentUserRole = data.role || 'user';
        // 管理员显示审批管理入口
        if (currentUserRole === 'admin') {
            const approvalsNav = document.getElementById('tab-approvals');
            if (approvalsNav) approvalsNav.style.display = '';
        }
    } catch (e) {
        window.location.href = '/static/login.html';
        return;
    }

    sessionIdEl.textContent = currentSessionId;
    checkHealth();
    setupEventListeners();
    setupNavigation();
    initDashboardCharts();
    initAuditStatsCharts();
    startDashboardAutoRefresh();
    loadSessions();
    
    // 每 30 秒检查一次健康状态
    setInterval(checkHealth, 30000);
}

function setupEventListeners() {
    // 发送消息
    sendBtn.addEventListener('click', sendMessage);
    chatInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });
    
    // 自动调整输入框高度
    chatInput.addEventListener('input', () => {
        chatInput.style.height = 'auto';
        chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + 'px';
    });
    
    // 快捷提示按钮
    document.querySelectorAll('.hint-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            chatInput.value = btn.dataset.hint;
            sendMessage();
        });
    });
    
    // 登出
    const logoutBtn = document.getElementById('logoutBtn');
    if (logoutBtn) {
        logoutBtn.addEventListener('click', async () => {
            try {
                await fetch(API_BASE + '/auth/logout', {
                    method: 'POST',
                    credentials: 'include'
                });
            } catch (e) {
                // 忽略网络错误
            }
            StorageManager.removeSessionId();
            window.location.href = '/static/login.html';
        });
    }
    
    // 新会话
    document.getElementById('newChatBtn').addEventListener('click', () => {
        currentSessionId = generateSessionId();
        sessionIdEl.textContent = currentSessionId;
        chatMessages.innerHTML = `
            <div class="welcome-message">
                <h3><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14zM7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/></svg> 已开启新会话</h3>
                <p>请输入您的运维指令...</p>
            </div>
        `;
        loadSessions();
    });
    
    // 刷新历史会话
    const refreshSessionsBtn = document.getElementById('refreshSessions');
    if (refreshSessionsBtn) {
        refreshSessionsBtn.addEventListener('click', loadSessions);
    }
    
    // 确认弹窗
    document.getElementById('confirmBtn').addEventListener('click', async () => {
        confirmModal.classList.remove('active');
        if (pendingConfirm) {
            // 显示"正在执行确认操作"的提示，不重复添加用户消息
            addMessage('assistant', '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="4"/><polyline points="12 7 12 12 15 15"/></svg> 正在执行确认的操作...', {loading: true});
            try {
                await sendMessage(pendingConfirm.message, true);
            } catch (e) {
                addMessage('assistant', `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> 执行失败: ${e.message}`);
            }
            pendingConfirm = null;
        }
    });
    
    document.getElementById('cancelBtn').addEventListener('click', () => {
        confirmModal.classList.remove('active');
        if (pendingConfirm) {
            addMessage('assistant', '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> 用户已取消执行该操作', {cancelled: true});
            pendingConfirm = null;
        }
    });
    
    // 关闭链路弹窗
    document.getElementById('closeChainModal').addEventListener('click', () => {
        chainModal.classList.remove('active');
    });
    
    // 确认弹窗关闭按钮
    document.getElementById('closeConfirmModal').addEventListener('click', () => {
        document.getElementById('cancelBtn').click();
    });
    
    // 权限申请弹窗
    document.getElementById('closePrivilegeModal').addEventListener('click', () => {
        document.getElementById('cancelPrivilegeBtn').click();
    });
    document.getElementById('cancelPrivilegeBtn').addEventListener('click', () => {
        privilegeModal.classList.remove('active');
        pendingPrivilege = null;
    });
    document.getElementById('submitPrivilegeBtn').addEventListener('click', submitPrivilegeRequest);
    
    // 点击遮罩层关闭弹窗
    confirmModal.addEventListener('click', (e) => {
        if (e.target === confirmModal) document.getElementById('cancelBtn').click();
    });
    chainModal.addEventListener('click', (e) => {
        if (e.target === chainModal) chainModal.classList.remove('active');
    });
    privilegeModal.addEventListener('click', (e) => {
        if (e.target === privilegeModal) document.getElementById('cancelPrivilegeBtn').click();
    });
    
    // ESC 键关闭弹窗
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            if (confirmModal.classList.contains('active')) {
                document.getElementById('cancelBtn').click();
            }
            if (privilegeModal.classList.contains('active')) {
                document.getElementById('cancelPrivilegeBtn').click();
            }
            if (chainModal.classList.contains('active')) {
                chainModal.classList.remove('active');
            }
        }
    });
    
    // 审计刷新
    document.getElementById('refreshAudit').addEventListener('click', () => {
        loadAuditLogs();
        loadAuditStats();
    });
    document.getElementById('auditFilter').addEventListener('change', loadAuditLogs);
    const auditStatsDays = document.getElementById('auditStatsDays');
    if (auditStatsDays) {
        auditStatsDays.addEventListener('change', loadAuditStats);
    }
    
    // 仪表盘刷新
    const refreshDashboardBtn = document.getElementById('refreshDashboard');
    if (refreshDashboardBtn) {
        refreshDashboardBtn.addEventListener('click', loadDashboard);
    }
    const toggleAutoRefreshBtn = document.getElementById('toggleAutoRefresh');
    if (toggleAutoRefreshBtn) {
        toggleAutoRefreshBtn.addEventListener('click', () => {
            dashboardAutoRefresh = !dashboardAutoRefresh;
            toggleAutoRefreshBtn.classList.toggle('active', dashboardAutoRefresh);
            toggleAutoRefreshBtn.textContent = dashboardAutoRefresh ? '自动刷新' : '已暂停';
            if (dashboardAutoRefresh) startDashboardAutoRefresh();
            else stopDashboardAutoRefresh();
        });
    }
}

function setupNavigation() {
    const navBtns = document.querySelectorAll('.nav-btn');
    const views = document.querySelectorAll('.view');
    
    navBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            const viewName = btn.dataset.view;
            
            // 更新按钮状态
            navBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            
            // 切换视图
            views.forEach(v => v.classList.remove('active'));
            document.getElementById(viewName + 'View').classList.add('active');
            
            // 加载对应数据
            if (viewName === 'audit') {
                loadAuditLogs();
                loadAuditStats();
            }
            if (viewName === 'tools') loadTools();
            if (viewName === 'config') loadConfig();
            if (viewName === 'dashboard') loadDashboard();
            if (viewName === 'approvals') loadApprovals();
        });
    });
}

// ===== API 调用 =====


// ===== 健康检查 =====

// ===== 发送消息（SSE 流式） =====
// ===== 消息渲染 =====
// ===== 审计日志 =====
async function loadAuditLogs() {
    const list = document.getElementById('auditList');
    const status = document.getElementById('auditFilter').value;
    
    try {
        const url = '/audit/chains' + (status ? `?status=${status}&limit=100` : '?limit=100');
        const data = await apiGet(url);
        
        // 兼容后端返回格式：直接数组或 {chains: [...]}
        const chains = Array.isArray(data) ? data : (data.chains || []);
        
        if (!chains || chains.length === 0) {
            list.innerHTML = '<p class="empty-state">暂无记录</p>';
            return;
        }
        
        list.innerHTML = chains.map(chain => `
            <div class="audit-item status-${chain.final_status}" onclick="showChainDetails('${chain.chain_id}')">
                <div class="audit-item-header">
                    <span class="audit-item-title">${escapeHtml(chain.user_input.substr(0, 50))}${chain.user_input.length > 50 ? '...' : ''}</span>
                    <span class="audit-item-status">${chain.final_status}</span>
                </div>
                <div class="audit-item-meta">
                    <span><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="4"/><polyline points="12 7 12 12 15 15"/></svg> ${chain.duration_sec ? chain.duration_sec + 's' : 'N/A'}</span>
                    <span><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg> ${chain.nodes ? chain.nodes.length : 0} 节点</span>
                    <span><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="4"/><polyline points="12 7 12 12 15 15"/></svg> ${new Date(chain.start_time * 1000).toLocaleString()}</span>
                </div>
            </div>
        `).join('');
    } catch (e) {
        list.innerHTML = `<p class="empty-state">加载失败: ${e.message}</p>`;
    }
}

// ===== 工具列表 =====
async function loadTools() {
    const grid = document.getElementById('toolsGrid');
    
    try {
        const data = await apiGet('/tools');
        
        if (!data.tools || data.tools.length === 0) {
            grid.innerHTML = '<p class="empty-state">暂无工具</p>';
            return;
        }
        grid.innerHTML = data.tools.map(tool => `
            <div class="tool-card">
                <h4>${escapeHtml(tool.name)}</h4>
                <p>${escapeHtml(tool.description)}</p>
                <div class="tool-card-params">
                    参数: ${Object.keys(tool.parameters.properties || {}).map(p => 
                        `<code>${escapeHtml(p)}</code>`
                    ).join(', ') || '无'}
                </div>
            </div>
        `).join('');
    } catch (e) {
        grid.innerHTML = `<p class="empty-state">加载失败: ${e.message}</p>`;
    }
}

// ===== 配置信息 =====
async function loadConfig() {
    const content = document.getElementById('configContent');
    
    try {
        const data = await apiGet('/config');
        
        content.innerHTML = `
            <div class="config-section">
                <h3>Agent 信息</h3>
                <div class="config-row">
                    <span class="config-label">名称</span>
                    <span class="config-value">${escapeHtml(data.agent.name || '')}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">版本</span>
                    <span class="config-value">${escapeHtml(data.agent.version || '')}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">描述</span>
                    <span class="config-value">${escapeHtml(data.agent.description || '')}</span>
                </div>
            </div>
            
            <div class="config-section">
                <h3>LLM 配置</h3>
                <div class="config-row">
                    <span class="config-label">提供商</span>
                    <span class="config-value">${escapeHtml(data.llm.provider || '')}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">模型</span>
                    <span class="config-value">${escapeHtml(data.llm.model || '')}</span>
                </div>
            </div>
            
            <div class="config-section">
                <h3>安全配置</h3>
                <div class="config-row">
                    <span class="config-label">受限用户</span>
                    <span class="config-value">${escapeHtml(data.security.restricted_user || '')}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">规则数量</span>
                    <span class="config-value">${data.security.rule_count !== undefined ? data.security.rule_count : 'N/A'}</span>
                </div>
            </div>
            
            <div class="config-section">
                <h3>审计配置</h3>
                <div class="config-row">
                    <span class="config-label">日志目录</span>
                    <span class="config-value">${escapeHtml(data.audit.log_dir || '')}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">保留天数</span>
                    <span class="config-value">${data.audit.retention_days !== undefined ? data.audit.retention_days : 'N/A'}</span>
                </div>
            </div>
        `;
    } catch (e) {
        content.innerHTML = `<p class="empty-state">加载失败: ${e.message}</p>`;
    }
}

// ===== 链路详情 =====
async function showChainDetails(chainId) {
    const details = document.getElementById('chainDetails');
    
    try {
        const chain = await apiGet('/audit/chain/' + chainId);
        
        const nodesHtml = chain.nodes.map((node, i) => `
            <div style="margin-bottom: 16px; padding: 12px; background: var(--bg-dark); border-radius: var(--radius);">
                <div style="display: flex; justify-content: space-between; margin-bottom: 8px;">
                    <strong>${i + 1}. ${node.node_type}</strong>
                    <span style="font-size: 12px; color: var(--text-secondary);">${new Date(node.timestamp * 1000).toLocaleTimeString()}</span>
                </div>
                <p style="margin-bottom: 8px;">${node.description}</p>
                <div style="font-size: 12px; color: var(--text-secondary);">
                    状态: <span style="color: ${node.status === 'success' ? 'var(--success)' : node.status === 'blocked' ? 'var(--danger)' : 'var(--warning)'};">${node.status}</span>
                    ${node.duration_ms ? `| 耗时: ${Math.round(node.duration_ms)}ms` : ''}
                </div>
                ${node.input_data && Object.keys(node.input_data).length > 0 ? `
                    <details style="margin-top: 8px;">
                        <summary style="font-size: 12px; cursor: pointer;">输入数据</summary>
                        <pre style="margin-top: 8px; font-size: 11px; overflow-x: auto;">${escapeHtml(JSON.stringify(node.input_data, null, 2))}</pre>
                    </details>
                ` : ''}
                ${node.output_data && Object.keys(node.output_data).length > 0 ? `
                    <details style="margin-top: 8px;">
                        <summary style="font-size: 12px; cursor: pointer;">输出数据</summary>
                        <pre style="margin-top: 8px; font-size: 11px; overflow-x: auto;">${escapeHtml(JSON.stringify(node.output_data, null, 2))}</pre>
                    </details>
                ` : ''}
            </div>
        `).join('');
        
        details.innerHTML = `
            <p><strong>链路 ID:</strong> ${chain.chain_id}</p>
            <p><strong>会话 ID:</strong> ${chain.session_id}</p>
            <p><strong>用户输入:</strong> ${escapeHtml(chain.user_input)}</p>
            <p><strong>状态:</strong> <span style="color: ${chain.final_status === 'completed' ? 'var(--success)' : chain.final_status === 'blocked' ? 'var(--danger)' : 'var(--warning)'}">${chain.final_status}</span></p>
            <p><strong>耗时:</strong> ${chain.duration_sec ? chain.duration_sec + 's' : 'N/A'}</p>
            <p><strong>开始时间:</strong> ${new Date(chain.start_time * 1000).toLocaleString()}</p>
            <hr style="border-color: var(--border); margin: 16px 0;">
            <h4>执行节点 (${chain.nodes.length})</h4>
            ${nodesHtml}
        `;
        
        chainModal.classList.add('active');
    } catch (e) {
        alert('加载链路详情失败: ' + e.message);
    }
}


// ===== 启动 =====
document.addEventListener('DOMContentLoaded', init);


// ===== 监控仪表盘 =====
let dashboardAutoRefresh = true;
let dashboardRefreshInterval = null;
let cpuMemChart = null;
let diskChart = null;
let serviceChart = null;
let networkChart = null;
let dashboardHistory = [];

function initDashboardCharts() {
    if (typeof echarts === 'undefined') {
        console.warn('ECharts 未加载，仪表盘图表不可用');
        return;
    }
    const cpuMemEl = document.getElementById('cpuMemChart');
    const diskEl = document.getElementById('diskChart');
    const svcEl = document.getElementById('serviceChart');
    const netEl = document.getElementById('networkChart');
    if (cpuMemEl) cpuMemChart = echarts.init(cpuMemEl);
    if (diskEl) diskChart = echarts.init(diskEl);
    if (svcEl) serviceChart = echarts.init(svcEl);
    if (netEl) networkChart = echarts.init(netEl);
    
    window.addEventListener('resize', () => {
        if (cpuMemChart) cpuMemChart.resize();
        if (diskChart) diskChart.resize();
        if (serviceChart) serviceChart.resize();
        if (networkChart) networkChart.resize();
    });
}

function startDashboardAutoRefresh() {
    if (dashboardRefreshInterval) clearInterval(dashboardRefreshInterval);
    dashboardRefreshInterval = setInterval(() => {
        const dashboardView = document.getElementById('dashboardView');
        if (dashboardAutoRefresh && dashboardView && dashboardView.classList.contains('active')) {
            loadDashboard();
        }
    }, 5000);
}

function stopDashboardAutoRefresh() {
    if (dashboardRefreshInterval) {
        clearInterval(dashboardRefreshInterval);
        dashboardRefreshInterval = null;
    }
}

async function loadDashboard() {
    try {
        // 同时获取概览和历史数据
        const [overviewRes, historyRes] = await Promise.all([
            apiGet('/monitor/overview'),
            apiGet('/monitor/history?points=60')
        ]);
        
        if (!overviewRes.success || !overviewRes.data) {
            console.error('仪表盘数据加载失败');
            return;
        }
        
        const data = overviewRes.data;
        dashboardHistory = historyRes.data || [];
        
        updateMetricCards(data);
        updateCpuMemChart(dashboardHistory);
        updateDiskChart(data.disk);
        updateServiceChart(data.services);
        updateNetworkChart(dashboardHistory);
        updateProcessTable(data.processes);
        updateNetworkCards(data.network);
        updateSystemInfo(data.system);
        checkMetricAlerts(data);
        
        const updateTime = document.getElementById('dashboardLastUpdate');
        if (updateTime) {
            updateTime.textContent = '更新于 ' + new Date().toLocaleTimeString();
        }
    } catch (e) {
        console.error('仪表盘加载失败:', e);
    }
}

function updateMetricCards(data) {
    const cpu = data.cpu || {};
    const mem = data.memory || {};
    const disk = data.disk || {};
    const proc = data.processes || {};
    const svc = data.services || {};
    const sys = data.system || {};
    
    const cpuEl = document.getElementById('cpuValue');
    if (cpuEl) {
        cpuEl.textContent = (cpu.usage_percent !== undefined ? cpu.usage_percent : '--') + '%';
        cpuEl.style.color = getUsageColor(cpu.usage_percent);
    }
    
    const memEl = document.getElementById('memValue');
    if (memEl) {
        memEl.textContent = (mem.usage_percent !== undefined ? mem.usage_percent : '--') + '%';
        memEl.style.color = getUsageColor(mem.usage_percent);
    }
    
    const diskEl = document.getElementById('diskValue');
    if (diskEl) {
        const maxDisk = disk.partitions && disk.partitions[0] ? disk.partitions[0].usage_percent : 0;
        diskEl.textContent = (maxDisk !== undefined ? maxDisk : '--') + '%';
        diskEl.style.color = getUsageColor(maxDisk);
    }
    
    const procEl = document.getElementById('procValue');
    if (procEl) {
        procEl.textContent = proc.total !== undefined ? proc.total : '--';
    }
    
    const svcEl = document.getElementById('svcValue');
    if (svcEl) {
        svcEl.textContent = svc.running !== undefined ? svc.running : '--';
    }
    
    const uptimeEl = document.getElementById('uptimeValue');
    if (uptimeEl && sys.uptime_seconds) {
        uptimeEl.textContent = formatUptime(sys.uptime_seconds);
    }
}

function getUsageColor(percent) {
    if (percent === undefined || percent === null) return 'var(--text-primary)';
    if (percent >= 90) return 'var(--danger)';
    if (percent >= 70) return 'var(--warning)';
    return 'var(--success)';
}

function formatUptime(seconds) {
    const d = Math.floor(seconds / 86400);
    const h = Math.floor((seconds % 86400) / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    if (d > 0) return `${d}天 ${h}小时`;
    if (h > 0) return `${h}小时 ${m}分钟`;
    return `${m}分钟`;
}

function updateCpuMemChart(history) {
    if (!cpuMemChart || typeof echarts === 'undefined') return;
    
    const timestamps = history.map(h => {
        const d = new Date(h.timestamp * 1000);
        return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0') + ':' + d.getSeconds().toString().padStart(2, '0');
    });
    const cpuData = history.map(h => h.cpu ? h.cpu.usage_percent : 0);
    const memData = history.map(h => h.memory ? h.memory.usage_percent : 0);
    
    const option = {
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' }
        },
        legend: {
            data: ['CPU', '内存'],
            textStyle: { color: '#64748b' },
            bottom: 0
        },
        grid: { left: '10%', right: '5%', top: '10%', bottom: '20%' },
        xAxis: {
            type: 'category',
            data: timestamps,
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            axisLabel: { color: '#64748b', fontSize: 10 }
        },
        yAxis: {
            type: 'value',
            max: 100,
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            splitLine: { lineStyle: { color: '#e2e8f0' } },
            axisLabel: { color: '#64748b', formatter: '{value}%' }
        },
        series: [
            {
                name: 'CPU',
                type: 'line',
                smooth: true,
                data: cpuData,
                lineStyle: { color: '#2563eb', width: 2 },
                areaStyle: { color: 'rgba(37, 99, 235, 0.15)' },
                itemStyle: { color: '#2563eb' },
                showSymbol: false
            },
            {
                name: '内存',
                type: 'line',
                smooth: true,
                data: memData,
                lineStyle: { color: '#16a34a', width: 2 },
                areaStyle: { color: 'rgba(22, 163, 74, 0.15)' },
                itemStyle: { color: '#16a34a' },
                showSymbol: false
            }
        ]
    };
    cpuMemChart.setOption(option);
}

function updateDiskChart(diskData) {
    if (!diskChart || typeof echarts === 'undefined') return;
    
    const partitions = (diskData && diskData.partitions) ? diskData.partitions : [];
    const data = partitions.map(p => ({
        name: p.mount,
        value: p.usage_percent
    }));
    
    const option = {
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'item',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' },
            formatter: '{b}: {c}%'
        },
        series: [
            {
                type: 'pie',
                radius: ['40%', '70%'],
                center: ['50%', '50%'],
                avoidLabelOverlap: true,
                itemStyle: {
                    borderRadius: 6,
                    borderColor: '#1e293b',
                    borderWidth: 2
                },
                label: {
                    show: true,
                    color: '#94a3b8',
                    formatter: '{b}\n{c}%'
                },
                labelLine: { lineStyle: { color: '#334155' } },
                data: data.length > 0 ? data : [{name: '无数据', value: 0}],
                color: ['#2563eb', '#16a34a', '#f59e0b', '#dc2626', '#8b5cf6', '#06b6d4']
            }
        ]
    };
    diskChart.setOption(option);
}

function updateServiceChart(services) {
    if (!serviceChart || typeof echarts === 'undefined') return;
    
    const running = services && services.running !== undefined ? services.running : 0;
    const failed = services && services.failed !== undefined ? services.failed : 0;
    const total = services && services.total !== undefined ? services.total : 0;
    const other = Math.max(0, total - running - failed);
    
    const option = {
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'item',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' }
        },
        legend: {
            orient: 'vertical',
            right: '5%',
            top: 'center',
            textStyle: { color: '#94a3b8' }
        },
        series: [
            {
                type: 'pie',
                radius: ['40%', '65%'],
                center: ['35%', '50%'],
                avoidLabelOverlap: true,
                itemStyle: {
                    borderRadius: 6,
                    borderColor: '#1e293b',
                    borderWidth: 2
                },
                label: {
                    show: true,
                    color: '#94a3b8',
                    formatter: '{c}'
                },
                data: [
                    { name: '运行中', value: running, itemStyle: { color: '#16a34a' } },
                    { name: '失败', value: failed, itemStyle: { color: '#dc2626' } },
                    { name: '其他', value: other, itemStyle: { color: '#64748b' } }
                ].filter(d => d.value > 0)
            }
        ]
    };
    serviceChart.setOption(option);
}

function updateProcessTable(processes) {
    const tbody = document.querySelector('#processTable tbody');
    if (!tbody) return;
    
    const top = (processes && processes.top_cpu) ? processes.top_cpu : [];
    if (top.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;color:var(--text-secondary)">暂无数据</td></tr>';
        return;
    }
    
    tbody.innerHTML = top.map(p => `
        <tr>
            <td>${p.pid}</td>
            <td>${escapeHtml(p.user)}</td>
            <td style="color:${getUsageColor(p.cpu)}">${p.cpu}%</td>
            <td>${p.mem}%</td>
            <td title="${escapeHtml(p.command)}">${escapeHtml(p.command.length > 40 ? p.command.substring(0, 40) + '...' : p.command)}</td>
        </tr>
    `).join('');
}

function updateNetworkCards(network) {
    const container = document.getElementById('networkCards');
    if (!container) return;
    
    const interfaces = (network && network.interfaces) ? network.interfaces : [];
    if (interfaces.length === 0) {
        container.innerHTML = '<p style="color:var(--text-secondary)">暂无网络接口数据</p>';
        return;
    }
    
    container.innerHTML = interfaces.map(iface => `
        <div class="network-card">
            <div class="network-card-header">
                <span class="network-name">${escapeHtml(iface.name)}</span>
                <span class="network-status ${iface.status === 'up' ? 'up' : 'down'}">${iface.status === 'up' ? '● 已连接' : '○ 断开'}</span>
            </div>
            <div class="network-card-body">
                <div><span>IP:</span> <code>${escapeHtml(iface.ip || 'N/A')}</code></div>
                <div><span>RX:</span> ${formatBytes(iface.rx_bytes || 0)}</div>
                <div><span>TX:</span> ${formatBytes(iface.tx_bytes || 0)}</div>
            </div>
        </div>
    `).join('');
}

function updateSystemInfo(sys) {
    if (!sys) return;
    // 已在 metric cards 中展示 uptime
}



// ===== 审计日志统计 =====
let auditStatusChart = null;
let auditTrendChart = null;
let auditToolsChart = null;
let auditRiskChart = null;
let auditBlockedChart = null;

function initAuditStatsCharts() {
    if (typeof echarts === 'undefined') return;
    const els = ['auditStatusChart', 'auditTrendChart', 'auditToolsChart', 'auditRiskChart', 'auditBlockedChart'];
    els.forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            const chart = echarts.init(el);
            if (id === 'auditStatusChart') auditStatusChart = chart;
            if (id === 'auditTrendChart') auditTrendChart = chart;
            if (id === 'auditToolsChart') auditToolsChart = chart;
            if (id === 'auditRiskChart') auditRiskChart = chart;
            if (id === 'auditBlockedChart') auditBlockedChart = chart;
        }
    });
}

async function loadAuditStats() {
    const daysSelect = document.getElementById('auditStatsDays');
    const days = daysSelect ? parseInt(daysSelect.value) : 7;
    
    try {
        const data = await apiGet('/audit/stats?days=' + days);
        if (!data.success || !data.data) {
            console.error('审计统计加载失败');
            return;
        }
        
        const stats = data.data;
        updateAuditSummary(stats);
        updateAuditStatusChart(stats.status_distribution);
        updateAuditTrendChart(stats.daily_trend);
        updateAuditToolsChart(stats.top_tools);
        updateAuditRiskChart(stats.risk_distribution);
        updateAuditBlockedChart(stats.blocked_reasons);
    } catch (e) {
        console.error('审计统计加载失败:', e);
    }
}

function updateAuditSummary(stats) {
    const totalEl = document.getElementById('statTotal');
    const blockedEl = document.getElementById('statBlocked');
    const blockRateEl = document.getElementById('statBlockRate');
    const avgDurationEl = document.getElementById('statAvgDuration');
    
    if (totalEl) totalEl.textContent = stats.total || 0;
    if (blockedEl) blockedEl.textContent = stats.blocked_count || 0;
    if (blockRateEl) blockRateEl.textContent = (stats.block_rate || 0) + '%';
    if (avgDurationEl) {
        const avg = stats.duration_stats && stats.duration_stats.avg_sec;
        avgDurationEl.textContent = avg !== undefined ? avg + 's' : '--';
    }
}

function updateAuditStatusChart(statusDist) {
    if (!auditStatusChart || typeof echarts === 'undefined') return;
    const data = Object.entries(statusDist || {}).map(([name, value]) => ({ name, value }));
    const colorMap = {
        'completed': '#16a34a',
        'blocked': '#dc2626',
        'failed': '#f59e0b',
        'running': '#2563eb',
        'pending': '#8b5cf6'
    };
    
    auditStatusChart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'item',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' }
        },
        series: [{
            type: 'pie',
            radius: ['40%', '70%'],
            center: ['50%', '50%'],
            avoidLabelOverlap: true,
            itemStyle: { borderRadius: 6, borderColor: '#1e293b', borderWidth: 2 },
            label: { show: true, color: '#94a3b8', formatter: '{b}\n{c}' },
            labelLine: { lineStyle: { color: '#334155' } },
            data: data.length > 0 ? data : [{ name: '无数据', value: 0 }],
            color: data.map(d => colorMap[d.name] || '#64748b')
        }]
    });
}

function updateAuditTrendChart(dailyTrend) {
    if (!auditTrendChart || typeof echarts === 'undefined') return;
    const dates = Object.keys(dailyTrend || {});
    const values = Object.values(dailyTrend || {});
    
    auditTrendChart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' }
        },
        grid: { left: '12%', right: '5%', top: '10%', bottom: '15%' },
        xAxis: {
            type: 'category',
            data: dates,
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            axisLabel: { color: '#64748b', fontSize: 10 }
        },
        yAxis: {
            type: 'value',
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            splitLine: { lineStyle: { color: '#e2e8f0' } },
            axisLabel: { color: '#64748b' }
        },
        series: [{
            type: 'bar',
            data: values,
            itemStyle: { color: '#2563eb', borderRadius: [4, 4, 0, 0] },
            barWidth: '60%'
        }]
    });
}

function updateAuditToolsChart(topTools) {
    if (!auditToolsChart || typeof echarts === 'undefined') return;
    const items = Object.entries(topTools || {}).sort((a, b) => b[1] - a[1]).slice(0, 10);
    const names = items.map(([name]) => name);
    const values = items.map(([, value]) => value);
    
    auditToolsChart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' }
        },
        grid: { left: '25%', right: '10%', top: '5%', bottom: '5%' },
        xAxis: {
            type: 'value',
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            splitLine: { lineStyle: { color: '#e2e8f0' } },
            axisLabel: { color: '#64748b' }
        },
        yAxis: {
            type: 'category',
            data: names.reverse(),
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            axisLabel: { color: '#94a3b8', fontSize: 11 }
        },
        series: [{
            type: 'bar',
            data: values.reverse(),
            itemStyle: {
                color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [
                    { offset: 0, color: '#3b82f6' },
                    { offset: 1, color: '#2563eb' }
                ]),
                borderRadius: [0, 4, 4, 0]
            },
            barWidth: '60%'
        }]
    });
}

function updateAuditRiskChart(riskDist) {
    if (!auditRiskChart || typeof echarts === 'undefined') return;
    const data = Object.entries(riskDist || {}).map(([name, value]) => ({ name, value }));
    const colorMap = {
        'safe': '#16a34a',
        'low': '#3b82f6',
        'medium': '#f59e0b',
        'high': '#dc2626',
        'critical': '#7f1d1d'
    };
    
    auditRiskChart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'item',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' }
        },
        series: [{
            type: 'pie',
            radius: ['30%', '60%'],
            center: ['50%', '50%'],
            roseType: 'area',
            itemStyle: { borderRadius: 4, borderColor: '#1e293b', borderWidth: 1 },
            label: { show: true, color: '#94a3b8', formatter: '{b}\n{c}' },
            labelLine: { lineStyle: { color: '#334155' } },
            data: data.length > 0 ? data : [{ name: '无数据', value: 0 }],
            color: data.map(d => colorMap[d.name] || '#64748b')
        }]
    });
}

function updateAuditBlockedChart(blockedReasons) {
    if (!auditBlockedChart || typeof echarts === 'undefined') return;
    const data = Object.entries(blockedReasons || {}).map(([name, value]) => ({ name, value }));
    
    auditBlockedChart.setOption({
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'item',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' }
        },
        series: [{
            type: 'pie',
            radius: ['35%', '65%'],
            center: ['50%', '50%'],
            itemStyle: { borderRadius: 6, borderColor: '#1e293b', borderWidth: 2 },
            label: { show: true, color: '#94a3b8', formatter: '{b}: {c}' },
            labelLine: { lineStyle: { color: '#334155' } },
            data: data.length > 0 ? data : [{ name: '无拦截数据', value: 0 }],
            color: ['#dc2626', '#f59e0b', '#64748b']
        }]
    });
}


// ===== 历史会话列表 =====
async function loadSessions() {
    const list = document.getElementById('sessionList');
    if (!list) return;
    
    try {
        const data = await apiGet('/sessions?limit=20');
        const sessions = data.sessions || [];
        
        if (sessions.length === 0) {
            list.innerHTML = '<p class="session-empty">暂无历史会话</p>';
            return;
        }
        
        list.innerHTML = sessions.map(s => `
            <div class="session-item ${s.session_id === currentSessionId ? 'active' : ''}" data-session-id="${escapeHtml(s.session_id)}">
                <span class="session-title">${escapeHtml(s.title || s.session_id)}</span>
                <span class="session-time">${formatSessionTime(s.last_active)}</span>
            </div>
        `).join('');
        
        list.querySelectorAll('.session-item').forEach(item => {
            item.addEventListener('click', () => {
                const sid = item.dataset.sessionId;
                switchSession(sid);
            });
        });
    } catch (e) {
        list.innerHTML = '<p class="session-empty">加载失败</p>';
    }
}

async function switchSession(sessionId) {
    currentSessionId = sessionId;
    StorageManager.setSessionId(sessionId);
    sessionIdEl.textContent = sessionId;
    
    // 切换到聊天视图
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    document.querySelector('.nav-btn[data-view="chat"]').classList.add('active');
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    document.getElementById('chatView').classList.add('active');
    
    // 从后端加载消息历史
    try {
        const data = await apiGet('/session/' + sessionId);
        const messages = data.messages || [];
        renderSessionMessages(messages);
        loadSessions(); // 刷新列表高亮
    } catch (e) {
        chatMessages.innerHTML = `
            <div class="welcome-message">
                <h3><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> 加载会话失败</h3>
                <p>${escapeHtml(e.message)}</p>
            </div>
        `;
    }
}

function renderSessionMessages(messages) {
    chatMessages.innerHTML = '';
    
    if (messages.length === 0) {
        chatMessages.innerHTML = `
            <div class="welcome-message">
                <h3><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 9V5a3 3 0 00-3-3l-4 9v11h11.28a2 2 0 002-1.7l1.38-9a2 2 0 00-2-2.3H14zM7 22H4a2 2 0 01-2-2v-7a2 2 0 012-2h3"/></svg> 欢迎使用</h3>
                <p>请输入您的运维指令...</p>
            </div>
        `;
        return;
    }
    
    for (const msg of messages) {
        if (!msg || typeof msg !== 'object') continue;
        const meta = {};
        if (msg.role === 'assistant') {
            // 尝试从消息内容中判断状态
            if (msg.content && typeof msg.content === 'string' && msg.content.startsWith('🛡️')) {
                meta.blocked = true;
            } else if (msg.content && typeof msg.content === 'string' && msg.content.startsWith('⚠️')) {
                meta.requiresConfirm = true;
            } else if (msg.content && typeof msg.content === 'string' && msg.content.startsWith('✅ 用户已确认执行')) {
                meta.confirmed = true;
            }
        }
        addMessage(msg.role, msg.content, meta);
    }
}

function formatSessionTime(timestamp) {
    const d = new Date(timestamp * 1000);
    const now = new Date();
    const diff = (now - d) / 1000;
    
    if (diff < 60) return '刚刚';
    if (diff < 3600) return Math.floor(diff / 60) + '分钟前';
    if (diff < 86400) return Math.floor(diff / 3600) + '小时前';
    return (d.getMonth() + 1) + '-' + d.getDate();
}


// ===== root 权限申请 =====

// ===== 网络流量趋势图 =====
function updateNetworkChart(history) {
    if (!networkChart || typeof echarts === 'undefined') return;
    
    const timestamps = history.map(h => {
        const d = new Date(h.timestamp * 1000);
        return d.getHours().toString().padStart(2, '0') + ':' + d.getMinutes().toString().padStart(2, '0') + ':' + d.getSeconds().toString().padStart(2, '0');
    });
    
    // 聚合所有接口的 RX/TX 速率
    const rxData = history.map(h => {
        const net = h.network || {};
        const ifaces = net.interfaces || [];
        return Math.round(ifaces.reduce((sum, iface) => sum + (iface.rx_rate_kb || 0), 0));
    });
    const txData = history.map(h => {
        const net = h.network || {};
        const ifaces = net.interfaces || [];
        return Math.round(ifaces.reduce((sum, iface) => sum + (iface.tx_rate_kb || 0), 0));
    });
    
    const option = {
        backgroundColor: 'transparent',
        tooltip: {
            trigger: 'axis',
            backgroundColor: 'rgba(255, 255, 255, 0.95)',
            borderColor: '#e2e8f0',
            textStyle: { color: '#1e293b' }
        },
        legend: {
            data: ['接收 (RX)', '发送 (TX)'],
            textStyle: { color: '#64748b' },
            bottom: 0
        },
        grid: { left: '10%', right: '5%', top: '10%', bottom: '20%' },
        xAxis: {
            type: 'category',
            data: timestamps,
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            axisLabel: { color: '#64748b', fontSize: 10 }
        },
        yAxis: {
            type: 'value',
            axisLine: { lineStyle: { color: '#e2e8f0' } },
            splitLine: { lineStyle: { color: '#e2e8f0' } },
            axisLabel: { color: '#64748b', formatter: '{value} KB/s' }
        },
        series: [
            {
                name: '接收 (RX)',
                type: 'line',
                smooth: true,
                data: rxData,
                lineStyle: { color: '#16a34a', width: 2 },
                areaStyle: { color: 'rgba(22, 163, 74, 0.15)' },
                itemStyle: { color: '#16a34a' },
                showSymbol: false
            },
            {
                name: '发送 (TX)',
                type: 'line',
                smooth: true,
                data: txData,
                lineStyle: { color: '#f59e0b', width: 2 },
                areaStyle: { color: 'rgba(245, 158, 11, 0.15)' },
                itemStyle: { color: '#f59e0b' },
                showSymbol: false
            }
        ]
    };
    networkChart.setOption(option);
}

// ===== 监控告警阈值检测 =====
function checkMetricAlerts(data) {
    const cpu = data.cpu || {};
    const mem = data.memory || {};
    const disk = data.disk || {};
    
    // CPU 告警
    const cpuEl = document.getElementById('cpuValue');
    if (cpuEl) {
        const pct = cpu.usage_percent || 0;
        setAlertClass(cpuEl.parentElement.parentElement, pct);
    }
    
    // 内存告警
    const memEl = document.getElementById('memValue');
    if (memEl) {
        const pct = mem.usage_percent || 0;
        setAlertClass(memEl.parentElement.parentElement, pct);
    }
    
    // 磁盘告警
    const diskEl = document.getElementById('diskValue');
    if (diskEl) {
        const partitions = disk.partitions || [];
        const maxPct = partitions.length > 0 ? partitions[0].usage_percent : 0;
        setAlertClass(diskEl.parentElement.parentElement, maxPct);
    }
}

function setAlertClass(element, percent) {
    if (!element) return;
    element.classList.remove('alert-warning', 'alert-critical');
    if (percent >= 90) {
        element.classList.add('alert-critical');
    } else if (percent >= 80) {
        element.classList.add('alert-warning');
    }
}


// ===== 用户体验增强功能 =====

// 通知系统


// 全局搜索功能
function initGlobalSearch() {
    const searchInput = document.createElement('input');
    searchInput.type = 'text';
    searchInput.placeholder = '搜索会话、命令...';
    searchInput.className = 'global-search-input';
    searchInput.style.cssText = `
        position: fixed;
        top: 20px;
        left: 50%;
        transform: translateX(-50%);
        width: 400px;
        max-width: 90%;
        padding: 12px 16px;
        background: var(--bg-card);
        border: 1px solid var(--border);
        border-radius: var(--radius-lg);
        color: var(--text-primary);
        font-size: 14px;
        outline: none;
        z-index: 3000;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        display: none;
    `;
    
    document.body.appendChild(searchInput);
    
    // Ctrl/Cmd + K 打开搜索
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
            e.preventDefault();
            searchInput.style.display = 'block';
            searchInput.focus();
        }
        if (e.key === 'Escape') {
            searchInput.style.display = 'none';
            searchInput.value = '';
        }
    });
    
    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        if (query.length > 0) {
            // 搜索历史会话
            const sessionItems = document.querySelectorAll('.session-item');
            sessionItems.forEach(item => {
                const title = item.querySelector('.session-title').textContent.toLowerCase();
                item.style.display = title.includes(query) ? 'block' : 'none';
            });
        } else {
            document.querySelectorAll('.session-item').forEach(item => {
                item.style.display = 'block';
            });
        }
    });
}

// 打字机效果
function typewriterEffect(element, text, speed = 30) {
    let i = 0;
    element.textContent = '';
    
    function type() {
        if (i < text.length) {
            element.textContent += text.charAt(i);
            i++;
            setTimeout(type, speed);
        }
    }
    
    type();
}

// 消息操作菜单
function addMessageMenu(messageEl) {
    const menu = document.createElement('div');
    menu.className = 'message-menu';
    menu.innerHTML = `
        <button class="msg-menu-btn" data-action="copy"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg> 复制</button>
        <button class="msg-menu-btn" data-action="quote"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg> 引用</button>
        <button class="msg-menu-btn" data-action="delete"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg> 删除</button>
    `;
    
    messageEl.appendChild(menu);
    
    // 点击消息时显示菜单
    messageEl.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        menu.style.display = 'block';
        menu.style.left = e.offsetX + 'px';
        menu.style.top = e.offsetY + 'px';
    });
    
    // 点击其他地方隐藏菜单
    document.addEventListener('click', () => {
        menu.style.display = 'none';
    });
    
    // 菜单按钮事件
    menu.querySelectorAll('.msg-menu-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = btn.dataset.action;
            const content = messageEl.querySelector('.message-content').textContent;
            
            if (action === 'copy') {
                navigator.clipboard.writeText(content).then(() => {
                    showNotification('成功', '消息内容已复制');
                });
            } else if (action === 'quote') {
                chatInput.value = `> ${content}\n\n`;
                chatInput.focus();
            }
            menu.style.display = 'none';
        });
    });
}

// 键盘快捷键
function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Ctrl/Cmd + Enter 发送消息
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            e.preventDefault();
            sendMessage();
        }
        
        // Ctrl + / 显示快捷键帮助
        if ((e.ctrlKey || e.metaKey) && e.key === '/') {
            e.preventDefault();
            showShortcutHelp();
        }
        
        // Alt + 左/右切换视图
        if (e.altKey && e.key === 'ArrowLeft') {
            e.preventDefault();
            switchToPrevView();
        }
        if (e.altKey && e.key === 'ArrowRight') {
            e.preventDefault();
            switchToNextView();
        }
    });
}

function switchToPrevView() {
    const views = ['chat', 'dashboard', 'audit', 'tools', 'config'];
    const activeBtn = document.querySelector('.nav-btn.active');
    if (!activeBtn) return;
    
    const currentView = activeBtn.dataset.view;
    const currentIndex = views.indexOf(currentView);
    const prevIndex = currentIndex > 0 ? currentIndex - 1 : views.length - 1;
    
    document.querySelector(`.nav-btn[data-view="${views[prevIndex]}"]`).click();
}

function switchToNextView() {
    const views = ['chat', 'dashboard', 'audit', 'tools', 'config'];
    const activeBtn = document.querySelector('.nav-btn.active');
    if (!activeBtn) return;
    
    const currentView = activeBtn.dataset.view;
    const currentIndex = views.indexOf(currentView);
    const nextIndex = currentIndex < views.length - 1 ? currentIndex + 1 : 0;
    
    document.querySelector(`.nav-btn[data-view="${views[nextIndex]}"]`).click();
}

function showShortcutHelp() {
    const helpContent = `
        <h3><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="16" rx="2" ry="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M8 12h.01M12 12h.01M16 12h.01M6 16h.01M10 16h4M18 16h.01"/></svg> 键盘快捷键</h3>
        <div style="margin-top: 16px; line-height: 1.8;">
            <p><kbd>Ctrl/Cmd + Enter</kbd> - 发送消息</p>
            <p><kbd>Ctrl/Cmd + K</kbd> - 打开搜索</p>
            <p><kbd>Ctrl/Cmd + /</kbd> - 显示快捷键帮助</p>
            <p><kbd>Alt + ←/→</kbd> - 切换视图</p>
            <p><kbd>Esc</kbd> - 关闭弹窗/搜索</p>
        </div>
        <style>
            kbd {
                background: var(--bg-dark);
                padding: 4px 8px;
                border-radius: 4px;
                font-family: monospace;
                font-size: 12px;
                border: 1px solid var(--border);
            }
        </style>
    `;
    
    const modal = document.createElement('div');
    modal.className = 'modal active';
    modal.innerHTML = `
        <div class="modal-content" style="max-width: 400px;">
            <div class="modal-header">
                <h3>快捷键帮助</h3>
                <button class="close-btn" onclick="this.closest('.modal').remove()">&times;</button>
            </div>
            <div class="modal-body">${helpContent}</div>
        </div>
    `;
    
    document.body.appendChild(modal);
    
    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });
}

// ===== 通知系统 =====

// ===== 全局搜索功能 =====
function initGlobalSearch() {
    const searchModal = document.getElementById('searchModal');
    const searchInput = document.getElementById('searchInput');
    const searchClose = document.getElementById('searchClose');
    const searchResults = document.getElementById('searchResults');
    
    if (!searchModal || !searchInput || !searchClose || !searchResults) return;
    
    // Ctrl/Cmd + K 打开搜索
    document.addEventListener('keydown', (e) => {
        if ((e.ctrlKey || e.metaKey) && e.key === 'k' && !e.altKey && !e.shiftKey) {
            e.preventDefault();
            if (!searchModal.classList.contains('active')) {
                searchModal.classList.add('active');
                searchInput.value = '';
                searchInput.focus();
                renderSearchResults('');
            }
        }
        if (e.key === 'Escape') {
            if (searchModal.classList.contains('active')) {
                searchModal.classList.remove('active');
                searchInput.value = '';
            }
        }
    });
    
    // 关闭按钮
    searchClose.addEventListener('click', () => {
        searchModal.classList.remove('active');
        searchInput.value = '';
    });
    
    // 点击遮罩关闭
    searchModal.addEventListener('click', (e) => {
        if (e.target === searchModal) {
            searchModal.classList.remove('active');
            searchInput.value = '';
        }
    });
    
    // 搜索输入
    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.toLowerCase();
        renderSearchResults(query);
    });
    
    // 键盘导航
    let currentIndex = -1;
    searchInput.addEventListener('keydown', (e) => {
        const items = searchResults.querySelectorAll('.search-result-item');
        if (items.length === 0) return;
        
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            currentIndex = Math.min(currentIndex + 1, items.length - 1);
            updateActiveResult(items, currentIndex);
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            currentIndex = Math.max(currentIndex - 1, 0);
            updateActiveResult(items, currentIndex);
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (currentIndex >= 0 && currentIndex < items.length) {
                items[currentIndex].click();
            }
        }
    });
}

function renderSearchResults(query) {
    const searchResults = document.getElementById('searchResults');
    const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
    
    let filteredSessions = sessions;
    if (query) {
        filteredSessions = sessions.filter(s => 
            s.title.toLowerCase().includes(query)
        );
    }
    
    if (filteredSessions.length === 0) {
        searchResults.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-muted);">未找到匹配的会话</div>';
        return;
    }
    
    searchResults.innerHTML = filteredSessions.map((session, index) => `
        <div class="search-result-item" data-session-id="${session.id}" data-index="${index}">
            <div class="search-result-icon"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg></div>
            <div class="search-result-info">
                <div class="search-result-title">${escapeHtml(session.title)}</div>
                <div class="search-result-time">${formatTime(session.timestamp)}</div>
            </div>
        </div>
    `).join('');
    
    // 添加点击事件
    searchResults.querySelectorAll('.search-result-item').forEach(item => {
        item.addEventListener('click', () => {
            const sessionId = item.dataset.sessionId;
            loadSession(sessionId);
            document.getElementById('searchModal').classList.remove('active');
            document.getElementById('searchInput').value = '';
        });
    });
    
    // 重置索引
    currentIndex = -1;
}

function updateActiveResult(items, index) {
    items.forEach((item, i) => {
        item.classList.toggle('active', i === index);
    });
    if (items[index]) {
        items[index].scrollIntoView({ block: 'nearest' });
    }
}

function formatTime(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now.getTime() - date.getTime();
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    
    if (days === 0) {
        return '今天 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    } else if (days === 1) {
        return '昨天 ' + date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    } else if (days < 7) {
        return date.toLocaleDateString('zh-CN', { weekday: 'short' });
    } else {
        return date.toLocaleDateString('zh-CN');
    }
}

// ===== 消息上下文菜单 =====
function initContextMenu() {
    const contextMenu = document.getElementById('contextMenu');
    if (!contextMenu) return;
    
    let currentMessage = null;
    
    // 点击消息显示菜单
    document.addEventListener('contextmenu', (e) => {
        const messageEl = e.target.closest('.message');
        if (messageEl) {
            e.preventDefault();
            currentMessage = messageEl;
            
            const rect = messageEl.getBoundingClientRect();
            const x = e.clientX;
            const y = e.clientY;
            
            // 确保菜单不超出视窗
            const menuWidth = contextMenu.offsetWidth || 160;
            const menuHeight = contextMenu.offsetHeight || 100;
            const finalX = Math.min(x, window.innerWidth - menuWidth - 10);
            const finalY = Math.min(y, window.innerHeight - menuHeight - 10);
            
            contextMenu.style.left = finalX + 'px';
            contextMenu.style.top = finalY + 'px';
            contextMenu.classList.add('active');
        }
    });
    
    // 点击其他地方隐藏菜单
    document.addEventListener('click', (e) => {
        if (!contextMenu.contains(e.target)) {
            contextMenu.classList.remove('active');
            currentMessage = null;
        }
    });
    
    // 复制功能
    document.getElementById('ctxCopy').addEventListener('click', () => {
        if (!currentMessage) return;
        const content = currentMessage.querySelector('.message-content').textContent;
        navigator.clipboard.writeText(content).then(() => {
            showNotification('成功', '消息内容已复制到剪贴板');
        }).catch(() => {
            showNotification('错误', '复制失败，请手动复制');
        });
        contextMenu.classList.remove('active');
    });
    
    // 引用功能
    document.getElementById('ctxQuote').addEventListener('click', () => {
        if (!currentMessage) return;
        const content = currentMessage.querySelector('.message-content').textContent;
        const chatInput = document.getElementById('chatInput');
        if (chatInput) {
            chatInput.value = `> ${content}\n\n`;
            chatInput.focus();
        }
        contextMenu.classList.remove('active');
    });
    
    // 删除功能
    document.getElementById('ctxDelete').addEventListener('click', () => {
        if (!currentMessage) return;
        currentMessage.remove();
        showNotification('成功', '消息已删除');
        contextMenu.classList.remove('active');
    });
    
    // 翻译功能
    document.getElementById('ctxTranslate').addEventListener('click', async () => {
        if (!currentMessage) return;
        
        const contentEl = currentMessage.querySelector('.message-content');
        if (!contentEl) return;
        
        // 检查是否已有翻译
        const existingTranslation = currentMessage.querySelector('.message-translation');
        if (existingTranslation) {
            existingTranslation.remove();
            contextMenu.classList.remove('active');
            return;
        }
        
        try {
            const originalText = contentEl.textContent;
            const translated = await translateMessage(originalText);
            
            const translationEl = document.createElement('div');
            translationEl.className = 'message-translation';
            translationEl.textContent = translated;
            
            contentEl.parentNode.insertBefore(translationEl, contentEl.nextSibling);
            showNotification('成功', '翻译完成');
        } catch (error) {
            showNotification('翻译失败', error.message);
        }
        
        contextMenu.classList.remove('active');
    });
}

// ===== 键盘快捷键 =====
function initKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Ctrl/Cmd + Enter 发送消息
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
            const activeEl = document.activeElement;
            if (activeEl && activeEl.tagName === 'TEXTAREA') {
                e.preventDefault();
                sendMessage();
            }
        }
        
        // Ctrl/Cmd + K 打开搜索（已在initGlobalSearch中处理）
        
        // Ctrl + / 显示快捷键帮助
        if ((e.ctrlKey || e.metaKey) && e.key === '/') {
            e.preventDefault();
            showShortcutHelp();
        }
        
        // Alt + 左/右切换视图
        if (e.altKey && e.key === 'ArrowLeft') {
            e.preventDefault();
            switchToPrevView();
        }
        if (e.altKey && e.key === 'ArrowRight') {
            e.preventDefault();
            switchToNextView();
        }
        
        // Esc 关闭弹窗
        if (e.key === 'Escape') {
            // 关闭快捷键帮助
            const shortcutModal = document.getElementById('shortcutHelpModal');
            if (shortcutModal) {
                shortcutModal.classList.remove('active');
            }
        }
    });
}

function switchToPrevView() {
    const views = ['chat', 'dashboard', 'audit', 'tools', 'config'];
    const activeBtn = document.querySelector('.nav-btn.active');
    if (!activeBtn) return;
    
    const currentView = activeBtn.dataset.view;
    const currentIndex = views.indexOf(currentView);
    const prevIndex = currentIndex > 0 ? currentIndex - 1 : views.length - 1;
    
    document.querySelector(`.nav-btn[data-view="${views[prevIndex]}"]`)?.click();
}

function switchToNextView() {
    const views = ['chat', 'dashboard', 'audit', 'tools', 'config'];
    const activeBtn = document.querySelector('.nav-btn.active');
    if (!activeBtn) return;
    
    const currentView = activeBtn.dataset.view;
    const currentIndex = views.indexOf(currentView);
    const nextIndex = currentIndex < views.length - 1 ? currentIndex + 1 : 0;
    
    document.querySelector(`.nav-btn[data-view="${views[nextIndex]}"]`)?.click();
}

function showShortcutHelp() {
    const modal = document.getElementById('shortcutHelpModal');
    if (modal) {
        modal.classList.add('active');
        
        // 点击遮罩关闭
        modal.addEventListener('click', (e) => {
            if (e.target === modal) {
                modal.classList.remove('active');
            }
        });
        
        // 关闭按钮
        document.getElementById('closeShortcutHelp')?.addEventListener('click', () => {
            modal.classList.remove('active');
        });
    }
}

// ===== 初始化增强功能 =====
function initEnhancements() {
    initGlobalSearch();
    initContextMenu();
    initKeyboardShortcuts();
    initSettingsPanel();
    initDraftAutoSave();
    initImportExport();
    initCommandTemplates();
    initTemplateSelector();
    initBrowserNotification();
    initTranslateFeature();
    initAccessibility();
    initMobileFeatures();
    initEmojiPicker();
    initReactionFeature();
    initOnboarding();
    initHapticFeedback();
    initSessionFolders();
    initSessionActions();
    initFolderManagement();
    initSearchHistory();
    initDataBackup();
    initNetworkStatus();
    initOfflineSupport();
}

// 在初始化时启动增强功能
document.addEventListener('DOMContentLoaded', () => {
    setTimeout(initEnhancements, 500);
});

// ===== 草稿自动保存 =====
function initDraftAutoSave() {
    const chatInput = document.getElementById('chatInput');
    if (!chatInput) return;
    
    let saveTimeout;
    const inputWrapper = chatInput.closest('.input-wrapper');
    
    // 加载已保存的草稿
    const savedDraft = StorageManager.getChatDraft();
    if (savedDraft && chatInput.value === '') {
        chatInput.value = savedDraft;
        autoResizeTextarea(chatInput);
    }
    
    // 监听输入变化
    chatInput.addEventListener('input', () => {
        const autoSaveDraft = document.getElementById('autoSaveDraft');
        if (autoSaveDraft && !autoSaveDraft.checked) {
            StorageManager.removeChatDraft();
            return;
        }
        
        clearTimeout(saveTimeout);
        saveTimeout = setTimeout(() => {
            const draft = chatInput.value;
            if (draft.trim()) {
                StorageManager.setChatDraft(draft);
            } else {
                StorageManager.removeChatDraft();
            }
        }, 1000);
    });
    
    // 发送消息时清除草稿
    const originalSendMessage = window.sendMessage;
    window.sendMessage = function() {
        StorageManager.removeChatDraft();
        if (originalSendMessage) {
            return originalSendMessage.apply(this, arguments);
        }
    };
}

// ===== 导入导出功能 =====
function initImportExport() {
    // 导出所有会话
    document.getElementById('exportAllSessions')?.addEventListener('click', () => {
        const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
        const exportData = {
            version: '1.0',
            exportTime: new Date().toISOString(),
            sessions: sessions
        };
        
        const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `kylin-ops-sessions-${new Date().toISOString().slice(0, 10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        showNotification('成功', '会话已导出');
        playNotificationSound('success');
    });
    
    // 导入会话
    const importBtn = document.getElementById('importSessions');
    const importInput = document.getElementById('importFileInput');
    
    importBtn?.addEventListener('click', () => {
        importInput?.click();
    });
    
    importInput?.addEventListener('change', (e) => {
        const file = e.target.files[0];
        if (!file) return;
        
        const reader = new FileReader();
        reader.onload = (event) => {
            try {
                const data = JSON.parse(event.target.result);
                if (data.sessions && Array.isArray(data.sessions)) {
                    const existingSessions = JSON.parse(localStorage.getItem('sessions') || '[]');
                    const mergedSessions = [...existingSessions];
                    
                    // 合并会话，避免重复ID
                    data.sessions.forEach(session => {
                        if (!mergedSessions.find(s => s.id === session.id)) {
                            mergedSessions.push(session);
                        }
                    });
                    
                    StorageManager.setSessions(mergedSessions);
                    loadSessions();
                    showNotification('成功', `已导入 ${data.sessions.length} 个会话`);
                    playNotificationSound('success');
                } else {
                    showNotification('错误', '文件格式不正确');
                }
            } catch (err) {
                showNotification('错误', '解析文件失败: ' + err.message);
            }
        };
        reader.readAsText(file);
        
        // 清空input以允许重新选择同一文件
        e.target.value = '';
    });
}

// ===== 声音通知 =====
let notificationSound = null;
let soundEnabled = localStorage.getItem('soundEnabled') === 'true';

function playNotificationSound(type) {
    if (!soundEnabled) return;
    
    const audioContext = new (window.AudioContext || window.webkitAudioContext)();
    const oscillator = audioContext.createOscillator();
    const gainNode = audioContext.createGain();
    
    oscillator.connect(gainNode);
    gainNode.connect(audioContext.destination);
    
    if (type === 'success') {
        oscillator.frequency.value = 800;
        oscillator.type = 'sine';
    } else if (type === 'error') {
        oscillator.frequency.value = 400;
        oscillator.type = 'square';
    } else {
        oscillator.frequency.value = 600;
        oscillator.type = 'sine';
    }
    
    gainNode.gain.value = 0.1;
    oscillator.start();
    oscillator.stop(audioContext.currentTime + 0.1);
}

// ===== 浏览器原生通知 =====
let browserNotificationsEnabled = localStorage.getItem('browserNotificationsEnabled') === 'true';

function initBrowserNotification() {
    const browserNotificationCheckbox = document.getElementById('browserNotification');
    
    if (browserNotificationCheckbox) {
        browserNotificationCheckbox.checked = browserNotificationsEnabled;
        
        browserNotificationCheckbox.addEventListener('change', (e) => {
            browserNotificationsEnabled = e.target.checked;
            localStorage.setItem('browserNotificationsEnabled', browserNotificationsEnabled);
            
            if (browserNotificationsEnabled && Notification.permission === 'default') {
                requestNotificationPermission();
            }
        });
        
        // 初始请求权限
        if (browserNotificationsEnabled && Notification.permission === 'default') {
            requestNotificationPermission();
        }
    }
}

function requestNotificationPermission() {
    Notification.requestPermission().then(permission => {
        if (permission === 'granted') {
            showNotification('通知已启用', '您将收到浏览器通知');
        }
    });
}

function sendBrowserNotification(title, body) {
    if (!browserNotificationsEnabled || Notification.permission !== 'granted') return;
    
    if (document.visibilityState === 'visible') return; // 如果页面可见，不发送通知
    
    const notification = new Notification(title, {
        body: body,
        icon: 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">🔔</text></svg>'
    });
    
    notification.onclick = () => {
        window.focus();
        notification.close();
    };
    
    setTimeout(() => notification.close(), 5000);
}

// ===== 翻译功能 =====
const translationCache = new Map();

async function translateMessage(text, targetLang = 'zh') {
    // 检查缓存
    const cacheKey = `${text}_${targetLang}`;
    if (translationCache.has(cacheKey)) {
        return translationCache.get(cacheKey);
    }
    
    try {
        // 使用免费的翻译API (LibreTranslate 公共实例)
        const response = await fetch('https://libretranslate.com/translate', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                q: text,
                source: 'auto',
                target: targetLang === 'zh' ? 'zh' : 'en',
                format: 'text'
            })
        });
        
        if (!response.ok) {
            throw new Error('翻译服务暂时不可用');
        }
        
        const data = await response.json();
        const translatedText = data.translatedText;
        
        // 缓存结果
        translationCache.set(cacheKey, translatedText);
        
        return translatedText;
    } catch (error) {
        console.error('Translation error:', error);
        throw error;
    }
}

function initTranslateFeature() {
    // 翻译功能将通过事件委托处理
    document.addEventListener('click', async (e) => {
        const translateBtn = e.target.closest('.translate-btn');
        if (!translateBtn) return;
        
        const messageEl = translateBtn.closest('.message');
        if (!messageEl) return;
        
        const contentEl = messageEl.querySelector('.message-content');
        if (!contentEl) return;
        
        // 检查是否已有翻译
        const existingTranslation = messageEl.querySelector('.message-translation');
        if (existingTranslation) {
            existingTranslation.remove();
            return;
        }
        
        // 显示加载状态
        translateBtn.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="4"/><polyline points="12 7 12 12 15 15"/></svg>';
        translateBtn.disabled = true;
        
        try {
            const originalText = contentEl.textContent;
            const translated = await translateMessage(originalText);
            
            // 创建翻译元素
            const translationEl = document.createElement('div');
            translationEl.className = 'message-translation';
            translationEl.textContent = translated;
            
            contentEl.parentNode.insertBefore(translationEl, contentEl.nextSibling);
            translateBtn.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="12" rx="10" ry="8"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 4c2 2.5 3 5 3 8s-1 5.5-3 8c-2-2.5-3-5-3-8s1-5.5 3-8z"/></svg>';

        } catch (error) {
            showNotification('翻译失败', error.message);
            translateBtn.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="12" rx="10" ry="8"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 4c2 2.5 3 5 3 8s-1 5.5-3 8c-2-2.5-3-5-3-8s1-5.5 3-8z"/></svg>';
        } finally {
            translateBtn.disabled = false;
        }
    });
    
    // 在消息菜单中添加翻译选项
    document.getElementById('ctxCopy')?.addEventListener('click', () => {
        const currentMessage = document.querySelector('.context-menu.active')?.closest('.message');
        if (!currentMessage) return;
        
        const content = currentMessage.querySelector('.message-content')?.textContent;
        if (content) {
            navigator.clipboard.writeText(content).then(() => {
                showNotification('成功', '消息内容已复制');
            });
        }
    });
}

// ===== 设置面板 =====
function initSettingsPanel() {
    const settingsBtn = document.getElementById('settingsBtn');
    const settingsPanel = document.getElementById('settingsPanel');
    const closeSettings = document.getElementById('closeSettings');
    
    settingsBtn?.addEventListener('click', () => {
        settingsPanel?.classList.add('active');
    });
    
    closeSettings?.addEventListener('click', () => {
        settingsPanel?.classList.remove('active');
    });
    
    // 点击外部关闭
    document.addEventListener('click', (e) => {
        if (settingsPanel?.classList.contains('active')) {
            if (!settingsPanel.contains(e.target) && e.target !== settingsBtn) {
                settingsPanel.classList.remove('active');
            }
        }
    });
    
    // 声音通知开关
    const soundNotificationCheckbox = document.getElementById('soundNotification');
    if (soundNotificationCheckbox) {
        soundNotificationCheckbox.checked = soundEnabled;
        soundNotificationCheckbox.addEventListener('change', (e) => {
            soundEnabled = e.target.checked;
            localStorage.setItem('soundEnabled', soundEnabled);
            if (soundEnabled) {
                playNotificationSound('success');
            }
        });
    }
}

// ===== 命令模板 =====
function initCommandTemplates() {
    // 默认模板
    const defaultTemplates = [
        { id: '1', name: '系统信息', command: '查看系统基本信息', icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>' },
        { id: '2', name: '内存使用', command: '查看内存使用情况', icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="9" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1" fill="currentColor" stroke="none"/><line x1="8" y1="15" x2="16" y2="15"/></svg>' },
        { id: '3', name: '磁盘空间', command: '查看磁盘空间使用情况', icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3"/><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/></svg>' },
        { id: '4', name: '网络状态', command: '查看网络连接状态', icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><ellipse cx="12" cy="12" rx="10" ry="8"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 4c2 2.5 3 5 3 8s-1 5.5-3 8c-2-2.5-3-5-3-8s1-5.5 3-8z"/></svg>' },
        { id: '5', name: '进程管理', command: '查看当前运行的进程', icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/></svg>' },
        { id: '6', name: '日志查看', command: '查看最近的系统日志', icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>' },
        { id: '7', name: '服务状态', command: '查看系统服务状态', icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>' },
        { id: '8', name: '资源监控', command: '查看CPU、内存、磁盘使用情况', icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 6 13.5 15.5 8.5 10.5 1 18"/><polyline points="17 6 23 6 23 12"/></svg>' }
    ];
    
    // 加载模板
    function getTemplates() {
        const saved = localStorage.getItem('commandTemplates');
        if (saved) {
            return JSON.parse(saved);
        }
        localStorage.setItem('commandTemplates', JSON.stringify(defaultTemplates));
        return defaultTemplates;
    }
    
    // 保存模板
    function saveTemplates(templates) {
        localStorage.setItem('commandTemplates', JSON.stringify(templates));
    }
    
    // 渲染模板列表
    function renderTemplates() {
        const container = document.getElementById('commandTemplates');
        if (!container) return;
        
        const templates = getTemplates();
        container.innerHTML = templates.map(t => `
            <div class="template-item" data-id="${t.id}">
                <div class="template-item-info">
                    <div class="template-item-name">${escapeHtml(t.name)}</div>
                    <div class="template-item-command">${escapeHtml(t.command)}</div>
                </div>
                <div class="template-item-actions">
                    <button class="use-btn" data-command="${escapeHtml(t.command)}">使用</button>
                    <button class="delete-btn" data-id="${t.id}">删除</button>
                </div>
            </div>
        `).join('');
        
        // 使用模板
        container.querySelectorAll('.use-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const command = btn.dataset.command;
                const chatInput = document.getElementById('chatInput');
                if (chatInput) {
                    chatInput.value = command;
                    chatInput.focus();
                    autoResizeTextarea(chatInput);
                }
            });
        });
        
        // 删除模板
        container.querySelectorAll('.delete-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const id = btn.dataset.id;
                const templates = getTemplates().filter(t => t.id !== id);
                saveTemplates(templates);
                renderTemplates();
                showNotification('成功', '模板已删除');
            });
        });
    }
    
    // 添加新模板
    document.getElementById('addTemplate')?.addEventListener('click', () => {
        const nameInput = document.getElementById('newTemplateName');
        const commandInput = document.getElementById('newTemplateCommand');
        
        const name = nameInput?.value.trim();
        const command = commandInput?.value.trim();
        
        if (!name || !command) {
            showNotification('错误', '请填写模板名称和命令');
            return;
        }
        
        const templates = getTemplates();
        templates.push({
            id: Date.now().toString(),
            name: name,
            command: command,
            icon: '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>'
        });
        
        saveTemplates(templates);
        renderTemplates();
        
        nameInput.value = '';
        commandInput.value = '';
        
        showNotification('成功', '模板已添加');
    });
    
    renderTemplates();
}

// ===== 模板选择器 =====
function initTemplateSelector() {
    const templateBtn = document.getElementById('templateSelectBtn');
    const templateSelector = document.getElementById('templateSelector');
    const closeBtn = document.getElementById('closeTemplateSelector');
    const templateList = document.getElementById('templateList');
    
    function getTemplates() {
        return JSON.parse(localStorage.getItem('commandTemplates') || '[]');
    }
    
    function renderTemplateList() {
        if (!templateList) return;
        
        const templates = getTemplates();
        if (templates.length === 0) {
            templateList.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-muted);">暂无模板，请在设置中添加</div>';
            return;
        }
        
        templateList.innerHTML = templates.map(t => `
            <div class="template-list-item" data-command="${escapeHtml(t.command)}">
                <div class="template-icon">${t.icon || '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>'}</div>
                <div class="template-info">
                    <div class="template-name">${escapeHtml(t.name)}</div>
                    <div class="template-command-preview">${escapeHtml(t.command)}</div>
                </div>
            </div>
        `).join('');
        
        templateList.querySelectorAll('.template-list-item').forEach(item => {
            item.addEventListener('click', () => {
                const command = item.dataset.command;
                const chatInput = document.getElementById('chatInput');
                if (chatInput) {
                    chatInput.value = command;
                    chatInput.focus();
                    autoResizeTextarea(chatInput);
                }
                templateSelector?.classList.remove('active');
            });
        });
    }
    
    templateBtn?.addEventListener('click', () => {
        renderTemplateList();
        templateSelector?.classList.add('active');
    });
    
    closeBtn?.addEventListener('click', () => {
        templateSelector?.classList.remove('active');
    });
    
    // 点击外部关闭
    document.addEventListener('click', (e) => {
        if (templateSelector?.classList.contains('active')) {
            if (!templateSelector.contains(e.target) && e.target !== templateBtn) {
                templateSelector.classList.remove('active');
            }
        }
    });
}

// ===== 自动调整文本框大小 =====
function autoResizeTextarea(textarea) {
    textarea.style.height = 'auto';
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

// ===== 无障碍和键盘导航 =====
function initAccessibility() {
    // 焦点管理
    initFocusManagement();
    
    // 键盘导航增强
    initKeyboardNavigation();
    
    // ARIA实时区域更新
    initLiveRegion();
}

function initFocusManagement() {
    // 模态框焦点陷阱
    const focusableSelectors = [
        'button:not([disabled])',
        'input:not([disabled])',
        'textarea:not([disabled])',
        'select:not([disabled])',
        '[tabindex]:not([tabindex="-1"])',
        'a[href]'
    ].join(',');
    
    // 为模态框添加焦点陷阱
    document.querySelectorAll('.modal.active, .settings-panel.active, .search-modal.active, .template-selector.active').forEach(modal => {
        trapFocus(modal);
    });
}

function trapFocus(element) {
    const focusableElements = element.querySelectorAll(focusableSelectors);
    const firstFocusable = focusableElements[0];
    const lastFocusable = focusableElements[focusableElements.length - 1];
    
    element.addEventListener('keydown', (e) => {
        if (e.key !== 'Tab') return;
        
        if (e.shiftKey) {
            if (document.activeElement === firstFocusable) {
                e.preventDefault();
                lastFocusable.focus();
            }
        } else {
            if (document.activeElement === lastFocusable) {
                e.preventDefault();
                firstFocusable.focus();
            }
        }
    });
}

function initKeyboardNavigation() {
    // RoveTab模式用于导航按钮
    const tabButtons = document.querySelectorAll('[role="tab"]');
    
    tabButtons.forEach((button, index) => {
        button.addEventListener('keydown', (e) => {
            let targetIndex;
            
            switch (e.key) {
                case 'ArrowRight':
                case 'ArrowDown':
                    e.preventDefault();
                    targetIndex = (index + 1) % tabButtons.length;
                    tabButtons[targetIndex].focus();
                    break;
                case 'ArrowLeft':
                case 'ArrowUp':
                    e.preventDefault();
                    targetIndex = (index - 1 + tabButtons.length) % tabButtons.length;
                    tabButtons[targetIndex].focus();
                    break;
                case 'Home':
                    e.preventDefault();
                    tabButtons[0].focus();
                    break;
                case 'End':
                    e.preventDefault();
                    tabButtons[tabButtons.length - 1].focus();
                    break;
            }
        });
    });
    
    // ESC关闭模态框并返回焦点
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const activeModal = document.querySelector('.modal.active, .settings-panel.active, .search-modal.active, .template-selector.active');
            if (activeModal) {
                const trigger = activeModal.dataset.focusTrigger;
                activeModal.classList.remove('active');
                
                // 返回焦点到触发元素
                if (trigger) {
                    const triggerEl = document.querySelector(trigger);
                    if (triggerEl) triggerEl.focus();
                }
            }
        }
    });
}

function initLiveRegion() {
    // 创建实时区域用于屏幕阅读器通知
    let liveRegion = document.getElementById('liveRegion');
    if (!liveRegion) {
        liveRegion = document.createElement('div');
        liveRegion.id = 'liveRegion';
        liveRegion.setAttribute('aria-live', 'polite');
        liveRegion.setAttribute('aria-atomic', 'true');
        liveRegion.className = 'sr-only';
        document.body.appendChild(liveRegion);
    }
    
    // 覆盖showNotification以发送ARIA通知
    const originalShowNotification = window.showNotification;
    window.showNotification = function(title, message, type) {
        // 更新实时区域
        liveRegion.textContent = `${title}: ${message}`;
        
        // 清除内容以允许重复通知
        setTimeout(() => {
            liveRegion.textContent = '';
        }, 1000);
        
        // 调用原始函数
        if (originalShowNotification) {
            return originalShowNotification.apply(this, arguments);
        }
    };
}

// 模态框打开时保存触发元素焦点
function setupModalFocusTrap(modal, triggerSelector) {
    modal.addEventListener('transitionend', () => {
        if (modal.classList.contains('active')) {
            const firstFocusable = modal.querySelector(focusableSelectors);
            if (firstFocusable) {
                firstFocusable.focus();
            }
            modal.dataset.focusTrigger = triggerSelector;
        }
    });
}

// 更新导航ARIA状态
function updateNavAriaState() {
    const navBtns = document.querySelectorAll('.nav-btn');
    navBtns.forEach(btn => {
        const isActive = btn.classList.contains('active');
        btn.setAttribute('aria-selected', isActive);
    });
}

// 在视图切换时更新ARIA
const originalSwitchView = window.switchView;
if (originalSwitchView) {
    window.switchView = function(viewName) {
        const result = originalSwitchView.apply(this, arguments);
        updateNavAriaState();
        return result;
    };
}

// ===== 移动端侧边栏抽屉功能 =====
function initMobileSidebar() {
    const mobileMenuBtn = document.getElementById('mobileMenuBtn');
    const sidebar = document.getElementById('sidebar');
    const sidebarOverlay = document.getElementById('sidebarOverlay');
    const sidebarCloseBtn = document.getElementById('sidebarCloseBtn');
    
    if (!mobileMenuBtn || !sidebar) return;
    
    // 打开侧边栏
    function openSidebar() {
        sidebar.classList.add('open');
        sidebarOverlay?.classList.add('active');
        mobileMenuBtn.classList.add('active');
        mobileMenuBtn.setAttribute('aria-expanded', 'true');
        document.body.style.overflow = 'hidden';
        
        // 焦点管理
        const firstFocusable = sidebar.querySelector('button, a, input, [tabindex]');
        if (firstFocusable) firstFocusable.focus();
    }
    
    // 关闭侧边栏
    function closeSidebar() {
        sidebar.classList.remove('open');
        sidebarOverlay?.classList.remove('active');
        mobileMenuBtn.classList.remove('active');
        mobileMenuBtn.setAttribute('aria-expanded', 'false');
        document.body.style.overflow = '';
        mobileMenuBtn.focus();
    }
    
    // 事件绑定
    mobileMenuBtn.addEventListener('click', () => {
        if (sidebar.classList.contains('open')) {
            closeSidebar();
        } else {
            openSidebar();
        }
    });
    
    sidebarCloseBtn?.addEventListener('click', closeSidebar);
    sidebarOverlay?.addEventListener('click', closeSidebar);
    
    // 点击导航项后关闭侧边栏
    sidebar.querySelectorAll('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                setTimeout(closeSidebar, 100);
            }
        });
    });
    
    // ESC键关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && sidebar.classList.contains('open')) {
            closeSidebar();
        }
    });
}

// ===== 触摸手势支持 =====
function initTouchGestures() {
    // 检测是否为触摸设备
    if (!('ontouchstart' in window) && navigator.maxTouchPoints === 0) return;
    
    let touchStartX = 0;
    let touchStartY = 0;
    let touchEndX = 0;
    let touchEndY = 0;
    let longPressTimer = null;
    
    const sidebar = document.getElementById('sidebar');
    
    // 边缘滑动打开侧边栏
    document.addEventListener('touchstart', (e) => {
        touchStartX = e.changedTouches[0].screenX;
        touchStartY = e.changedTouches[0].screenY;
        
        // 长按检测用于会话项
        const sessionItem = e.target.closest('.session-item');
        if (sessionItem) {
            longPressTimer = setTimeout(() => {
                showLongPressMenu(e, sessionItem);
            }, 500);
        }
    }, { passive: true });
    
    document.addEventListener('touchmove', (e) => {
        touchEndX = e.changedTouches[0].screenX;
        touchEndY = e.changedTouches[0].screenY;
        
        // 取消长按如果移动了
        if (Math.abs(touchEndX - touchStartX) > 10 || Math.abs(touchEndY - touchStartY) > 10) {
            clearTimeout(longPressTimer);
        }
        
        // 边缘滑动打开侧边栏
        if (touchStartX < 30 && touchEndX - touchStartX > 80) {
            const sidebar = document.getElementById('sidebar');
            if (!sidebar.classList.contains('open')) {
                document.getElementById('mobileMenuBtn')?.click();
            }
        }
    }, { passive: true });
    
    document.addEventListener('touchend', () => {
        clearTimeout(longPressTimer);
    }, { passive: true });
    
    // 滑动手势方向检测
    const chatMessages = document.getElementById('chatMessages');
    if (chatMessages) {
        let startX = 0;
        let startY = 0;
        let startTime = 0;
        
        chatMessages.addEventListener('touchstart', (e) => {
            startX = e.touches[0].clientX;
            startY = e.touches[0].clientY;
            startTime = Date.now();
        }, { passive: true });
        
        chatMessages.addEventListener('touchend', (e) => {
            const endX = e.changedTouches[0].clientX;
            const endY = e.changedTouches[0].clientY;
            const deltaX = endX - startX;
            const deltaY = endY - startY;
            const deltaTime = Date.now() - startTime;
            
            // 快速滑动检测
            if (deltaTime < 300 && Math.abs(deltaX) > 50 && Math.abs(deltaX) > Math.abs(deltaY)) {
                // 左滑显示操作按钮
                if (deltaX < 0 && window.innerWidth <= 768) {
                    const message = e.target.closest('.message');
                    if (message) {
                        showMessageActions(message);
                    }
                }
            }
        }, { passive: true });
    }
}

// 长按菜单
function showLongPressMenu(e, sessionItem) {
    // 移除已存在的菜单
    document.querySelectorAll('.long-press-menu').forEach(m => m.remove());
    
    const menu = document.createElement('div');
    menu.className = 'long-press-menu active';
    menu.innerHTML = `
        <div class="menu-item" data-action="rename"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg> 重命名</div>
        <div class="menu-item" data-action="delete"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg> 删除</div>
        <div class="menu-item" data-action="export"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg> 导出</div>
    `;
    
    // 定位菜单
    const touch = e.touches[0];
    const menuX = Math.min(touch.clientX, window.innerWidth - 180);
    const menuY = Math.min(touch.clientY, window.innerHeight - 150);
    
    menu.style.left = menuX + 'px';
    menu.style.top = menuY + 'px';
    
    document.body.appendChild(menu);
    
    // 菜单项点击
    menu.querySelectorAll('.menu-item').forEach(item => {
        item.addEventListener('click', () => {
            const action = item.dataset.action;
            handleLongPressAction(action, sessionItem);
            menu.remove();
        });
    });
    
    // 点击其他地方关闭
    setTimeout(() => {
        document.addEventListener('click', function handler(ev) {
            if (!menu.contains(ev.target)) {
                menu.remove();
                document.removeEventListener('click', handler);
            }
        });
    }, 100);
}

function handleLongPressAction(action, sessionItem) {
    const sessionId = sessionItem.dataset.sessionId;
    
    switch (action) {
        case 'rename':
            const newTitle = prompt('请输入新名称:', sessionItem.querySelector('.session-title')?.textContent);
            if (newTitle) {
                renameSession(sessionId, newTitle);
            }
            break;
        case 'delete':
            if (confirm('确定删除这个会话?')) {
                deleteSession(sessionId);
            }
            break;
        case 'export':
            exportSingleSession(sessionId);
            break;
    }
}

// 显示消息操作按钮
function showMessageActions(messageEl) {
    // 创建操作按钮
    const actions = document.createElement('div');
    actions.className = 'message-actions';
    actions.innerHTML = `
        <button class="action-btn" data-action="copy"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg></button>
        <button class="action-btn" data-action="quote"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/></svg></button>
        <button class="action-btn" data-action="delete"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6m3 0V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg></button>
    `;
    
    // 样式
    actions.style.cssText = `
        position: absolute;
        right: 10px;
        top: 10px;
        display: flex;
        gap: 8px;
        background: var(--bg-card);
        padding: 8px;
        border-radius: 8px;
        box-shadow: 0 2px 10px rgba(0,0,0,0.2);
        z-index: 100;
        animation: fadeIn 0.2s;
    `;
    
    messageEl.style.position = 'relative';
    messageEl.appendChild(actions);
    
    // 点击操作
    actions.querySelectorAll('.action-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            const action = btn.dataset.action;
            
            switch (action) {
                case 'copy':
                    const content = messageEl.querySelector('.message-content')?.textContent;
                    if (content) {
                        navigator.clipboard.writeText(content);
                        showNotification('成功', '已复制到剪贴板');
                    }
                    break;
                case 'quote':
                    const quoteContent = messageEl.querySelector('.message-content')?.textContent;
                    if (quoteContent) {
                        const chatInput = document.getElementById('chatInput');
                        if (chatInput) {
                            chatInput.value = `> ${quoteContent}\n\n`;
                            chatInput.focus();
                        }
                    }
                    break;
                case 'delete':
                    messageEl.remove();
                    showNotification('成功', '消息已删除');
                    break;
            }
            
            actions.remove();
        });
    });
    
    // 3秒后自动隐藏
    setTimeout(() => {
        if (actions.parentNode) {
            actions.remove();
        }
    }, 3000);
}

// 下拉刷新
function initPullToRefresh() {
    const mainContent = document.querySelector('.main-content');
    if (!mainContent) return;
    
    let startY = 0;
    let currentY = 0;
    let isPulling = false;
    
    // 只在移动端启用
    if (window.innerWidth > 768) return;
    
    // 创建刷新指示器
    const pullIndicator = document.createElement('div');
    pullIndicator.className = 'pull-to-refresh';
    pullIndicator.innerHTML = '<div class="spinner"></div>';
    document.body.appendChild(pullIndicator);
    
    mainContent.addEventListener('touchstart', (e) => {
        if (mainContent.scrollTop === 0) {
            startY = e.touches[0].clientY;
            isPulling = true;
        }
    }, { passive: true });
    
    mainContent.addEventListener('touchmove', (e) => {
        if (!isPulling) return;
        
        currentY = e.touches[0].clientY;
        const pullDistance = currentY - startY;
        
        if (pullDistance > 0 && pullDistance < 100) {
            pullIndicator.style.height = pullDistance + 'px';
            pullIndicator.classList.add('active');
        }
    }, { passive: true });
    
    mainContent.addEventListener('touchend', () => {
        if (!isPulling) return;
        
        const pullDistance = currentY - startY;
        
        if (pullDistance > 60) {
            // 执行刷新
            pullIndicator.style.height = '60px';
            refreshData();
        } else {
            pullIndicator.classList.remove('active');
            pullIndicator.style.height = '0';
        }
        
        isPulling = false;
        startY = 0;
        currentY = 0;
    }, { passive: true });
}

function refreshData() {
    // 触发数据刷新
    if (typeof loadSystemInfo === 'function') {
        loadSystemInfo();
    }
    if (typeof loadAuditLogs === 'function') {
        loadAuditLogs();
    }
    
    setTimeout(() => {
        document.querySelector('.pull-to-refresh')?.classList.remove('active');
        showNotification('刷新成功', '数据已更新');
    }, 1000);
}

// 导出单个会话
function exportSingleSession(sessionId) {
    const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
    const session = sessions.find(s => s.id === sessionId);
    
    if (!session) {
        showNotification('错误', '会话不存在');
        return;
    }
    
    const exportData = {
        version: '1.0',
        exportTime: new Date().toISOString(),
        sessions: [session]
    };
    
    const blob = new Blob([JSON.stringify(exportData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `session-${session.title.slice(0, 20)}-${new Date().toISOString().slice(0,10)}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    
    showNotification('成功', '会话已导出');
}

// 重命名会话
function renameSession(sessionId, newTitle) {
    const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
    const index = sessions.findIndex(s => s.id === sessionId);
    
    if (index !== -1) {
        sessions[index].title = newTitle;
        sessions[index].timestamp = Date.now();
        StorageManager.setSessions(sessions);
        loadSessions();
        showNotification('成功', '会话已重命名');
    }
}

// 删除会话
function deleteSession(sessionId) {
    const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
    const filtered = sessions.filter(s => s.id !== sessionId);
    StorageManager.setSessions(filtered);
    
    // 如果删除的是当前会话，切换到新会话
    if (currentSessionId === sessionId) {
        if (filtered.length > 0) {
            loadSession(filtered[0].id);
        } else {
            createNewSession();
        }
    }
    
    loadSessions();
    showNotification('成功', '会话已删除');
}

// 初始化移动端功能
function initMobileFeatures() {
    initMobileSidebar();
    initTouchGestures();
    initPullToRefresh();
}

// ===== 震动反馈 =====
function vibrateDevice(pattern = [50]) {
    if ('vibrate' in navigator) {
        navigator.vibrate(pattern);
    }
}

// ===== 表情选择器 =====
function initEmojiPicker() {
    const emojiBtn = document.getElementById('emojiBtn');
    const emojiPicker = document.getElementById('emojiPicker');
    const emojiList = document.getElementById('emojiList');
    const emojiSearch = document.getElementById('emojiSearch');
    
    if (!emojiBtn || !emojiPicker) return;
    
    // 表情数据
    const emojis = {
        recent: [],
        smile: ['😀', '😃', '😄', '😁', '😆', '😅', '🤣', '😂', '🙂', '😊', '😇', '🥰', '😍', '🤩', '😘', '😗', '😚', '😋', '😛', '😜', '🤪', '😝', '🤑', '🤗', '🤭', '🤫', '🤔', '🤐', '🤨', '😐', '😑', '😶', '😏', '😒', '🙄', '😬', '🤥', '😌', '😔', '😪', '🤤', '😴', '😷', '🤒', '🤕', '🤢', '🤮', '🤧', '🥵', '🥶', '🥴', '😵', '🤯', '🤠', '🥳', '😎', '🤓', '🧐', '😕', '😟', '🙁', '😮', '😯', '😲', '😳', '🥺', '😦', '😧', '😨', '😰', '😥', '😢', '😭', '😱', '😖', '😣', '😞', '😓', '😩', '😫', '🥱', '😤', '😡', '😠', '🤬', '😈', '👿', '💀', '☠️', '💩', '🤡', '👹', '👺', '👻', '👽', '👾', '🤖'],
        gesture: ['👍', '👎', '👊', '✊', '🤛', '🤜', '🤝', '👏', '🙌', '👐', '🤲', '🤞', '✌️', '🤟', '🤘', '👌', '🤏', '👈', '👉', '👆', '👇', '☝️', '✋', '🤚', '🖐️', '🖖', '👋', '🤙', '💪', '🦾', '🦿', '🦵', '🦶', '👂', '🦻', '👃', '🧠', '🫀', '🫁', '🦷', '🦴', '👀', '👁️', '👅', '👄', '👶', '🧒', '👦', '👧', '🧑', '👱', '👨', '🧔', '👩', '🧓', '👴', '👵', '🙍', '🙎', '🙅', '🙆', '💁', '🙋', '🧏', '🙇', '🤦', '🤷', '👮', '🕵️', '💂', '🥷', '👷', '🤴', '👸', '👳', '👲', '🧕', '🤵', '👰', '🤰', '🤱', '👼', '🎒', '🎓', '👑', '📿', '💄', '💍', '💎', '🐵', '🐒', '🦍', '🦧', '🐶', '🐕', '🦮', '🐩', '🐺', '🦊', '🦁', '🐯', '🐱', '🐶', '🦁', '🐾', '🐉', '🐲'],
        animal: ['🐶', '🐱', '🐭', '🐹', '🐰', '🦊', '🐻', '🐼', '🐻‍❄️', '🐨', '🐯', '🦁', '🐮', '🐷', '🐽', '🐸', '🐵', '🙈', '🙉', '🙊', '🐒', '🐔', '🐧', '🐦', '🐤', '🐣', '🐥', '🦆', '🦅', '🦉', '🦇', '🐺', '🐗', '🐴', '🦄', '🐝', '🪱', '🐛', '🦋', '🐌', '🐞', '🐜', '🪰', '🪲', '🪳', '🦟', '🦗', '🕷️', '🕸️', '🦂', '🐢', '🐍', '🦎', '🦖', '🦕', '🐙', '🦑', '🦐', '🦞', '🦀', '🐡', '🐠', '🐟', '🐬', '🐳', '🐋', '🦈', '🐊', '🐅', '🐆', '🦓', '🦍', '🦧', '🦣', '🐘', '🦛', '🦏', '🐪', '🐫', '🦒', '🦘', '🦬', '🐃', '🐂', '🐄', '🐎', '🐖', '🐏', '🐑', '🦙', '🐐', '🦌', '🐕', '🐩', '🦮', '🐈', '🐈‍⬛', '🪶', '🐓', '🦃', '🦤', '🦚', '🦜', '🦢', '🦩', '🕊️', '🐇', '🦝', '🦨', '🦡', '🦫', '🦦', '🦥', '🐁', '🐀', '🐿️', '🦔'],
        food: ['🍎', '🍐', '🍊', '🍋', '🍌', '🍉', '🍇', '🍓', '🫐', '🍈', '🍒', '🍑', '🥭', '🍍', '🥥', '🥝', '🍅', '🍆', '🥑', '🥦', '🥬', '🥒', '🌶️', '🫑', '🌽', '🥕', '🫒', '🧄', '🧅', '🥔', '🍠', '🥐', '🥯', '🍞', '🥖', '🥨', '🧀', '🥚', '🍳', '🧈', '🥞', '🧇', '🥓', '🥩', '🍗', '🍖', '🦴', '🌭', '🍔', '🍟', '🍕', '🫓', '🥪', '🥙', '🧆', '🌮', '🌯', '🫔', '🥗', '🥘', '🫕', '🥫', '🍝', '🍜', '🍲', '🍛', '🍣', '🍱', '🥟', '🦪', '🍤', '🍙', '🍚', '🍘', '🍥', '🥠', '🥮', '🍢', '🍡', '🍧', '🍨', '🍦', '🥧', '🧁', '🍰', '🎂', '🍮', '🍭', '🍬', '🍫', '🍿', '🍩', '🍪', '🌰', '🥜', '🍯', '🥛', '🍼', '☕', '🫖', '🍵', '🧃', '🥤', '🧋', '🍶', '🍺', '🍻', '🥂', '🍷', '🥃', '🍸', '🍹', '🧉', '🍾', '🧊', '🥄', '🍴', '🍽️', '🥣', '🥡', '🥢', '🧂'],
        activity: ['⚽', '🏀', '🏈', '⚾', '🥎', '🎾', '🏐', '🏉', '🥏', '🎱', '🪀', '🏓', '🏸', '🏒', '🏑', '🥍', '🏏', '🪃', '🥅', '⛳', '🪁', '🏹', '🎣', '🤿', '🥊', '🥋', '🎽', '🛹', '🛼', '🛷', '⛸️', '🥌', '🎿', '⛷️', '🏂', '🪂', '🏋️', '🤼', '🤸', '⛹️', '🤺', '🤾', '🏌️', '🏇', '🧘', '🏄', '🏊', '🤽', '🚣', '🧗', '🚵', '🚴', '🏆', '🥇', '🥈', '🥉', '🏅', '🎖️', '🏵️', '🎗️', '🎫', '🎟️', '🎪', '🤹', '🎭', '🩰', '🎨', '🎬', '🎤', '🎧', '🎼', '🎹', '🥁', '🪘', '🎷', '🎺', '🪗', '🎸', '🪕', '🎻', '🎲', '♟️', '🎯', '🎳', '🎮', '🕹️', '🎰', '🧩', '🪅', '🪆', '🪄', '🎁', '🎀', '🎊', '🎉', '🎈', '�商铺', '🎎', '🏮', '🪭', '🎐', '🧧', '✉️', '📩', '📨', '📧', '💌', '📥', '📤', '📦', '🏷️', '🪧', '📪', '📫', '📬', '📭', '📮', '📯', '📜', '📃', '📄', '📑', '🧾', '📊', '📈', '📉', '🗒️', '🗓️', '📆', '📅', '🗑️', '📇', '🗃️', '🗳️', '🗄️', '📋', '📁', '📂', '🗂️', '🗞️', '📰', '📓', '📔', '📒', '📕', '📗', '📘', '📙', '📚', '📖', '🔖', '🧷', '🔗', '📎', '🖇️', '📐', '📏', '🧮', '📌', '📍', '✂️', '🔍', '🔎', '🔏', '🔒', '🔓', '🔐', '🔑', '🗝️', '🔨', '🪓', '⛏️', '⚒️', '🛠️', '🗡️', '⚔️', '🔫', '🧨', '💣', '🧳', '⌛', '⏳', '⌚', '⏰', '⏱️', '⏲️', '🕰️', '🧭', '🪧', '📡', '🔋', '🔌', '💡', '🔦', '🕯️', '🪔', '🧯', '🛢️', '💸', '💵', '💴', '💶', '💷', '🪙', '💰', '💳', '💎', '⚖️', '🪜', '🧰', '🪝', '🔬', '🔭', '📡', '💊', '💉', '🩸', '🩹', '🩺', '🩻'],
        symbols: ['❤️', '🧡', '💛', '💚', '💙', '💜', '🖤', '🤍', '🤎', '💔', '❣️', '💕', '💞', '💓', '💗', '💖', '💘', '💝', '💟', '☮️', '✝️', '☪️', '🕉️', '☸️', '✡️', '🔯', '🕎', '☯️', '☦️', '🛐', '⛎', '♈', '♉', '♊', '♋', '♌', '♍', '♎', '♏', '♐', '♑', '♒', '♓', '🆔', '⚛️', '🉑', '☢️', '☣️', '📴', '📳', '🈶', '🈚', '🈸', '🈺', '🈷️', '✴️', '🆚', '💮', '🉐', '㊙️', '㊗️', '🈴', '🈵', '🈹', '🈲', '🅰️', '🅱️', '🆎', '🆑', '🅾️', '🆘', '⛔', '📵', '🚫', '❗', '❕', '❓', '❔', '‼️', '⁉️', '🔅', '🔆', '〽️', '⚠️', '🚸', '🔱', '⚜️', '🔰', '♻️', '✅', '🈯', '💹', '❇️', '✳️', '❎', '🌐', '💠', 'Ⓜ️', '🌀', '💤', '🏧', '🚻', '🚹', '🚺', '🚼', '⚧️', '🅾️', '🛗', '♿', '🈳', '🈂️', '🛂', '🛃', '🛄', '🛅', '🚾', '🚰', '🚭', '🚯', '🚱', '🚳', '🚲', '⚠️', '🔞', '🔃', '🔄', '🔙', '🔛', '🔝', '🔜', '✔️', '☑️', '🔘', '🔴', '🟠', '🟡', '🟢', '🔵', '🟣', '⚫', '⚪', '🟤', '🔺', '🔻', '🔸', '🔹', '🔶', '🔷', '🔳', '🔲', '▪️', '▫️', '◾', '◽', '◼️', '◻️', '🟥', '🟧', '🟨', '🟩', '🟦', '🟪', '⬛', '⬜', '🟫', '🔈', '🔇', '🔉', '🔊', '🔔', '🔕', '📣', '📢', '💬', '💭', '🗯️', '♠️', '♣️', '♥️', '♦️', '🃏', '🎴', '🀄', '🕐', '🕑', '🕒', '🕓', '🕔', '🕕', '🕖', '🕗', '🕘', '🕙', '🕚', '🕛', '🕜', '🕝', '🕞', '🕟', '🕠', '🕡', '🕢', '🕣', '🕤', '🕥', '🕦', '🕧']
    };
    
    let currentCategory = 'recent';
    
    // 渲染表情
    function renderEmojis(category = 'recent', searchQuery = '') {
        let emojiArray = emojis[category] || [];
        
        // 搜索过滤
        if (searchQuery) {
            // 简单搜索，直接显示匹配的表情
            emojiArray = Object.values(emojis).flat().filter(e => e.includes(searchQuery));
        }
        
        if (category === 'recent') {
            const recentEmojis = JSON.parse(localStorage.getItem('recentEmojis') || '[]');
            emojiArray = recentEmojis;
        }
        
        emojiList.innerHTML = emojiArray.map(emoji => 
            `<button class="emoji-item" data-emoji="${emoji}">${emoji}</button>`
        ).join('');
        
        if (emojiArray.length === 0) {
            emojiList.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-muted);">暂无表情</div>';
        }
    }
    
    // 切换分类
    document.querySelectorAll('.emoji-category-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.emoji-category-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            currentCategory = btn.dataset.category;
            renderEmojis(currentCategory);
        });
    });
    
    // 搜索
    emojiSearch?.addEventListener('input', (e) => {
        renderEmojis('search', e.target.value);
    });
    
    // 选择表情
    emojiList.addEventListener('click', (e) => {
        const emojiItem = e.target.closest('.emoji-item');
        if (emojiItem) {
            const emoji = emojiItem.dataset.emoji;
            insertEmoji(emoji);
            
            // 保存到最近使用
            saveRecentEmoji(emoji);
            
            // 关闭选择器
            closeEmojiPicker();
            vibrateDevice([30]);
        }
    });
    
    // 打开/关闭
    emojiBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        if (emojiPicker.classList.contains('active')) {
            closeEmojiPicker();
        } else {
            openEmojiPicker();
        }
    });
    
    function openEmojiPicker() {
        emojiPicker.classList.add('active');
        emojiBtn.textContent = '😀';
        renderEmojis(currentCategory);
    }
    
    function closeEmojiPicker() {
        emojiPicker.classList.remove('active');
        emojiBtn.textContent = '😊';
    }
    
    // 点击外部关闭
    document.addEventListener('click', (e) => {
        if (!emojiPicker.contains(e.target) && e.target !== emojiBtn) {
            closeEmojiPicker();
        }
    });
    
    function insertEmoji(emoji) {
        const chatInput = document.getElementById('chatInput');
        if (chatInput) {
            const start = chatInput.selectionStart;
            const end = chatInput.selectionEnd;
            const text = chatInput.value;
            chatInput.value = text.substring(0, start) + emoji + text.substring(end);
            chatInput.selectionStart = chatInput.selectionEnd = start + emoji.length;
            chatInput.focus();
            autoResizeTextarea(chatInput);
        }
    }
    
    function saveRecentEmoji(emoji) {
        let recentEmojis = JSON.parse(localStorage.getItem('recentEmojis') || '[]');
        recentEmojis = recentEmojis.filter(e => e !== emoji);
        recentEmojis.unshift(emoji);
        recentEmojis = recentEmojis.slice(0, 24);
        localStorage.setItem('recentEmojis', JSON.stringify(recentEmojis));
    }
}

// ===== 消息反应功能 =====
function initReactionFeature() {
    const reactionPicker = document.getElementById('reactionPicker');
    let currentMessage = null;
    let longPressTimer = null;
    
    // 反应按钮点击
    reactionPicker.querySelectorAll('.reaction-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            if (currentMessage) {
                addReaction(currentMessage, btn.dataset.reaction);
                hideReactionPicker();
                vibrateDevice([50]);
            }
        });
    });
    
    // 消息长按显示反应选择器
    document.getElementById('chatMessages')?.addEventListener('touchstart', (e) => {
        const message = e.target.closest('.message');
        if (message && window.innerWidth <= 768) {
            longPressTimer = setTimeout(() => {
                showReactionPicker(message, e.touches[0]);
                vibrateDevice([100, 50, 100]);
            }, 500);
        }
    }, { passive: true });
    
    document.getElementById('chatMessages')?.addEventListener('touchmove', () => {
        clearTimeout(longPressTimer);
    }, { passive: true });
    
    document.getElementById('chatMessages')?.addEventListener('touchend', () => {
        clearTimeout(longPressTimer);
    });
    
    // 桌面端右键菜单
    document.getElementById('ctxReaction')?.addEventListener('click', () => {
        const contextMenu = document.getElementById('contextMenu');
        if (currentMessage) {
            const rect = currentMessage.getBoundingClientRect();
            showReactionPickerAt(rect.right - 100, rect.bottom + 10);
        }
        contextMenu.classList.remove('active');
    });
    
    function showReactionPicker(message, touch) {
        currentMessage = message;
        const rect = message.getBoundingClientRect();
        showReactionPickerAt(
            touch ? touch.clientX - 120 : rect.right - 120,
            touch ? touch.clientY - 30 : rect.bottom + 10
        );
    }
    
    function showReactionPickerAt(x, y) {
        reactionPicker.style.left = Math.max(10, Math.min(x, window.innerWidth - 260)) + 'px';
        reactionPicker.style.top = Math.max(10, Math.min(y, window.innerHeight - 60)) + 'px';
        reactionPicker.classList.add('active');
    }
    
    function hideReactionPicker() {
        reactionPicker.classList.remove('active');
        currentMessage = null;
    }
    
    // 点击外部关闭
    document.addEventListener('click', (e) => {
        if (!reactionPicker.contains(e.target)) {
            hideReactionPicker();
        }
    });
    
    // ESC关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            hideReactionPicker();
        }
    });
    
    function addReaction(message, reaction) {
        const reactionsContainer = message.querySelector('.message-reactions') || createReactionsContainer(message);
        
        // 检查是否已存在该反应
        const existingReaction = reactionsContainer.querySelector(`[data-reaction="${reaction}"]`);
        if (existingReaction) {
            const count = parseInt(existingReaction.querySelector('.count')?.textContent || '1');
            existingReaction.querySelector('.count').textContent = count + 1;
        } else {
            const reactionEl = document.createElement('span');
            reactionEl.className = 'message-reaction';
            reactionEl.dataset.reaction = reaction;
            reactionEl.innerHTML = `${reaction}<span class="count">1</span>`;
            reactionsContainer.appendChild(reactionEl);
            
            // 点击反应移除
            reactionEl.addEventListener('click', () => {
                const count = parseInt(reactionEl.querySelector('.count')?.textContent || '1');
                if (count > 1) {
                    reactionEl.querySelector('.count').textContent = count - 1;
                } else {
                    reactionEl.remove();
                }
            });
        }
        
        // 保存反应到localStorage
        saveReactions(message);
    }
    
    function createReactionsContainer(message) {
        const container = document.createElement('div');
        container.className = 'message-reactions';
        message.appendChild(container);
        return container;
    }
    
    function saveReactions(message) {
        const sessionId = message.closest('.session-messages')?.dataset.sessionId;
        if (!sessionId) return;
        
        const messages = document.querySelectorAll(`.session-messages[data-session-id="${sessionId}"] .message`);
        const reactions = {};
        
        messages.forEach((msg, index) => {
            const msgReactions = msg.querySelector('.message-reactions');
            if (msgReactions) {
                const reactionEls = msgReactions.querySelectorAll('.message-reaction');
                if (reactionEls.length > 0) {
                    reactions[index] = Array.from(reactionEls).map(el => ({
                        emoji: el.dataset.reaction,
                        count: parseInt(el.querySelector('.count')?.textContent || '1')
                    }));
                }
            }
        });
        
        // 保存到会话数据
        const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
        const sessionIndex = sessions.findIndex(s => s.id === sessionId);
        if (sessionIndex !== -1) {
            sessions[sessionIndex].reactions = reactions;
            StorageManager.setSessions(sessions);
        }
    }
}

// ===== 新手引导 =====
function initOnboarding() {
    const onboardingOverlay = document.getElementById('onboardingOverlay');
    const onboardingTour = document.getElementById('onboardingTour');
    const firstUseHint = document.getElementById('firstUseHint');
    
    // 检查是否已完成新手引导
    const hasSeenOnboarding = localStorage.getItem('hasSeenOnboarding');
    
    if (!hasSeenOnboarding) {
        // 显示新手引导
        setTimeout(() => {
            startOnboarding();
        }, 1000);
    }
    
    function startOnboarding() {
        onboardingOverlay.classList.add('active');
        onboardingTour.classList.add('active');
        showStep(1);
    }
    
    function showStep(step) {
        document.querySelectorAll('.onboarding-step').forEach(s => s.classList.remove('active'));
        const stepEl = document.querySelector(`.onboarding-step[data-step="${step}"]`);
        if (stepEl) {
            stepEl.classList.add('active');
        }
    }
    
    function endOnboarding() {
        onboardingOverlay.classList.remove('active');
        onboardingTour.classList.remove('active');
        localStorage.setItem('hasSeenOnboarding', 'true');
        
        // 显示功能提示
        setTimeout(() => {
            showFirstUseHint();
        }, 2000);
    }
    
    // 事件绑定
    document.querySelector('.onboarding-skip')?.addEventListener('click', endOnboarding);
    document.querySelector('.onboarding-finish')?.addEventListener('click', endOnboarding);
    
    document.querySelectorAll('.onboarding-next').forEach(btn => {
        btn.addEventListener('click', () => {
            const currentStep = document.querySelector('.onboarding-step.active');
            const stepNum = parseInt(currentStep?.dataset.step || '1');
            showStep(stepNum + 1);
            vibrateDevice([30]);
        });
    });
    
    document.querySelectorAll('.onboarding-prev').forEach(btn => {
        btn.addEventListener('click', () => {
            const currentStep = document.querySelector('.onboarding-step.active');
            const stepNum = parseInt(currentStep?.dataset.step || '1');
            showStep(stepNum - 1);
            vibrateDevice([30]);
        });
    });
    
    // 首次使用提示
    document.getElementById('closeFirstHint')?.addEventListener('click', () => {
        firstUseHint.classList.remove('show');
        localStorage.setItem('hasSeenFirstHint', 'true');
    });
    
    function showFirstUseHint() {
        const hasSeenFirstHint = localStorage.getItem('hasSeenFirstHint');
        if (!hasSeenFirstHint) {
            firstUseHint.classList.add('show');
        }
    }
    
    // 手动开始引导
    window.startOnboardingTour = startOnboarding;
}

// ===== 震动反馈 =====
function initHapticFeedback() {
    // 按钮点击震动
    document.addEventListener('click', (e) => {
        const btn = e.target.closest('button:not([disabled])');
        if (btn && window.innerWidth <= 768) {
            // 根据按钮类型选择震动模式
            const isImportant = btn.classList.contains('send-btn') || 
                               btn.classList.contains('nav-btn');
            vibrateDevice(isImportant ? [50] : [20]);
        }
    });
    
    // 开关切换震动
    document.querySelectorAll('.switch input').forEach(toggle => {
        toggle.addEventListener('change', () => {
            vibrateDevice([30, 20, 30]);
        });
    });
    
    // 通知震动
    const originalShowNotification = window.showNotification;
    window.showNotification = function(title, message, type) {
        if (type === 'error') {
            vibrateDevice([100, 50, 100, 50, 100]);
        } else if (type === 'success') {
            vibrateDevice([50, 30, 50]);
        }
        
        if (originalShowNotification) {
            return originalShowNotification.apply(this, arguments);
        }
    };
}

// ===== 会话分组/文件夹功能 =====
function initSessionFolders() {
    const folderHeader = document.getElementById('folderHeader');
    const favoritesHeader = document.getElementById('favoritesHeader');
    const sessionSearchInput = document.getElementById('sessionSearchInput');
    const clearSessionSearch = document.getElementById('clearSessionSearch');
    
    // 初始化文件夹数据结构
    initFolders();
    
    // 切换文件夹展开/收起
    folderHeader?.addEventListener('click', () => {
        const content = document.getElementById('folderSessions');
        folderHeader.classList.toggle('expanded');
        content.classList.toggle('expanded');
    });
    
    // 切换收藏展开/收起
    favoritesHeader?.addEventListener('click', () => {
        const content = document.getElementById('favoritesSessions');
        favoritesHeader.classList.toggle('expanded');
        content.classList.toggle('expanded');
    });
    
    // 搜索会话
    sessionSearchInput?.addEventListener('input', (e) => {
        const query = e.target.value.trim();
        clearSessionSearch.style.display = query ? 'flex' : 'none';
        filterSessions(query);
    });
    
    clearSessionSearch?.addEventListener('click', () => {
        sessionSearchInput.value = '';
        clearSessionSearch.style.display = 'none';
        filterSessions('');
    });
    
    // 渲染文件夹和收藏
    renderFolders();
    renderFavorites();
    renderSessions();
}

function initFolders() {
    const folders = JSON.parse(localStorage.getItem('sessionFolders') || '[]');
    if (folders.length === 0) {
        // 创建默认文件夹
        StorageManager.setFolders([
            { id: 'default', name: '默认分组', color: '#2563eb', sessions: [] }
        ]);
    }
}

function renderFolders() {
    const folders = JSON.parse(localStorage.getItem('sessionFolders') || '[]');
    const folderSessions = document.getElementById('folderSessions');
    const folderCount = document.getElementById('folderCount');
    const folderPickerList = document.getElementById('folderPickerList');
    
    // 统计所有文件夹中的会话数
    const totalSessions = folders.reduce((sum, f) => sum + f.sessions.length, 0);
    if (folderCount) folderCount.textContent = totalSessions;
    
    // 渲染文件夹列表
    if (folderSessions) {
        folderSessions.innerHTML = folders.map(folder => {
            const folderSessionsList = folder.sessions.map(sessionId => {
                const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
                const session = sessions.find(s => s.id === sessionId);
                if (!session) return '';
                return `
                    <div class="folder-session-item ${session.id === currentSessionId ? 'active' : ''}" 
                         data-session-id="${session.id}"
                         onclick="loadSession('${session.id}')">
                        <span class="session-favorite-icon">${session.favorite ? '<svg class="icon" viewBox="0 0 24 24" fill="currentColor" stroke="currentColor" stroke-width="1"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>' : ''}</span>
                        <span class="session-pin-icon">${session.pinned ? '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="17" x2="12" y2="22"/><path d="M5 17h14v-1.76a2 2 0 00-1.11-1.79l-1.78-.9A2 2 0 0115 10.76V6h1a2 2 0 000-4H8a2 2 0 000 4h1v4.76a2 2 0 01-1.11 1.79l-1.78.9A2 2 0 005 15.24V17z"/></svg>' : ''}</span>
                        <span>${escapeHtml(session.title)}</span>
                    </div>
                `;
            }).join('');
            
            return `
                <div class="folder-group" data-folder-id="${folder.id}">
                    <button class="folder-header sub-folder" onclick="toggleSubFolder('${folder.id}')">
                        <span class="folder-color-indicator" style="background:${folder.color}"></span>
                        <span class="folder-title">${escapeHtml(folder.name)}</span>
                        <span class="folder-count">${folder.sessions.length}</span>
                    </button>
                    <div class="folder-sessions sub-sessions" id="subFolder_${folder.id}">
                        ${folderSessionsList || '<div style="padding:8px;color:var(--text-muted);font-size:12px;">暂无会话</div>'}
                    </div>
                </div>
            `;
        }).join('');
    }
    
    // 渲染文件夹选择器
    if (folderPickerList) {
        folderPickerList.innerHTML = folders.map(folder => `
            <button class="folder-picker-item" data-folder="${folder.id}">
                <span class="folder-color-indicator" style="background:${folder.color}"></span>
                <span>${escapeHtml(folder.name)}</span>
            </button>
        `).join('');
    }
}

function renderFavorites() {
    const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
    const favorites = sessions.filter(s => s.favorite);
    const favoritesSessions = document.getElementById('favoritesSessions');
    const favoritesCount = document.getElementById('favoritesCount');
    
    if (favoritesCount) favoritesCount.textContent = favorites.length;
    
    if (favoritesSessions) {
        favoritesSessions.innerHTML = favorites.map(session => `
            <div class="folder-session-item ${session.id === currentSessionId ? 'active' : ''}" 
                 data-session-id="${session.id}"
                 onclick="loadSession('${session.id}')">
                <span class="session-favorite-icon">⭐</span>
                <span class="session-pin-icon">${session.pinned ? '📌' : ''}</span>
                <span>${escapeHtml(session.title)}</span>
            </div>
        `).join('') || '<div style="padding:8px;color:var(--text-muted);font-size:12px;">暂无收藏</div>';
    }
}

function toggleSubFolder(folderId) {
    const content = document.getElementById(`subFolder_${folderId}`);
    const header = document.querySelector(`[data-folder-id="${folderId}"] .folder-header`);
    if (content) content.classList.toggle('expanded');
    if (header) header.classList.toggle('expanded');
}

function filterSessions(query) {
    const sessionList = document.getElementById('sessionList');
    const allItems = sessionList.querySelectorAll('.session-item');
    
    allItems.forEach(item => {
        const title = item.querySelector('.session-title')?.textContent?.toLowerCase() || '';
        const match = title.includes(query.toLowerCase());
        item.style.display = match ? '' : 'none';
    });
}

// ===== 会话收藏/置顶 =====
function initSessionActions() {
    const contextMenu = document.getElementById('sessionContextMenu');
    let selectedSessionId = null;
    
    // 右键点击会话
    document.getElementById('sessionList')?.addEventListener('contextmenu', (e) => {
        const sessionItem = e.target.closest('.session-item');
        if (sessionItem) {
            e.preventDefault();
            selectedSessionId = sessionItem.dataset.sessionId;
            showSessionContextMenu(e.clientX, e.clientY, selectedSessionId);
        }
    });
    
    // 长按会话（移动端）
    let pressTimer;
    document.getElementById('sessionList')?.addEventListener('touchstart', (e) => {
        const sessionItem = e.target.closest('.session-item');
        if (sessionItem) {
            pressTimer = setTimeout(() => {
                selectedSessionId = sessionItem.dataset.sessionId;
                const rect = sessionItem.getBoundingClientRect();
                showSessionContextMenu(rect.right - 180, rect.bottom + 10, selectedSessionId);
                vibrateDevice([50]);
            }, 500);
        }
    });
    
    document.getElementById('sessionList')?.addEventListener('touchmove', () => {
        clearTimeout(pressTimer);
    });
    
    // 菜单项点击
    contextMenu?.querySelectorAll('.session-menu-item').forEach(item => {
        item.addEventListener('click', () => {
            const action = item.dataset.action;
            handleSessionAction(action, selectedSessionId);
            hideSessionContextMenu();
        });
    });
    
    // 点击外部关闭
    document.addEventListener('click', (e) => {
        if (!contextMenu.contains(e.target)) {
            hideSessionContextMenu();
        }
    });
    
    // ESC关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            hideSessionContextMenu();
        }
    });
    
    function showSessionContextMenu(x, y, sessionId) {
        const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
        const session = sessions.find(s => s.id === sessionId);
        
        if (!session) return;
        
        // 更新菜单项状态
        const favoriteItem = contextMenu.querySelector('[data-action="favorite"]');
        const unfavoriteItem = contextMenu.querySelector('[data-action="unfavorite"]');
        const pinItem = contextMenu.querySelector('[data-action="pin"]');
        const unpinItem = contextMenu.querySelector('[data-action="unpin"]');
        
        if (favoriteItem) favoriteItem.style.display = session.favorite ? 'none' : '';
        if (unfavoriteItem) unfavoriteItem.style.display = session.favorite ? '' : 'none';
        if (pinItem) pinItem.style.display = session.pinned ? 'none' : '';
        if (unpinItem) unpinItem.style.display = session.pinned ? '' : 'none';
        
        // 定位
        contextMenu.style.left = Math.min(x, window.innerWidth - 200) + 'px';
        contextMenu.style.top = Math.min(y, window.innerHeight - 300) + 'px';
        contextMenu.classList.add('active');
    }
    
    function hideSessionContextMenu() {
        contextMenu.classList.remove('active');
    }
}

function handleSessionAction(action, sessionId) {
    const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
    const folders = JSON.parse(localStorage.getItem('sessionFolders') || '[]');
    const sessionIndex = sessions.findIndex(s => s.id === sessionId);
    
    if (sessionIndex === -1) return;
    
    switch (action) {
        case 'favorite':
            sessions[sessionIndex].favorite = true;
            showNotification('成功', '会话已收藏');
            break;
            
        case 'unfavorite':
            sessions[sessionIndex].favorite = false;
            showNotification('成功', '已取消收藏');
            break;
            
        case 'pin':
            sessions[sessionIndex].pinned = true;
            showNotification('成功', '会话已置顶');
            break;
            
        case 'unpin':
            sessions[sessionIndex].pinned = false;
            showNotification('成功', '已取消置顶');
            break;
            
        case 'move':
            showFolderPicker(sessionId);
            return; // 特殊处理
            
        case 'rename':
            const newTitle = prompt('请输入新名称:', sessions[sessionIndex].title);
            if (newTitle && newTitle.trim()) {
                sessions[sessionIndex].title = newTitle.trim();
                showNotification('成功', '会话已重命名');
            }
            break;
            
        case 'export':
            exportSingleSession(sessionId);
            return;
            
        case 'delete':
            if (confirm('确定删除这个会话?')) {
                // 从文件夹中移除
                folders.forEach(folder => {
                    folder.sessions = folder.sessions.filter(id => id !== sessionId);
                });
                StorageManager.setFolders(folders);
                
                // 删除会话
                sessions.splice(sessionIndex, 1);
                StorageManager.setSessions(sessions);
                
                if (currentSessionId === sessionId) {
                    if (sessions.length > 0) {
                        loadSession(sessions[0].id);
                    } else {
                        createNewSession();
                    }
                }
                
                renderSessions();
                renderFolders();
                renderFavorites();
                showNotification('成功', '会话已删除');
            }
            return;
    }
    
    StorageManager.setSessions(sessions);
    renderSessions();
    renderFolders();
    renderFavorites();
}

function showFolderPicker(sessionId) {
    const modal = document.getElementById('folderPickerModal');
    modal.classList.add('active');
    modal.dataset.sessionId = sessionId;
    
    // 更新文件夹列表
    renderFolders();
}

function hideFolderPicker() {
    document.getElementById('folderPickerModal').classList.remove('active');
}

// ===== 文件夹管理 =====
function initFolderManagement() {
    // 关闭文件夹选择弹窗
    document.getElementById('closeFolderPicker')?.addEventListener('click', hideFolderPicker);
    
    // 关闭创建文件夹弹窗
    document.getElementById('closeCreateFolder')?.addEventListener('click', hideCreateFolderModal);
    document.getElementById('cancelCreateFolder')?.addEventListener('click', hideCreateFolderModal);
    
    // 从选择器创建文件夹
    document.getElementById('createFolderFromPicker')?.addEventListener('click', () => {
        hideFolderPicker();
        showCreateFolderModal();
    });
    
    // 创建文件夹
    document.getElementById('confirmCreateFolder')?.addEventListener('click', createNewFolder);
    
    // 颜色选择
    document.querySelectorAll('.color-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.color-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });
    
    // 文件夹选择
    document.getElementById('folderPickerList')?.addEventListener('click', (e) => {
        const item = e.target.closest('.folder-picker-item');
        if (item) {
            const folderId = item.dataset.folder;
            const modal = document.getElementById('folderPickerModal');
            const sessionId = modal.dataset.sessionId;
            moveSessionToFolder(sessionId, folderId);
            hideFolderPicker();
        }
    });
    
    // 键盘确认
    document.getElementById('newFolderName')?.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            createNewFolder();
        }
    });
}

function showCreateFolderModal() {
    const modal = document.getElementById('createFolderModal');
    modal.classList.add('active');
    document.getElementById('newFolderName').value = '';
    document.getElementById('newFolderName').focus();
}

function hideCreateFolderModal() {
    document.getElementById('createFolderModal').classList.remove('active');
}

function createNewFolder() {
    const name = document.getElementById('newFolderName').value.trim();
    if (!name) {
        showNotification('错误', '请输入文件夹名称');
        return;
    }
    
    const activeColor = document.querySelector('.color-btn.active');
    const color = activeColor?.dataset.color || '#2563eb';
    
    const folders = JSON.parse(localStorage.getItem('sessionFolders') || '[]');
    const newFolder = {
        id: 'folder_' + Date.now(),
        name: name,
        color: color,
        sessions: []
    };
    
    folders.push(newFolder);
    StorageManager.setFolders(folders);
    
    hideCreateFolderModal();
    renderFolders();
    showNotification('成功', '文件夹已创建');
}

function moveSessionToFolder(sessionId, folderId) {
    const folders = JSON.parse(localStorage.getItem('sessionFolders') || '[]');
    
    // 从所有文件夹移除
    folders.forEach(folder => {
        folder.sessions = folder.sessions.filter(id => id !== sessionId);
    });
    
    // 添加到目标文件夹
    if (folderId) {
        const targetFolder = folders.find(f => f.id === folderId);
        if (targetFolder) {
            targetFolder.sessions.push(sessionId);
        }
    }
    
    StorageManager.setFolders(folders);
    renderFolders();
    renderSessions();
    showNotification('成功', '会话已移动');
}

// ===== 搜索历史记录 =====
function initSearchHistory() {
    const modal = document.getElementById('searchHistoryModal');
    const list = document.getElementById('searchHistoryList');
    
    // 关闭
    document.getElementById('closeSearchHistory')?.addEventListener('click', () => {
        modal.classList.remove('active');
    });
    
    // 清空历史
    document.getElementById('clearSearchHistory')?.addEventListener('click', () => {
        if (confirm('确定清空所有搜索历史?')) {
            localStorage.removeItem('searchHistory');
            renderSearchHistory();
            showNotification('成功', '搜索历史已清空');
        }
    });
    
    // ESC关闭
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && modal.classList.contains('active')) {
            modal.classList.remove('active');
        }
    });
    
    // 点击外部关闭
    modal?.addEventListener('click', (e) => {
        if (e.target === modal) {
            modal.classList.remove('active');
        }
    });
}

function showSearchHistory() {
    const modal = document.getElementById('searchHistoryModal');
    modal.classList.add('active');
    renderSearchHistory();
}

function renderSearchHistory() {
    const list = document.getElementById('searchHistoryList');
    const history = JSON.parse(localStorage.getItem('searchHistory') || '[]');
    
    if (history.length === 0) {
        list.innerHTML = `
            <div class="empty-history">
                <div class="empty-history-icon"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></div>
                <p>暂无搜索历史</p>
            </div>
        `;
        return;
    }
    
    list.innerHTML = history.map(item => `
        <div class="search-history-item" data-query="${escapeHtml(item.query)}">
            <span class="search-icon"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg></span>
            <span class="search-text">${escapeHtml(item.query)}</span>
            <span class="search-time">${formatTimeAgo(item.time)}</span>
            <button class="search-delete" onclick="event.stopPropagation(); deleteSearchHistoryItem('${escapeHtml(item.query)}')">×</button>
        </div>
    `).join('');
    
    // 点击搜索历史项
    list.querySelectorAll('.search-history-item').forEach(item => {
        item.addEventListener('click', () => {
            const query = item.dataset.query;
            const modal = document.getElementById('searchHistoryModal');
            modal.classList.remove('active');
            
            // 执行搜索
            const searchInput = document.getElementById('globalSearchInput') || document.getElementById('searchInput');
            if (searchInput) {
                searchInput.value = query;
                searchInput.dispatchEvent(new Event('input'));
            }
        });
    });
}

function saveSearchHistory(query) {
    if (!query.trim()) return;
    
    let history = JSON.parse(localStorage.getItem('searchHistory') || '[]');
    
    // 移除重复
    history = history.filter(h => h.query !== query);
    
    // 添加到开头
    history.unshift({
        query: query,
        time: Date.now()
    });
    
    // 限制数量
    history = history.slice(0, 50);
    
    localStorage.setItem('searchHistory', JSON.stringify(history));
}

function deleteSearchHistoryItem(query) {
    let history = JSON.parse(localStorage.getItem('searchHistory') || '[]');
    history = history.filter(h => h.query !== query);
    localStorage.setItem('searchHistory', JSON.stringify(history));
    renderSearchHistory();
}

function formatTimeAgo(timestamp) {
    const seconds = Math.floor((Date.now() - timestamp) / 1000);
    
    if (seconds < 60) return '刚刚';
    if (seconds < 3600) return Math.floor(seconds / 60) + '分钟前';
    if (seconds < 86400) return Math.floor(seconds / 3600) + '小时前';
    if (seconds < 604800) return Math.floor(seconds / 86400) + '天前';
    return new Date(timestamp).toLocaleDateString();
}

// ===== 数据备份/恢复 =====
function initDataBackup() {
    // 导出所有数据
    window.exportAllData = function() {
        const data = {
            version: '1.0',
            exportTime: new Date().toISOString(),
            sessions: JSON.parse(localStorage.getItem('sessions') || '[]'),
            folders: JSON.parse(localStorage.getItem('sessionFolders') || '[]'),
            settings: JSON.parse(localStorage.getItem('appSettings') || '{}'),
            searchHistory: JSON.parse(localStorage.getItem('searchHistory') || '[]'),
            recentEmojis: JSON.parse(localStorage.getItem('recentEmojis') || '[]'),
            commandTemplates: JSON.parse(localStorage.getItem('commandTemplates') || '[]')
        };
        
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `kylinops-backup-${new Date().toISOString().slice(0,10)}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        showNotification('成功', '数据已导出');
    };
    
    // 导入数据
    window.importAllData = function(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const data = JSON.parse(e.target.result);
                
                // 验证数据格式
                if (!data.version || !data.sessions) {
                    throw new Error('无效的备份文件');
                }
                
                // 合并会话（避免重复ID）
                const existingSessions = JSON.parse(localStorage.getItem('sessions') || '[]');
                const existingIds = new Set(existingSessions.map(s => s.id));
                const newSessions = data.sessions.filter(s => !existingIds.has(s.id));
                const allSessions = [...existingSessions, ...newSessions];
                
                // 合并文件夹
                const existingFolders = JSON.parse(localStorage.getItem('sessionFolders') || '[]');
                const existingFolderIds = new Set(existingFolders.map(f => f.id));
                const newFolders = (data.folders || []).filter(f => !existingFolderIds.has(f.id));
                const allFolders = [...existingFolders, ...newFolders];
                
                // 保存
                StorageManager.setSessions(allSessions);
                StorageManager.setFolders(allFolders);
                
                if (data.settings) {
                    const currentSettings = JSON.parse(localStorage.getItem('appSettings') || '{}');
                    localStorage.setItem('appSettings', JSON.stringify({ ...currentSettings, ...data.settings }));
                }
                
                if (data.commandTemplates) {
                    localStorage.setItem('commandTemplates', JSON.stringify(data.commandTemplates));
                }
                
                // 刷新界面
                loadSessions();
                renderFolders();
                renderFavorites();
                
                showNotification('成功', `已导入 ${newSessions.length} 个会话`);
                
            } catch (error) {
                showNotification('错误', '导入失败: ' + error.message, 'error');
            }
        };
        reader.readAsText(file);
    };
}

// 更新renderSessions函数以支持收藏和置顶
const originalRenderSessions = window.renderSessions;
window.renderSessions = function() {
    if (originalRenderSessions) {
        originalRenderSessions();
    }
    
    const sessionList = document.getElementById('sessionList');
    if (!sessionList) return;
    
    // 添加收藏和置顶图标
    const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
    
    sessionList.querySelectorAll('.session-item').forEach(item => {
        const sessionId = item.dataset.sessionId;
        const session = sessions.find(s => s.id === sessionId);
        
        if (session) {
            if (session.favorite) {
                item.classList.add('favorited');
            }
            if (session.pinned) {
                item.classList.add('pinned');
            }
        }
    });
};

// ===== 网络状态检测和自动重连 =====
let isOnline = navigator.onLine;
let reconnectAttempts = 0;
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY = 3000;

function initNetworkStatus() {
    // 初始状态
    updateNetworkStatus();
    
    // 监听网络状态变化
    window.addEventListener('online', handleOnline);
    window.addEventListener('offline', handleOffline);
    
    // 定期检查网络状态
    setInterval(checkNetworkHealth, 30000);
    
    // 重试按钮
    document.getElementById('offlineRetryBtn')?.addEventListener('click', () => {
        attemptReconnect();
    });
    
    // 监听连接质量变化
    if ('connection' in navigator) {
        navigator.connection.addEventListener('change', handleConnectionChange);
    }
}

function updateNetworkStatus() {
    const statusDot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    const networkStatus = document.getElementById('networkStatus');
    
    if (isOnline) {
        statusDot.className = 'status-dot online';
        statusText.textContent = '在线';
        networkStatus.className = 'network-status connected';
        networkStatus.title = '网络已连接';
    } else {
        statusDot.className = 'status-dot offline';
        statusText.textContent = '离线';
        networkStatus.className = 'network-status disconnected';
        networkStatus.title = '网络已断开';
    }
}

function handleOnline() {
    isOnline = true;
    reconnectAttempts = 0;
    updateNetworkStatus();
    
    // 隐藏离线横幅
    const offlineBanner = document.getElementById('offlineBanner');
    offlineBanner?.classList.add('hidden');
    
    // 移除离线样式
    document.querySelector('.chat-input-area')?.classList.remove('offline');
    
    // 显示重连成功
    showNotification('已恢复连接', '网络已恢复，正在同步数据...', 'success');
    
    // 重新获取数据
    refreshData();
}

function handleOffline() {
    isOnline = false;
    updateNetworkStatus();
    
    // 显示离线横幅
    const offlineBanner = document.getElementById('offlineBanner');
    offlineBanner?.classList.remove('hidden');
    
    // 添加离线样式
    document.querySelector('.chat-input-area')?.classList.add('offline');
    
    showNotification('网络已断开', '您现在处于离线模式', 'warning');
    
    // 保存当前状态到本地缓存
    saveOfflineState();
}

function handleConnectionChange() {
    const connection = navigator.connection;
    
    // 检测慢速连接
    if (connection.effectiveType === '2g' || connection.effectiveType === 'slow-2g') {
        showNotification('网络较慢', '当前网络连接较慢，可能会影响体验', 'warning');
    }
}

function checkNetworkHealth() {
    if (!isOnline) return;
    
    // 发送ping请求检测连接
    fetch('/api/ping', { 
        method: 'HEAD',
        cache: 'no-cache' 
    }).then(() => {
        if (!isOnline) {
            handleOnline();
        }
    }).catch(() => {
        // 静默处理，不影响用户体验
    });
}

function attemptReconnect() {
    if (isOnline) return;
    
    reconnectAttempts++;
    const reconnectingIndicator = document.getElementById('reconnectingIndicator');
    reconnectingIndicator?.classList.add('active');
    
    showNotification('正在重连...', `尝试第 ${reconnectAttempts} 次连接`, 'info');
    
    // 尝试通过fetch检测网络
    fetch('/api/ping', { 
        method: 'HEAD',
        cache: 'no-cache'
    }).then(() => {
        handleOnline();
        reconnectingIndicator?.classList.remove('active');
    }).catch(() => {
        reconnectingIndicator?.classList.remove('active');
        
        if (reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
            showNotification('连接失败', `${RECONNECT_DELAY / 1000}秒后重试...`, 'warning');
            setTimeout(attemptReconnect, RECONNECT_DELAY);
        } else {
            showNotification('无法连接', '请检查网络设置', 'error');
            reconnectAttempts = 0;
        }
    });
}

// ===== API错误友好提示 =====
const originalFetch = window.fetch;

window.fetch = async function(...args) {
    const [resource, options = {}] = args;
    
    try {
        const response = await originalFetch(resource, options);
        
        // 检查HTTP状态
        if (!response.ok) {
            throw await handleHttpError(response);
        }
        
        return response;
    } catch (error) {
        // 处理网络错误
        if (!navigator.onLine) {
            handleOffline();
            throw { 
                type: 'NETWORK_ERROR', 
                message: '网络已断开，请检查您的网络连接',
                retry: true 
            };
        }
        
        // 处理超时
        if (error.name === 'AbortError' || error.type === 'timeout') {
            showErrorToast({
                title: '请求超时',
                message: '服务器响应时间过长，可能网络较慢或服务器繁忙',
                actions: [
                    { label: '重试', primary: true, action: () => window.fetch(resource, options) }
                ]
            });
            throw { type: 'TIMEOUT', message: '请求超时' };
        }
        
        // 其他错误
        throw error;
    }
};

async function handleHttpError(response) {
    let errorInfo = {
        type: 'HTTP_ERROR',
        status: response.status,
        message: '请求失败',
        retry: false
    };
    
    switch (response.status) {
        case 400:
            errorInfo.title = '请求错误';
            errorInfo.message = '发送的请求格式不正确，请检查输入';
            errorInfo.retry = false;
            break;
        case 401:
            errorInfo.title = '未授权';
            errorInfo.message = '登录已过期，请重新登录';
            errorInfo.retry = false;
            errorInfo.action = () => {
                localStorage.removeItem('token');
                window.location.href = 'login.html';
            };
            break;
        case 403:
            errorInfo.title = '禁止访问';
            errorInfo.message = '您没有权限执行此操作';
            errorInfo.retry = false;
            break;
        case 404:
            errorInfo.title = '未找到';
            errorInfo.message = '请求的资源不存在';
            errorInfo.retry = false;
            break;
        case 408:
            errorInfo.title = '请求超时';
            errorInfo.message = '服务器处理请求超时';
            errorInfo.retry = true;
            break;
        case 429:
            errorInfo.title = '请求过于频繁';
            errorInfo.message = '操作太频繁，请稍后再试';
            errorInfo.retry = true;
            break;
        case 500:
            errorInfo.title = '服务器错误';
            errorInfo.message = '服务器内部错误，请稍后再试';
            errorInfo.retry = true;
            break;
        case 502:
        case 503:
        case 504:
            errorInfo.title = '服务器不可用';
            errorInfo.message = '服务器暂时不可用，请稍后再试';
            errorInfo.retry = true;
            break;
        default:
            errorInfo.title = '请求失败';
            errorInfo.message = `服务器返回错误 (${response.status})`;
            errorInfo.retry = true;
    }
    
    // 显示错误提示
    showErrorToast(errorInfo);
    
    return errorInfo;
}

function showErrorToast(error) {
    const existing = document.querySelector('.error-toast');
    if (existing) existing.remove();
    
    const toast = document.createElement('div');
    toast.className = `error-toast ${error.type === 'TIMEOUT' ? 'warning' : ''}`;
    
    let actionsHtml = '';
    if (error.retry) {
        actionsHtml = `
            <div class="error-actions">
                <button class="error-btn secondary" onclick="this.closest('.error-toast').remove()">关闭</button>
                <button class="error-btn primary" onclick="this.closest('.error-toast').remove(); window.location.reload()">刷新页面</button>
            </div>
        `;
    }
    
    toast.innerHTML = `
        <div class="error-header">
            <span class="error-icon">${getErrorIcon(error.type)}</span>
            <span class="error-title">${error.title || '出错了'}</span>
        </div>
        <div class="error-message">${error.message}</div>
        ${actionsHtml}
    `;
    
    document.body.appendChild(toast);
    
    // 自动关闭（非关键错误）
    if (!error.action && !error.retry) {
        setTimeout(() => {
            toast.style.opacity = '0';
            setTimeout(() => toast.remove(), 300);
        }, 5000);
    }
}

function getErrorIcon(type) {
    const icons = {
        'NETWORK_ERROR': '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 16c4.42 0 8-3.58 8-8"/><path d="M4 20c6.63 0 12-5.37 12-12"/><path d="M4 12a8 8 0 018 8"/><line x1="20" y1="4" x2="20.01" y2="4"/></svg>',
        'TIMEOUT': '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="4"/><polyline points="12 7 12 12 15 15"/></svg>',
        'HTTP_ERROR': '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
        'SERVER_ERROR': '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z"/></svg>',
        'AUTH_ERROR': '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>'
    };
    return icons[type] || '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';
}

// ===== 离线模式支持 =====
const OFFLINE_CACHE_KEY = 'offlineCache';
const OFFLINE_DATA_KEY = 'offlineData';

function initOfflineSupport() {
    // 检查是否支持Service Worker
    if ('serviceWorker' in navigator) {
        registerServiceWorker();
    }
    
    // 缓存API响应
    cacheApiResponses();
}

async function registerServiceWorker() {
    try {
        const registration = await navigator.serviceWorker.register('/sw.js');
        console.log('Service Worker registered:', registration.scope);
        
        // 检查更新
        registration.addEventListener('updatefound', () => {
            const newWorker = registration.installing;
            newWorker.addEventListener('statechange', () => {
                if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
                    showNotification('更新可用', '有新版本可用，刷新页面以更新', 'info', 10000);
                }
            });
        });
    } catch (error) {
        console.log('Service Worker registration failed:', error);
    }
}

function saveOfflineState() {
    // 保存当前会话状态
    const sessions = JSON.parse(localStorage.getItem('sessions') || '[]');
    const currentSessionId = window.currentSessionId;
    const chatMessages = document.getElementById('chatMessages')?.innerHTML;
    
    const offlineState = {
        timestamp: Date.now(),
        currentSessionId,
        chatMessages,
        sessions
    };
    
    localStorage.setItem(OFFLINE_DATA_KEY, JSON.stringify(offlineState));
}

function loadOfflineState() {
    const offlineState = JSON.parse(localStorage.getItem(OFFLINE_DATA_KEY) || '{}');
    
    if (offlineState.timestamp && Date.now() - offlineState.timestamp < 24 * 60 * 60 * 1000) {
        return offlineState;
    }
    
    return null;
}

function cacheApiResponses() {
    // 拦截API响应进行缓存
    const originalResponse = Response.prototype.clone;
    
    Response.prototype.clone = function() {
        const clone = originalResponse.call(this);
        
        // 缓存成功的GET请求响应
        if (this.url && this.url.includes('/api/') && this.ok) {
            cacheResponse(this.url, clone.clone());
        }
        
        return clone;
    };
}

const responseCache = new Map();

function cacheResponse(url, response) {
    response.clone().json().then(data => {
        responseCache.set(url, {
            data,
            timestamp: Date.now()
        });
        
        // 保存到localStorage
        const cache = JSON.parse(localStorage.getItem(OFFLINE_CACHE_KEY) || '{}');
        cache[url] = {
            data,
            timestamp: Date.now()
        };
        localStorage.setItem(OFFLINE_CACHE_KEY, JSON.stringify(cache));
    }).catch(() => {});
}

function getCachedResponse(url) {
    const cache = JSON.parse(localStorage.getItem(OFFLINE_CACHE_KEY) || '{}');
    const cached = cache[url];
    
    if (cached && Date.now() - cached.timestamp < 60 * 60 * 1000) { // 1小时内有效
        return cached.data;
    }
    
    return null;
}

function showOfflineMessage() {
    const chatMessages = document.getElementById('chatMessages');
    if (!chatMessages) return;
    
    const message = document.createElement('div');
    message.className = 'offline-message';
    message.innerHTML = `
        <div class="offline-icon"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 16c4.42 0 8-3.58 8-8"/><path d="M4 20c6.63 0 12-5.37 12-12"/><path d="M4 12a8 8 0 018 8"/><line x1="20" y1="4" x2="20.01" y2="4"/></svg></div>
        <div class="offline-title">您现在处于离线模式</div>
        <div class="offline-desc">
            无法连接到服务器，部分功能不可用<br>
            消息将在恢复连接后自动发送
        </div>
    `;
    
    chatMessages.appendChild(message);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ===== 增强的showNotification =====
const originalShowNotification = window.showNotification;
window.showNotification = function(title, message, type = 'info', duration = 3000) {
    // 如果处于离线状态且需要联网操作，给出提示
    if (!isOnline && type === 'error') {
        showOfflineMessage();
    }
    
    // 调用原始函数
    if (originalShowNotification) {
        return originalShowNotification(title, message, type, duration);
    }
};


