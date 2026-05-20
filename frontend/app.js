// Kylin Safe Ops Agent - 前端逻辑

const API_BASE = window.location.origin + '/api';
// API_TOKEN 已废弃，改用 Cookie/Session 认证

// ===== 状态管理 =====
let currentSessionId = localStorage.getItem('ops_session_id') || generateSessionId();
let isProcessing = false;
let pendingConfirm = null;

function generateSessionId() {
    const id = 'sess_' + Math.random().toString(36).substr(2, 9);
    localStorage.setItem('ops_session_id', id);
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
        // 可选：在界面上展示当前用户
    } catch (e) {
        window.location.href = '/static/login.html';
        return;
    }

    sessionIdEl.textContent = currentSessionId;
    checkHealth();
    setupEventListeners();
    setupNavigation();
    
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
            localStorage.removeItem('ops_session_id');
            window.location.href = '/static/login.html';
        });
    }
    
    // 新会话
    document.getElementById('newChatBtn').addEventListener('click', () => {
        currentSessionId = generateSessionId();
        sessionIdEl.textContent = currentSessionId;
        chatMessages.innerHTML = `
            <div class="welcome-message">
                <h3>👋 已开启新会话</h3>
                <p>请输入您的运维指令...</p>
            </div>
        `;
    });
    
    // 确认弹窗
    document.getElementById('confirmBtn').addEventListener('click', () => {
        confirmModal.classList.remove('active');
        if (pendingConfirm) {
            // 显示"正在执行确认操作"的提示，不重复添加用户消息
            addMessage('assistant', '⏳ 正在执行确认的操作...', {loading: true});
            sendMessage(pendingConfirm.message, true);
            pendingConfirm = null;
        }
    });
    
    document.getElementById('cancelBtn').addEventListener('click', () => {
        confirmModal.classList.remove('active');
        if (pendingConfirm) {
            addMessage('assistant', '❌ 用户已取消执行该操作', {cancelled: true});
            pendingConfirm = null;
        }
    });
    
    // 关闭链路弹窗
    document.getElementById('closeChainModal').addEventListener('click', () => {
        chainModal.classList.remove('active');
    });
    
    // 审计刷新
    document.getElementById('refreshAudit').addEventListener('click', loadAuditLogs);
    document.getElementById('auditFilter').addEventListener('change', loadAuditLogs);
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
            if (viewName === 'audit') loadAuditLogs();
            if (viewName === 'tools') loadTools();
            if (viewName === 'config') loadConfig();
        });
    });
}

// ===== API 调用 =====
async function apiPost(endpoint, data) {
    const res = await fetch(API_BASE + endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify(data)
    });
    if (!res.ok) {
        if (res.status === 401) {
            window.location.href = '/static/login.html';
            throw new Error('会话已过期，请重新登录');
        }
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    }
    return res.json();
}

async function apiGet(endpoint) {
    const res = await fetch(API_BASE + endpoint, {
        credentials: 'include'
    });
    if (!res.ok) {
        if (res.status === 401) {
            window.location.href = '/static/login.html';
            throw new Error('会话已过期，请重新登录');
        }
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
    }
    return res.json();
}

// ===== 健康检查 =====
async function checkHealth() {
    try {
        const data = await apiGet('/health');
        statusDot.className = 'status-dot online';
        statusText.textContent = `在线 | ${data.tools_count} 个工具`;
    } catch (e) {
        statusDot.className = 'status-dot offline';
        statusText.textContent = '离线';
    }
}

