#!/bin/bash

# Kylin Safe Ops Agent 启动脚本

set -e

cd "$(dirname "$0")"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3"
    exit 1
fi

# 创建虚拟环境（如果不存在）
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "安装依赖..."
pip install -q -r requirements.txt

# 创建日志目录
mkdir -p logs/audit

# 启动服务
echo "启动 Kylin Safe Ops Agent..."
echo "访问地址: http://localhost:8000"
echo "API 文档: http://localhost:8000/api/docs"

python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
