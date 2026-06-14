async function sendMessage(messageOverride = null, confirmed = false, elevated = false) {
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
        const response = await fetch(API_BASE + '/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
                message: message,
                session_id: currentSessionId,
                confirmed: confirmed,
                elevated: elevated
            })
        });
        
        if (!response.ok) {
            if (response.status === 401) {
                window.location.href = '/static/login.html';
                return;
            }
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }
        
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let streamingDiv = null;
        let streamingContentDiv = null;
        let accumulatedContent = '';
        let chainId = '';
        let toolResults = [];
        
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split('\n\n');
            buffer = parts.pop(); // 保留不完整的最后部分
            
            for (const part of parts) {
                if (!part.trim()) continue;
                const lines = part.split('\n');
                let eventName = 'message';
                let dataStr = '';
                
                for (const line of lines) {
                    const trimmed = line.trim();
                    if (trimmed.startsWith('event:')) {
                        eventName = trimmed.slice(6).trim();
                    } else if (trimmed.startsWith('data:')) {
                        dataStr = trimmed.slice(5).trim();
                    }
                }
                
                let data = {};
                if (dataStr) {
                    try { data = JSON.parse(dataStr); } catch (e) { data = { raw: dataStr }; }
                }
                
                if (eventName === 'status') {
                    if (data.message) {
                        const loadingEl = document.getElementById(loadingId);
                        if (loadingEl) {
                            const contentEl = loadingEl.querySelector('.message-content');
                            if (contentEl) {
                                contentEl.innerHTML = `<div class="loading-dots"><span></span><span></span><span></span></div><div style="margin-top:4px;font-size:12px;color:#888">${escapeHtml(data.message)}</div>`;
                            }
                        }
                    }
                } else if (eventName === 'blocked') {
                    removeLoadingMessage(loadingId);
                    if (streamingDiv) { streamingDiv.remove(); streamingDiv = null; }
                    addMessage('assistant', data.message || '操作被拦截', {
                        chainId: data.chain_id,
                        blocked: true
                    });
                    isProcessing = false;
                    sendBtn.disabled = false;
                    return;
                } else if (eventName === 'confirm') {
                    removeLoadingMessage(loadingId);
                    if (streamingDiv) { streamingDiv.remove(); streamingDiv = null; }
                    pendingConfirm = { message };
                    document.getElementById('confirmMessage').textContent = data.message || '需要确认';
                    const riskDetails = document.getElementById('riskDetails');
                    if (data.reason) {
                        riskDetails.textContent = data.reason;
                        riskDetails.style.display = 'block';
                    } else {
                        riskDetails.style.display = 'none';
                    }
                    confirmModal.classList.add('active');
                    addMessage('assistant', data.message || '需要确认', {
                        chainId: data.chain_id,
                        requiresConfirm: true
                    });
                    isProcessing = false;
                    sendBtn.disabled = false;
                    return;
                } else if (eventName === 'privilege') {
                    removeLoadingMessage(loadingId);
                    if (streamingDiv) { streamingDiv.remove(); streamingDiv = null; }
                    pendingPrivilege = {
                        message,
                        command: data.command,
                        tool_name: data.tool_name || '',
                        arguments: data.arguments || {}
                    };
                    document.getElementById('privilegeMessage').textContent = data.message || '需要 root 权限';
                    document.getElementById('privilegeCommand').textContent = data.command || '';
                    document.getElementById('privilegeReason').value = '';
                    privilegeModal.classList.add('active');
                    addMessage('assistant', data.message || '需要 root 权限', {
                        chainId: data.chain_id,
                        requiresPrivilege: true
                    });
                    isProcessing = false;
                    sendBtn.disabled = false;
                    return;
                } else if (eventName === 'message') {
                    if (!streamingDiv) {
                        removeLoadingMessage(loadingId);
                        streamingDiv = document.createElement('div');
                        streamingDiv.className = 'message assistant streaming-msg';
                        streamingDiv.innerHTML = '<div class="message-avatar"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="9" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1" fill="currentColor" stroke="none"/><line x1="8" y1="15" x2="16" y2="15"/></svg></div><div class="message-content"></div>';
                        chatMessages.appendChild(streamingDiv);
                        streamingContentDiv = streamingDiv.querySelector('.message-content');
                    }
                    accumulatedContent += data.token || '';
                    if (streamingContentDiv) {
                        streamingContentDiv.innerHTML = renderMarkdown(accumulatedContent);
                    }
                    chatMessages.scrollTop = chatMessages.scrollHeight;
                } else if (eventName === 'tool_result') {
                    toolResults.push(data);
                } else if (eventName === 'done') {
                    removeLoadingMessage(loadingId);
                    if (streamingDiv) {
                        streamingDiv.remove();
                        streamingDiv = null;
                    }
                    chainId = data.chain_id || chainId;
                    
                    let finalContent = accumulatedContent;
                    if (toolResults.length > 0) {
                        finalContent += '\n\n' + formatToolResults(toolResults);
                    }
                    
                    const isConfirmed = finalContent && finalContent.startsWith('✅ 用户已确认执行');
                    
                    addMessage('assistant', finalContent || '执行完成', {
                        chainId: chainId,
                        toolResults: toolResults,
                        confirmed: isConfirmed
                    });
                    isProcessing = false;
                    sendBtn.disabled = false;
                    return;
                } else if (eventName === 'error') {
                    removeLoadingMessage(loadingId);
                    if (streamingDiv) { streamingDiv.remove(); streamingDiv = null; }
                    addMessage('assistant', `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> 错误: ${escapeHtml(data.message || '未知错误')}`);
                    isProcessing = false;
                    sendBtn.disabled = false;
                    return;
                }
            }
        }
    } catch (e) {
        removeLoadingMessage(loadingId);
        addMessage('assistant', `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> 请求失败: ${escapeHtml(e.message)}`);
    } finally {
        isProcessing = false;
        sendBtn.disabled = false;
    }
}


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
    avatar.innerHTML = role === 'user' ? '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>' : '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="9" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1" fill="currentColor" stroke="none"/><line x1="8" y1="15" x2="16" y2="15"/></svg>';
    
    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';
    
    // 安全徽章
    if (meta.blocked) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-danger';
        badge.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg> 已拦截';
        contentDiv.appendChild(badge);
    } else if (meta.cancelled) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-secondary';
        badge.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> 已取消';
        contentDiv.appendChild(badge);
    } else if (meta.requiresConfirm) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-warning';
        badge.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> 待确认';
        contentDiv.appendChild(badge);
    } else if (meta.confirmed) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-success';
        badge.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> 已确认执行';
        contentDiv.appendChild(badge);
    } else if (role === 'assistant' && !meta.blocked && !meta.cancelled) {
        const badge = document.createElement('div');
        badge.className = 'security-badge badge-safe';
        badge.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> 安全';
        contentDiv.appendChild(badge);
    }
    
    // 消息内容（支持 Markdown 简单渲染）
    // 使用 insertAdjacentHTML 避免 DOM 重解析导致已有子节点事件丢失
    contentDiv.insertAdjacentHTML('beforeend', renderMarkdown(content));
    
    // 链路追踪链接
    if (meta.chainId) {
        const chainLink = document.createElement('div');
        chainLink.className = 'chain-link';
        chainLink.innerHTML = '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg> 查看推理链路 (${meta.chainId.substr(0, 8)})';
        chainLink.addEventListener('click', () => showChainDetails(meta.chainId));
        contentDiv.appendChild(chainLink);
    }
    
    div.appendChild(avatar);
    div.appendChild(contentDiv);
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    
    // 增强：为代码块添加复制按钮
    contentDiv.querySelectorAll('pre').forEach(pre => {
        pre.style.position = 'relative';
        const copyBtn = document.createElement('button');
        copyBtn.className = 'code-copy-btn';
        copyBtn.textContent = '复制';
        copyBtn.addEventListener('click', () => {
            const code = pre.querySelector('code');
            const text = code ? code.textContent : pre.textContent;
            navigator.clipboard.writeText(text).then(() => {
                copyBtn.textContent = '已复制';
                setTimeout(() => copyBtn.textContent = '复制', 1500);
            }).catch(() => {
                const ta = document.createElement('textarea');
                ta.value = text;
                document.body.appendChild(ta);
                ta.select();
                document.execCommand('copy');
                document.body.removeChild(ta);
                copyBtn.textContent = '已复制';
                setTimeout(() => copyBtn.textContent = '复制', 1500);
            });
        });
        pre.appendChild(copyBtn);
    });
    
    // 增强：超长消息折叠
    const maxMsgHeight = 400;
    if (contentDiv.scrollHeight > maxMsgHeight && !meta.loading) {
        contentDiv.classList.add('collapsed');
        const expandBtn = document.createElement('button');
        expandBtn.className = 'msg-expand-btn';
        expandBtn.textContent = '展开更多';
        expandBtn.addEventListener('click', () => {
            contentDiv.classList.remove('collapsed');
            expandBtn.remove();
            chatMessages.scrollTop = chatMessages.scrollHeight;
        });
        div.appendChild(expandBtn);
    }
    
    // 增强：图片加载完成后重新滚动
    contentDiv.querySelectorAll('img').forEach(img => {
        img.addEventListener('load', () => {
            chatMessages.scrollTop = chatMessages.scrollHeight;
        });
    });
    
    return div;
}


