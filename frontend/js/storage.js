// ===== StorageManager：封装 localStorage，减少散落各处的直接访问 =====

const StorageManager = {
    get(key, defaultValue = null) {
        try {
            const val = localStorage.getItem(key);
            return val !== null ? JSON.parse(val) : defaultValue;
        } catch {
            return defaultValue;
        }
    },

    set(key, value) {
        localStorage.setItem(key, JSON.stringify(value));
    },

    remove(key) {
        localStorage.removeItem(key);
    },

    getString(key, defaultValue = '') {
        return localStorage.getItem(key) || defaultValue;
    },

    setString(key, value) {
        localStorage.setItem(key, value);
    },

    // 会话相关
    getSessionId() {
        return this.getString('ops_session_id', '');
    },

    setSessionId(id) {
        this.setString('ops_session_id', id);
    },

    removeSessionId() {
        this.remove('ops_session_id');
    },

    getSessions() {
        return this.get('sessions', []);
    },

    setSessions(sessions) {
        this.set('sessions', sessions);
    },

    getFolders() {
        return this.get('sessionFolders', []);
    },

    setFolders(folders) {
        this.set('sessionFolders', folders);
    },

    // 主题
    getTheme() {
        return this.getString('theme', 'light');
    },

    setTheme(theme) {
        this.setString('theme', theme);
    },

    // 草稿
    getChatDraft() {
        return this.getString('chatDraft', '');
    },

    setChatDraft(draft) {
        this.setString('chatDraft', draft);
    },

    removeChatDraft() {
        this.remove('chatDraft');
    },

    // 命令模板
    getCommandTemplates() {
        return this.get('commandTemplates', []);
    },

    setCommandTemplates(templates) {
        this.set('commandTemplates', templates);
    },

    // 搜索历史
    getSearchHistory() {
        return this.get('searchHistory', []);
    },

    setSearchHistory(history) {
        this.set('searchHistory', history);
    },

    // 最近表情
    getRecentEmojis() {
        return this.get('recentEmojis', []);
    },

    setRecentEmojis(emojis) {
        this.set('recentEmojis', emojis);
    },

    // 设置
    getSettings() {
        return this.get('appSettings', {});
    },

    setSettings(settings) {
        this.set('appSettings', settings);
    },

    // 音效/通知开关
    getSoundEnabled() {
        return this.getString('soundEnabled', 'false') === 'true';
    },

    setSoundEnabled(enabled) {
        this.setString('soundEnabled', String(enabled));
    },

    getBrowserNotificationsEnabled() {
        return this.getString('browserNotificationsEnabled', 'false') === 'true';
    },

    setBrowserNotificationsEnabled(enabled) {
        this.setString('browserNotificationsEnabled', String(enabled));
    },

    // 离线数据
    getOfflineData() {
        return this.get('ops_offline_data', null);
    },

    setOfflineData(data) {
        this.set('ops_offline_data', data);
    },

    // 新手引导
    hasSeenOnboarding() {
        return this.getString('hasSeenOnboarding', '') === 'true';
    },

    setHasSeenOnboarding() {
        this.setString('hasSeenOnboarding', 'true');
    },
};