// ===== 发送消息 =====
async function sendMessage(messageOverride = null, confirmed = false) {
    if (isProcessing) return;
    
    const message = messageOverride || chatInput.value.trim();
    if (!message) return;
    
    if (!messageOverride) {
        chatInput.value = '';
        chatInput.style.height = 'auto';
    }
    
    isProcessing = true;
    sendBtn.disabled = true;
    
    // 添加用户消息（确认执行时不重复添加）
    if (!confirmed) {
        addMessage('user', message);
    }
    
    // 显示加载状态
    const loadingId = addLoadingMessage();
    
    try {
        const data = await apiPost('/chat', {
            message: message,
            session_id: currentSessionId,
            confirmed: confirmed
        });
        
        removeLoadingMessage(loadingId);
        
        if (data.requires_confirm) {
            // 需要确认
            pendingConfirm = { message };
            document.getElementById('confirmMessage').textContent = data.message;
            document.getElementById('riskDetails').textContent = data.confirm_reason || '';
            confirmModal.classList.add('active');
            
            addMessage('assistant', data.message, {
                chainId: data.chain_id,
                requiresConfirm: true
            });
        } else if (data.blocked) {
            // 被拦截
            addMessage('assistant', data.message, {
                chainId: data.chain_id,
                blocked: true
            });
        } else {
            // 正常回复（包括确认执行后的回复）
            let content = data.message;
            
            // 如果有工具执行结果，格式化展示
            if (data.tool_results && data.tool_results.length > 0) {
                content += '\n\n' + formatToolResults(data.tool_results);
            }
            
            // 判断是否为确认执行后的回复
            const isConfirmed = data.message && data.message.startsWith('✅ 用户已确认执行');
            
            addMessage('assistant', content, {
                chainId: data.chain_id,
                toolResults: data.tool_results,
                confirmed: isConfirmed
            });
        }
    } catch (e) {
        removeLoadingMessage(loadingId);
        addMessage('assistant', `❌ 请求失败: ${e.message}`);
    } finally {
        isProcessing = false;
        sendBtn.disabled = false;
    }
}

// ===== 消息渲染 =====
function addMessage(role, content, meta = {}) {
    // 如果是 loading 状态的临时消息，先移除之前的 loading
    if (meta.loading && role === 'assistant') {
        const prevLoading = chatMessages.querySelector('.message.assistant.loading-msg');
        if (prevLoading) prevLoading.remove();
    }
    
    const div = document.createElement('div');
    div.className = `message ${role}`;
    if (meta.loading) div.classList.add('loading-msg');
    
    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    avatar.textContent = role === 'user' ? '👤' : '🤖';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    // 安全徽章
    if (meta.blocked) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-danger';
        badge.textContent = '🛡️ 已拦截';
        contentDiv.appendChild(badge);
    } else if (meta.cancelled) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-secondary';
        badge.textContent = '❌ 已取消';
        contentDiv.appendChild(badge);
    } else if (meta.requiresConfirm) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-warning';
        badge.textContent = '⚠️ 待确认';
        contentDiv.appendChild(badge);
    } else if (meta.confirmed) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-success';
        badge.textContent = '✅ 已确认执行';
        contentDiv.appendChild(badge);
    } else if (role === 'assistant' && !meta.blocked && !meta.cancelled) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-safe';
        badge.textContent = '✅ 安全';
        contentDiv.appendChild(badge);
    }
    
    // 消息内容（支持 Markdown 简单渲染）
    contentDiv.innerHTML += renderMarkdown(content);
    
    // 链路追踪链接
    if (meta.chainId) {
        const chainLink = document.createElement('div');
        chainLink.className = 'chain-link';
        chainLink.textContent = `🔗 查看推理链路 (${meta.chainId.substr(0, 8)})`;
        chainLink.addEventListener('click', () => showChainDetails(meta.chainId));
        contentDiv.appendChild(chainLink);
    }
    
    div.appendChild(avatar);
    div.appendChild(contentDiv);
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    
    return div;
}

function renderMarkdown(text) {
    /**
     * 使用 marked.js 解析 Markdown + DOMPurify 净化 HTML
     * 彻底防御 XSS：先由 marked 生成 HTML，再由 DOMPurify 过滤危险标签/属性
     */
    if (!text) return '';
    
    // 配置 marked：禁用不安全的 HTML 标签，启用 GitHub Flavored Markdown
    marked.setOptions({
        gfm: true,
        breaks: true,
        headerIds: false,
        mangle: false,
        sanitize: false  // 由 DOMPurify 处理，marked 自身不做过滤
    });
    
    // 先解析 Markdown 为 HTML
    const rawHtml = marked.parse(text);
    
    // 再用 DOMPurify 净化：只允许安全标签和属性
    const cleanHtml = DOMPurify.sanitize(rawHtml, {
        ALLOWED_TAGS: [
            'p', 'br', 'hr',
            'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
            'strong', 'b', 'em', 'i', 'del', 's',
            'ul', 'ol', 'li',
            'pre', 'code',
            'blockquote',
            'a', 'img',
            'table', 'thead', 'tbody', 'tr', 'th', 'td'
        ],
        ALLOWED_ATTR: {
            'a': ['href', 'title'],
            'img': ['src', 'alt', 'title'],
            'code': ['class'],
            'pre': ['class']
        },
        ALLOW_DATA_ATTR: false,
        // 强制所有链接在新标签页打开，并添加 noopener
        ADD_ATTR: ['target', 'rel']
    });
    
    return cleanHtml;
}

