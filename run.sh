#!/bin/bash
# ============================================================================
# Vanna MCP Server 启动脚本
# ============================================================================
# 用法:
#   ./run.sh                 # 默认配置启动 (单 worker)
#   ./run.sh --workers 4     # 生产模式 (4 worker)
#   MCP_PORT=9000 ./run.sh   # 自定义端口
#
# 环境变量 (可通过 .env 文件或直接设置):
#   MCP_HOST          监听地址 (默认: 0.0.0.0)
#   MCP_PORT          监听端口 (默认: 19099)
#   MCP_WORKERS       worker 进程数 (默认: 1)
#   VANNA_MOCK_MODE   设为 1 启用模拟模式 (无 Vanna 实例, 用于接口测试)
#
#   DB_TYPE           数据库类型 (mysql|postgresql|highgo|dm)
#   DB_HOST           数据库主机
#   DB_PORT           数据库端口
#   DB_NAME           数据库名
#   DB_USER           数据库用户
#   DB_PASSWORD       数据库密码
#   LLM_MODEL         LLM 模型名 (默认: gpt-4)
#   OPENAI_API_KEY    OpenAI API Key
#   CHROMADB_PATH     ChromaDB 数据目录 (默认: ./chromadb_data)
# ============================================================================

set -euo pipefail

# 切换到脚本所在目录 (确保相对路径正确)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  Vanna MCP Server 启动脚本"
echo "=========================================="
echo "  工作目录: $SCRIPT_DIR"
echo "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

# 加载 .env 文件 (如果存在)
if [ -f .env ]; then
    echo "  [INFO] 从 .env 文件加载环境变量..."
    set -a
    source .env
    set +a
    echo "  [INFO] .env 已加载"
else
    echo "  [WARN] 未找到 .env 文件, 使用系统环境变量"
fi
echo ""

# 配置参数 (带默认值)
MCP_HOST="${MCP_HOST:-0.0.0.0}"
MCP_PORT="${MCP_PORT:-19099}"
MCP_WORKERS="${MCP_WORKERS:-1}"
VANNA_MOCK_MODE="${VANNA_MOCK_MODE:-0}"

# 解析命令行参数
while [[ $# -gt 0 ]]; do
    case $1 in
        --workers)
            MCP_WORKERS="$2"
            shift 2
            ;;
        --host)
            MCP_HOST="$2"
            shift 2
            ;;
        --port)
            MCP_PORT="$2"
            shift 2
            ;;
        --help|-h)
            echo "用法: ./run.sh [--workers N] [--host HOST] [--port PORT]"
            echo ""
            echo "选项:"
            echo "  --workers N    worker 进程数 (默认: 1)"
            echo "  --host HOST    监听地址 (默认: 0.0.0.0)"
            echo "  --port PORT    监听端口 (默认: 19099)"
            echo ""
            echo "环境变量:"
            echo "  MCP_HOST, MCP_PORT, MCP_WORKERS, VANNA_MOCK_MODE"
            echo "  DB_TYPE, DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD"
            echo "  LLM_MODEL, OPENAI_API_KEY, CHROMADB_PATH"
            exit 0
            ;;
        *)
            echo "[ERROR] 未知参数: $1"
            echo "使用 --help 查看用法"
            exit 1
            ;;
    esac
done

# 打印配置信息
echo "  [配置]"
echo "    监听地址:     $MCP_HOST"
echo "    监听端口:     $MCP_PORT"
echo "    Worker 数:    $MCP_WORKERS"
echo "    Mock 模式:    $VANNA_MOCK_MODE"
echo "    MCP 端点:     http://$MCP_HOST:$MCP_PORT/vanna/mcp"
if [ -n "${DB_TYPE:-}" ]; then
    echo "    数据库类型:   $DB_TYPE"
fi
if [ -n "${LLM_MODEL:-}" ]; then
    echo "    LLM 模型:     $LLM_MODEL"
fi
echo ""

# 检查 Python 环境
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] 未找到 python3, 请安装 Python 3.9+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "  [INFO] Python 版本: $PYTHON_VERSION"

# 检查关键依赖
check_dependency() {
    local pkg="$1"
    if python3 -c "import $pkg" 2>/dev/null; then
        echo "  [OK] $pkg 已安装"
    else
        echo "  [MISSING] $pkg 未安装 — 请运行: pip install $pkg"
        return 1
    fi
}

echo ""
echo "  [依赖检查]"
DEPS_OK=true
check_dependency mcp || DEPS_OK=false
check_dependency starlette || DEPS_OK=false
check_dependency uvicorn || DEPS_OK=false

if [ "$VANNA_MOCK_MODE" != "1" ]; then
    check_dependency vanna || DEPS_OK=false
    check_dependency pandas || DEPS_OK=false

    # 检查 vanna_instance.py 是否存在
    if [ -f vanna_instance.py ]; then
        echo "  [OK] vanna_instance.py 已找到"
    else
        echo "  [WARN] vanna_instance.py 未找到 — 请确保 backend-dev-1 已交付该文件"
        echo "         或设置 VANNA_MOCK_MODE=1 使用模拟模式"
    fi
else
    echo "  [INFO] Mock 模式 — 跳过 Vanna 依赖检查"
fi

if [ "$DEPS_OK" != "true" ]; then
    echo ""
    echo "  [ERROR] 依赖检查未通过, 请安装缺失的依赖后重试"
    echo "          pip install -r requirements.txt"
    exit 1
fi

echo ""
echo "=========================================="
echo "  启动 Vanna MCP Server..."
echo "=========================================="
echo ""

# 启动 uvicorn
# 使用 exec 替换当前进程, 确保信号正确传递
if [ "$MCP_WORKERS" -gt 1 ]; then
    echo "  [INFO] 多 Worker 模式 ($MCP_WORKERS workers)"
    exec uvicorn server:app \
        --host "$MCP_HOST" \
        --port "$MCP_PORT" \
        --workers "$MCP_WORKERS" \
        --log-level info
else
    echo "  [INFO] 单 Worker 模式"
    exec python3 server.py
fi