// 配置 marked：禁用不安全的 HTML 标签，启用 GitHub Flavored Markdown
// 仅在模块初始化时配置一次，避免重复调用
marked.setOptions({
    gfm: true,
    breaks: true,
    headerIds: false,
    mangle: false,
    sanitize: false  // 由 DOMPurify 处理，marked 自身不做过滤
});

function renderMarkdown(text) {
    /**
     * 使用 marked.js 解析 Markdown + DOMPurify 净化 HTML
     * 彻底防御 XSS：先由 marked 生成 HTML，再由 DOMPurify 过滤危险标签/属性
     */
    if (!text || typeof text !== 'string') return '';
    
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
            'table', 'thead', 'tbody', 'tr', 'th', 'td',
            'svg', 'path', 'circle', 'line', 'polyline', 'rect'
        ],
        ALLOWED_ATTR: {
            'a': ['href', 'title'],
            'img': ['src', 'alt', 'title'],
            'code': ['class'],
            'pre': ['class'],
            'svg': ['class', 'viewBox', 'fill', 'stroke', 'stroke-width', 'xmlns'],
            'path': ['d', 'fill', 'stroke', 'stroke-width'],
            'circle': ['cx', 'cy', 'r', 'fill', 'stroke', 'stroke-width'],
            'line': ['x1', 'y1', 'x2', 'y2', 'fill', 'stroke', 'stroke-width'],
            'polyline': ['points', 'fill', 'stroke', 'stroke-width'],
            'rect': ['x', 'y', 'width', 'height', 'rx', 'ry', 'fill', 'stroke', 'stroke-width']
        },
        ALLOW_DATA_ATTR: false,
        // 强制所有链接在新标签页打开，并添加 noopener
        ADD_ATTR: ['target', 'rel']
    });
    
    return cleanHtml;
}


