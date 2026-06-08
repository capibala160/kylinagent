// ===== 审批管理 =====
async function loadApprovals() {
    const container = document.getElementById('approvalsContent');
    if (!container) return;
    container.innerHTML = '<p class="empty-state">加载中...</p>';
    
    try {
        const data = await apiGet('/privilege/requests?status=pending&limit=100');
        renderApprovals(data.requests || []);
    } catch (e) {
        container.innerHTML = `<p class="empty-state">加载失败: ${escapeHtml(e.message)}</p>`;
    }
}

function renderApprovals(requests) {
    const container = document.getElementById('approvalsContent');
    if (!requests || requests.length === 0) {
        container.innerHTML = '<p class="empty-state">暂无待审批的权限申请</p>';
        return;
    }
    
    const now = Date.now() / 1000;
    let html = '<div class="approvals-list">';
    requests.forEach(req => {
        const isExpired = req.status === 'pending' && (now - req.created_at > 600);
        const statusClass = isExpired ? 'expired' : req.status;
        const statusText = isExpired ? '已过期' : (req.status === 'pending' ? '待审批' : req.status);
        const timeStr = new Date(req.created_at * 1000).toLocaleString();
        
        html += `
        <div class="approval-card ${statusClass}">
            <div class="approval-header">
                <span class="approval-id">#${escapeHtml(req.request_id || '')}</span>
                <span class="approval-status ${statusClass}">${escapeHtml(statusText)}</span>
            </div>
            <div class="approval-body">
                <p><strong>申请人:</strong> ${escapeHtml(req.requested_by || '')}</p>
                <p><strong>命令:</strong> <code>${escapeHtml(req.command || '')}</code></p>
                <p><strong>理由:</strong> ${escapeHtml(req.reason || '')}</p>
                <p><strong>申请时间:</strong> ${escapeHtml(timeStr)}</p>
            </div>
            ${req.status === 'pending' && !isExpired ? `
            <div class="approval-actions">
                <button class="btn-danger" onclick="approveRequest('${escapeHtml(req.request_id)}')">批准</button>
                <button class="btn-secondary" onclick="rejectRequest('${escapeHtml(req.request_id)}')">拒绝</button>
            </div>
            ` : ''}
        </div>
        `;
    });
    html += '</div>';
    container.innerHTML = html;
}

async function approveRequest(requestId) {
    try {
        const res = await apiPost(`/privilege/approve/${requestId}`, { approved: true });
        showNotification('审批成功', res.message || '权限申请已批准', 'success');
        loadApprovals();
    } catch (e) {
        showNotification('审批失败', e.message, 'error');
    }
}

async function rejectRequest(requestId) {
    try {
        const res = await apiPost(`/privilege/approve/${requestId}`, { approved: false });
        showNotification('审批成功', res.message || '权限申请已拒绝', 'info');
        loadApprovals();
    } catch (e) {
        showNotification('审批失败', e.message, 'error');
    }
}
