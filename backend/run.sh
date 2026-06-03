#!/bin/bash

# Kylin Safe Ops Agent 启动脚本（比赛环境适配版）
# 支持: 麒麟高级服务器版 V11 / LoongArch64

set -e

cd "$(dirname "$0")"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3"
    exit 1
fi

# 检测系统信息（用于调试）
echo "========================================"
echo "系统信息:"
python3 -c "import platform; print(f'  OS: {platform.system()} {platform.release()}')"
python3 -c "import platform; print(f'  Arch: {platform.machine()}')"
echo "  Python: $(python3 --version)"
echo "========================================"

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖（静默模式，减少输出）
echo "安装依赖..."
pip install -q -r requirements.txt

# 创建必要目录
mkdir -p logs/audit
mkdir -p data

# 检查并创建 opsagent 用户（如果不存在且不是 root）
if [ "$EUID" -eq 0 ] && ! id -u opsagent &>/dev/null; then
    echo "创建 opsagent 受限用户..."
    useradd -r -s /bin/bash -m opsagent 2>/dev/null || true
fi

# 检查是否有 LLM API 配置
if grep -q 'api_key: "sk-dummy"' ../config/agent.yaml 2>/dev/null; then
    echo ""
    echo "⚠️ 警告: config/agent.yaml 中 LLM API Key 为默认值"
    echo "   如需使用真实模型，请修改 ../config/agent.yaml"
    echo "   无模型时将自动回退到 Mock 模式（内置运维场景支持）"
    echo ""
fi

# 启动服务
echo "启动 Kylin Safe Ops Agent..."
echo "访问地址: http://localhost:8000"
echo "API 文档: http://localhost:8000/api/docs"
echo ""

# 生产环境建议去掉 --reload，使用如下命令：
# python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000
python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