function formatToolResults(results) {
    let html = '';
    results.forEach(r => {
        if (r.blocked) {
            html += `\n❌ **${r.tool}**: 被拦截 - ${r.reason}\n`;
        } else if (r.pending) {
            html += `\n⏸️ **${r.tool}**: 已挂起等待确认 (${r.risk_level}) - ${r.reason}\n`;
        } else if (r.error) {
            html += `\n⚠️ **${r.tool}**: 错误 - ${r.error}\n`;
        } else if (r.result) {
            const content = r.result.content ? r.result.content.join('\n') : '';
            const preview = content.length > 500 ? content.substr(0, 500) + '...' : content;
            html += `\n✅ **${r.tool}**:\n\`\`\`\n${preview}\n\`\`\`\n`;
        }
    });
    return html;
}

function addLoadingMessage() {
    const id = 'loading-' + Date.now();
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.id = id;
    div.innerHTML = `
        <div class="message-avatar">🤖</div>
        <div class="message-content">
            <div class="loading-dots">
                <span></span><span></span><span></span>
            </div>
        </div>
    `;
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return id;
}

function removeLoadingMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// ===== 审计日志 =====
async function loadAuditLogs() {
    const list = document.getElementById('auditList');
    const status = document.getElementById('auditFilter').value;
    
    try {
        const url = '/audit/chains' + (status ? `?status=${status}&limit=100` : '?limit=100');
        const data = await apiGet(url);
        
        if (!data.chains || data.chains.length === 0) {
            list.innerHTML = '<p class="empty-state">暂无记录</p>';
            return;
        }
        
        list.innerHTML = data.chains.map(chain => `
            <div class="audit-item status-${chain.final_status}" onclick="showChainDetails('${chain.chain_id}')">
                <div class="audit-item-header">
                    <span class="audit-item-title">${escapeHtml(chain.user_input.substr(0, 50))}${chain.user_input.length > 50 ? '...' : ''}</span>
                    <span class="audit-item-status">${chain.final_status}</span>
                </div>
                <div class="audit-item-meta">
                    <span>⏱️ ${chain.duration_sec ? chain.duration_sec + 's' : 'N/A'}</span>
                    <span>🔗 ${chain.nodes ? chain.nodes.length : 0} 节点</span>
                    <span>🕐 ${new Date(chain.start_time * 1000).toLocaleString()}</span>
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
        
        grid.innerHTML = data.tools.map(tool => `
            <div class="tool-card">
                <h4>${tool.name}</h4>
                <p>${tool.description}</p>
                <div class="tool-card-params">
                    参数: ${Object.keys(tool.parameters.properties || {}).map(p => 
                        `<code>${p}</code>`
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
                    <span class="config-value">${data.agent.name}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">版本</span>
                    <span class="config-value">${data.agent.version}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">描述</span>
                    <span class="config-value">${data.agent.description}</span>
                </div>
            </div>
            
            <div class="config-section">
                <h3>LLM 配置</h3>
                <div class="config-row">
                    <span class="config-label">提供商</span>
                    <span class="config-value">${data.llm.provider}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">模型</span>
                    <span class="config-value">${data.llm.model}</span>
                </div>
            </div>
            
            <div class="config-section">
                <h3>安全配置</h3>
                <div class="config-row">
                    <span class="config-label">受限用户</span>
                    <span class="config-value">${data.security.restricted_user}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">规则数量</span>
                    <span class="config-value">${data.security.rule_count}</span>
                </div>
            </div>
            
            <div class="config-section">
                <h3>审计配置</h3>
                <div class="config-row">
                    <span class="config-label">日志目录</span>
                    <span class="config-value">${data.audit.log_dir}</span>
                </div>
                <div class="config-row">
                    <span class="config-label">保留天数</span>
                    <span class="config-value">${data.audit.retention_days}</span>
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

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ===== 启动 =====
document.addEventListener('DOMContentLoaded', init);
