// ===== API 调用封装 =====

const API_BASE = window.location.origin + '/api';

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

async function checkHealth() {
    try {
        const data = await apiGet('/health');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        if (statusDot) {
            statusDot.className = 'status-dot online';
            statusText.textContent = `在线 | ${data.tools_count} 个工具`;
        }
        // 同步全局网络状态，确保离线横幅和重连指示器被隐藏
        if (typeof isOnline !== 'undefined') {
            isOnline = true;
        }
        const offlineBanner = document.getElementById('offlineBanner');
        offlineBanner?.classList.add('hidden');
        document.querySelector('.chat-input-area')?.classList.remove('offline');
        const reconnectingIndicator = document.getElementById('reconnectingIndicator');
        reconnectingIndicator?.classList.remove('active');
    } catch (e) {
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        if (statusDot) {
            statusDot.className = 'status-dot offline';
            statusText.textContent = '离线';
        }
        if (typeof isOnline !== 'undefined') {
            isOnline = false;
        }
    }
}
