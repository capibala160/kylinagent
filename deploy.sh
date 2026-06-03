#!/bin/bash

# ============================================================================
# Kylin Safe Ops Agent —— 一键部署脚本（比赛环境专用）
# 适配: 麒麟高级服务器版 V11 (kylin v10sp3 兼容) / LoongArch64
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_NAME="kylin-ops-agent"
BACKEND_DIR="$SCRIPT_DIR/backend"
CONFIG_FILE="$SCRIPT_DIR/config/agent.yaml"

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info()  { echo -e "${GREEN}[INFO]${NC}  $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ============================================================================
# 前置检查
# ============================================================================

check_env() {
    log_info "检查运行环境..."
    
    # Python 版本
    if ! command -v python3 &> /dev/null; then
        log_error "未找到 python3，请安装 Python 3.9+"
        exit 1
    fi
    
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    log_info "Python 版本: $PYTHON_VERSION"
    
    # 系统架构
    ARCH=$(uname -m)
    log_info "系统架构: $ARCH"
    
    # 操作系统
    if [ -f /etc/os-release ]; then
        OS_NAME=$(grep -oP '(?<=^NAME=).+' /etc/os-release | tr -d '"')
        OS_VERSION=$(grep -oP '(?<=^VERSION=).+' /etc/os-release | tr -d '"')
        log_info "操作系统: $OS_NAME $OS_VERSION"
    fi
    
    # 检查必要系统命令
    for cmd in ps df free uptime ss ip systemctl; do
        if ! command -v $cmd &> /dev/null; then
            log_warn "系统命令 $cmd 可能未安装，部分功能将不可用"
        fi
    done
    
    # 可选工具提示
    for cmd in lsof iostat sysstat; do
        if ! command -v $cmd &> /dev/null; then
            log_warn "$cmd 未安装，建议安装以增强功能: yum install -y $cmd"
        fi
    done
}

# ============================================================================
# 安装后端依赖
# ============================================================================

install_backend() {
    log_info "安装后端依赖..."
    cd "$BACKEND_DIR"
    
    # 创建虚拟环境
    if [ ! -d "venv" ]; then
        python3 -m venv venv
    fi
    
    source venv/bin/activate
    
    # 升级 pip
    pip install --upgrade pip -q
    
    # 安装依赖
    pip install -r requirements.txt -q
    
    # 创建必要目录
    mkdir -p logs/audit
    mkdir -p data
    
    log_info "后端依赖安装完成"
}

# ============================================================================
# 配置检查与建议
# ============================================================================

check_config() {
    log_info "检查配置文件..."
    
    if [ ! -f "$CONFIG_FILE" ]; then
        log_error "配置文件不存在: $CONFIG_FILE"
        exit 1
    fi
    
    # 检查 LLM 配置
    if grep -q 'api_key: "sk-dummy"' "$CONFIG_FILE"; then
        log_warn "LLM API Key 为默认值，将使用 Mock 模式运行"
        log_warn "Mock 模式支持: ps/df/free/内存/CPU/网络/进程/日志等运维场景"
    fi
    
    # 检查受限用户
    RESTRICTED_USER=$(grep -oP '(?<=restricted_user: ").*(?=")' "$CONFIG_FILE" || echo "opsagent")
    if ! id -u "$RESTRICTED_USER" &>/dev/null; then
        log_warn "受限用户 $RESTRICTED_USER 不存在，安全执行器将降级为当前用户运行"
    fi
}

# ============================================================================
# 启动服务
# ============================================================================

start_service() {
    log_info "启动服务..."
    cd "$BACKEND_DIR"
    source venv/bin/activate
    
    # 检查端口占用
    if ss -tlnp | grep -q ':8000 '; then
        log_warn "端口 8000 已被占用，尝试查找占用进程..."
        ss -tlnp | grep ':8000 '
        log_error "请先释放端口 8000 后再启动"
        exit 1
    fi
    
    # 后台启动
    nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 > logs/server.log 2>&1 &
    PID=$!
    
    # 等待启动
    log_info "等待服务启动 (PID: $PID)..."
    for i in {1..15}; do
        if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
            log_info "✅ 服务启动成功!"
            log_info "访问地址: http://localhost:8000"
            log_info "API 文档: http://localhost:8000/api/docs"
            log_info "日志文件: $BACKEND_DIR/logs/server.log"
            echo "$PID" > logs/server.pid
            return 0
        fi
        sleep 1
    done
    
    log_error "服务启动超时，请检查日志: $BACKEND_DIR/logs/server.log"
    tail -n 30 logs/server.log
    exit 1
}

# ============================================================================
# systemd 服务安装（可选，需要 root）
# ============================================================================

install_systemd() {
    if [ "$EUID" -ne 0 ]; then
        log_warn "非 root 用户，跳过 systemd 服务安装"
        return 0
    fi
    
    log_info "安装 systemd 服务..."
    
    cat > /etc/systemd/system/kylin-ops-agent.service <<EOF
[Unit]
Description=Kylin Safe Ops Agent
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$BACKEND_DIR
Environment="PATH=$BACKEND_DIR/venv/bin"
Environment="CONFIG_PATH=$SCRIPT_DIR/config/agent.yaml"
ExecStart=$BACKEND_DIR/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    
    systemctl daemon-reload
    systemctl enable kylin-ops-agent
    log_info "systemd 服务已安装，可使用 systemctl start kylin-ops-agent 启动"
}

# ============================================================================
# 停止服务
# ============================================================================

stop_service() {
    log_info "停止现有服务..."
    
    # 通过 PID 文件停止
    if [ -f "$BACKEND_DIR/logs/server.pid" ]; then
        PID=$(cat "$BACKEND_DIR/logs/server.pid")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null || true
            sleep 2
        fi
        rm -f "$BACKEND_DIR/logs/server.pid"
    fi
    
    # 通过端口查找停止
    PIDS=$(ss -tlnp | grep ':8000 ' | grep -oP 'pid=\K[0-9]+' | sort -u)
    for pid in $PIDS; do
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            log_warn "终止占用 8000 端口的进程 $pid"
            kill "$pid" 2>/dev/null || true
        fi
    done
}

# ============================================================================
# 健康检查
# ============================================================================

health_check() {
    log_info "执行健康检查..."
    
    RESPONSE=$(curl -sf http://localhost:8000/api/health 2>/dev/null || echo "")
    if [ -n "$RESPONSE" ]; then
        log_info "健康检查通过:"
        echo "$RESPONSE" | python3 -m json.tool 2>/dev/null || echo "$RESPONSE"
    else
        log_warn "健康检查失败，服务可能未完全启动"
    fi
}

# ============================================================================
# 主流程
# ============================================================================

case "${1:-all}" in
    install)
        check_env
        install_backend
        check_config
        ;;
    start)
        stop_service
        start_service
        sleep 2
        health_check
        ;;
    systemd)
        install_systemd
        systemctl start kylin-ops-agent
        ;;
    stop)
        stop_service
        ;;
    restart)
        stop_service
        start_service
        sleep 2
        health_check
        ;;
    all|*)
        check_env
        install_backend
        check_config
        stop_service
        start_service
        sleep 2
        health_check
        ;;
esac

log_info "部署完成!"