function formatToolResults(results) {
    let html = '';
    if (!Array.isArray(results)) return html;
    results.forEach(r => {
        if (!r || typeof r !== 'object') return;
        if (r.blocked) {
            html += `\n<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> **${r.tool || '未知工具'}**: 被拦截 - ${r.reason || ''}\n`;
        } else if (r.pending) {
            html += `\n<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg> **${r.tool || '未知工具'}**: 已挂起等待确认 (${r.risk_level || ''}) - ${r.reason || ''}\n`;
        } else if (r.error) {
            html += `\n<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg> **${r.tool || '未知工具'}**: 错误 - ${r.error}\n`;
        } else if (r.result && typeof r.result === 'object') {
            const content = Array.isArray(r.result.content) ? r.result.content.join('\n') : '';
            const preview = content.length > 500 ? content.substr(0, 500) + '...' : content;
            html += `\n<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 11-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg> **${r.tool || '未知工具'}**:\n\`\`\`\n${preview}\n\`\`\`\n`;
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
        <div class="message-avatar"><svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="4" y="4" width="16" height="16" rx="4"/><circle cx="9" cy="10" r="1" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1" fill="currentColor" stroke="none"/><line x1="8" y1="15" x2="16" y2="15"/></svg></div>
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


async function submitPrivilegeRequest() {
    const reason = document.getElementById('privilegeReason').value.trim();
    if (!reason || reason.length < 5) {
        alert('申请理由至少5个字');
        return;
    }
    
    if (!pendingPrivilege) return;
    
    document.getElementById('submitPrivilegeBtn').disabled = true;
    document.getElementById('submitPrivilegeBtn').textContent = '提交中...';
    
    try {
        // 1. 提交权限申请
        const reqRes = await apiPost('/privilege/request', {
            session_id: currentSessionId,
            command: pendingPrivilege.command || '',
            reason: reason,
            tool_name: pendingPrivilege.tool_name || undefined,
            arguments: pendingPrivilege.arguments || undefined
        });
        
        if (!reqRes.success) {
            throw new Error(reqRes.message || '申请提交失败');
        }
        
        const requestId = reqRes.request_id;
        privilegeModal.classList.remove('active');
        addMessage('assistant', `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="4"/><polyline points="12 7 12 12 15 15"/></svg> 权限申请已提交 (${escapeHtml(requestId)})\n理由：${escapeHtml(reason)}\n\n请等待管理员审批。`, {loading: false});
        
    } catch (e) {
        addMessage('assistant', `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg> 权限申请失败: ${escapeHtml(e.message)}`);
        privilegeModal.classList.remove('active');
    } finally {
        document.getElementById('submitPrivilegeBtn').disabled = false;
        document.getElementById('submitPrivilegeBtn').textContent = '提交申请';
    }
}


async function executeWithElevation() {
    if (!pendingPrivilege) return;
    await sendMessage(pendingPrivilege.message, false, true);
    pendingPrivilege = null;
}


