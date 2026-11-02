#!/bin/bash
# ============================================================================
# AMA Web 服务管理脚本
# 用法: ./web.sh [start|stop|restart|status]
# ============================================================================

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 配置（可在启动时通过环境变量覆盖）
BACKEND_PORT="${BACKEND_PORT:-8011}"
FRONTEND_PORT="${FRONTEND_PORT:-8012}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
BACKEND_URL="${BACKEND_URL:-http://localhost:$BACKEND_PORT}"
CONDA_ENV="ama"
PID_DIR="$SCRIPT_DIR/logs"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
BATCH_WORKER_PID_FILE="$PID_DIR/batch-worker.pid"
MCP_WORKER_PID_FILE="$PID_DIR/mcp-worker.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 创建 PID 目录
mkdir -p "$PID_DIR"

# 打印带颜色的消息
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查端口是否被占用
check_port() {
    local port=$1
    lsof -ti:$port > /dev/null 2>&1
}

# 杀死占用端口的进程
kill_port() {
    local port=$1
    local pids=$(lsof -ti:$port 2>/dev/null)
    if [ -n "$pids" ]; then
        echo "$pids" | xargs kill -9 2>/dev/null
        return 0
    fi
    return 1
}

# 等待服务启动 (通过健康检查)
# 参数: $1=端口, $2=服务名, $3=健康检查路径(可选), $4=最大等待秒数(默认30)
wait_for_service() {
    local port=$1
    local name=$2
    local health_path=${3:-"/"}
    local max_wait=${4:-30}
    local interval=1
    local elapsed=0
    
    echo -ne "  等待 $name 启动中"
    
    while [ $elapsed -lt $max_wait ]; do
        # 首先检查端口是否已监听
        if check_port $port; then
            # 尝试 HTTP 健康检查
            local http_code=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 2 --max-time 3 "http://localhost:$port$health_path" 2>/dev/null)
            if [ "$http_code" = "200" ] || [ "$http_code" = "404" ] || [ "$http_code" = "307" ]; then
                echo -e " ${GREEN}✓${NC}"
                return 0
            fi
        fi
        
        echo -n "."
        sleep $interval
        elapsed=$((elapsed + interval))
    done
    
    echo -e " ${RED}✗${NC}"
    return 1
}

# 启动后端服务
start_backend() {
    info "启动后端服务 (端口: $BACKEND_PORT)..."
    
    if check_port $BACKEND_PORT; then
        warn "端口 $BACKEND_PORT 已被占用，正在释放..."
        kill_port $BACKEND_PORT
        sleep 1
    fi
    
    # 激活 conda 环境并启动后端
    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate $CONDA_ENV
    
    nohup uvicorn web.backend.app.main:app --host 0.0.0.0 --port $BACKEND_PORT > "$PID_DIR/backend.log" 2>&1 &
    echo $! > "$BACKEND_PID_FILE"
    
    # 使用健康检查等待服务启动 (最多等待30秒)
    if wait_for_service $BACKEND_PORT "后端服务" "/health" 30; then
        success "后端服务已启动 → http://localhost:$BACKEND_PORT"
    else
        error "后端服务启动超时，请查看日志: $PID_DIR/backend.log"
        # 显示最后几行日志帮助诊断
        echo "  最近日志:"
        tail -5 "$PID_DIR/backend.log" 2>/dev/null | sed 's/^/    /'
        return 1
    fi
}

# 启动批量任务 Worker
start_batch_worker() {
    info "启动批量任务 Worker..."

    if [ -f "$BATCH_WORKER_PID_FILE" ]; then
        local existing_pid=$(cat "$BATCH_WORKER_PID_FILE" 2>/dev/null)
        if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
            warn "批量任务 Worker 已在运行 (PID: $existing_pid)"
            return 0
        fi
        rm -f "$BATCH_WORKER_PID_FILE"
    fi

    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate $CONDA_ENV

    nohup python -m web.backend.app.batch_worker > "$PID_DIR/batch-worker.log" 2>&1 &
    echo $! > "$BATCH_WORKER_PID_FILE"
    sleep 1

    local worker_pid=$(cat "$BATCH_WORKER_PID_FILE" 2>/dev/null)
    if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
        success "批量任务 Worker 已启动 (PID: $worker_pid)"
    else
        error "批量任务 Worker 启动失败，请查看日志: $PID_DIR/batch-worker.log"
        echo "  最近日志:"
        tail -5 "$PID_DIR/batch-worker.log" 2>/dev/null | sed 's/^/    /'
        return 1
    fi
}

# 启动 MCP 单 PDF 任务 Worker
start_mcp_worker() {
    local mcp_enabled="false"
    if [ -f "$SCRIPT_DIR/.env" ]; then
        mcp_enabled=$(grep -E '^AMA_MCP_ENABLED=' "$SCRIPT_DIR/.env" | tail -1 | cut -d '=' -f2 | tr -d '"' | tr -d "'" | tr '[:upper:]' '[:lower:]' | tr -d '\r' || true)
    fi
    if [ "$mcp_enabled" != "true" ]; then
        info "AMA MCP 未启用，跳过 MCP Worker"
        return 0
    fi

    info "启动 MCP Worker..."
    if [ -f "$MCP_WORKER_PID_FILE" ]; then
        local existing_pid=$(cat "$MCP_WORKER_PID_FILE" 2>/dev/null)
        if [ -n "$existing_pid" ] && kill -0 "$existing_pid" 2>/dev/null; then
            warn "MCP Worker 已在运行 (PID: $existing_pid)"
            return 0
        fi
        rm -f "$MCP_WORKER_PID_FILE"
    fi

    source "$(conda info --base)/etc/profile.d/conda.sh"
    conda activate $CONDA_ENV
    nohup python mcp/run_worker.py > "$PID_DIR/mcp-worker.log" 2>&1 &
    echo $! > "$MCP_WORKER_PID_FILE"
    sleep 1

    local worker_pid=$(cat "$MCP_WORKER_PID_FILE" 2>/dev/null)
    if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
        success "MCP Worker 已启动 (PID: $worker_pid)"
    else
        error "MCP Worker 启动失败，请查看日志: $PID_DIR/mcp-worker.log"
        return 1
    fi
}

# 启动前端服务
start_frontend() {
    info "启动前端服务 (端口: $FRONTEND_PORT)..."
    info "前端代理后端 → $BACKEND_URL"
    
    if check_port $FRONTEND_PORT; then
        warn "端口 $FRONTEND_PORT 已被占用，正在释放..."
        kill_port $FRONTEND_PORT
        sleep 1
    fi
    
    cd "$SCRIPT_DIR/web/frontend"
    nohup env VITE_BACKEND_URL="$BACKEND_URL" VITE_FRONTEND_PORT="$FRONTEND_PORT" \
        npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort > "$PID_DIR/frontend.log" 2>&1 &
    echo $! > "$FRONTEND_PID_FILE"
    cd "$SCRIPT_DIR"
    
    # 使用健康检查等待服务启动 (最多等待30秒)
    if wait_for_service $FRONTEND_PORT "前端服务" "/ama/" 30; then
        success "前端服务已启动 → http://localhost:$FRONTEND_PORT/ama/"
    else
        error "前端服务启动超时，请查看日志: $PID_DIR/frontend.log"
        echo "  最近日志:"
        tail -5 "$PID_DIR/frontend.log" 2>/dev/null | sed 's/^/    /'
        return 1
    fi
}

# 停止后端服务
stop_backend() {
    info "停止后端服务..."
    
    if [ -f "$BACKEND_PID_FILE" ]; then
        kill -9 $(cat "$BACKEND_PID_FILE") 2>/dev/null
        rm -f "$BACKEND_PID_FILE"
    fi
    
    kill_port $BACKEND_PORT
    success "后端服务已停止"
}

# 停止批量任务 Worker
stop_batch_worker() {
    info "停止批量任务 Worker..."

    if [ -f "$BATCH_WORKER_PID_FILE" ]; then
        local worker_pid=$(cat "$BATCH_WORKER_PID_FILE" 2>/dev/null)
        if [ -n "$worker_pid" ]; then
            kill "$worker_pid" 2>/dev/null
            sleep 1
            if kill -0 "$worker_pid" 2>/dev/null; then
                kill -9 "$worker_pid" 2>/dev/null
            fi
        fi
        rm -f "$BATCH_WORKER_PID_FILE"
    fi

    success "批量任务 Worker 已停止"
}

# 停止 MCP Worker
stop_mcp_worker() {
    info "停止 MCP Worker..."
    if [ -f "$MCP_WORKER_PID_FILE" ]; then
        local worker_pid=$(cat "$MCP_WORKER_PID_FILE" 2>/dev/null)
        if [ -n "$worker_pid" ]; then
            kill "$worker_pid" 2>/dev/null
            local waited=0
            while kill -0 "$worker_pid" 2>/dev/null && [ "$waited" -lt 35 ]; do
                sleep 1
                waited=$((waited + 1))
            done
            if kill -0 "$worker_pid" 2>/dev/null; then
                warn "MCP Worker 未在 35 秒内退出，将强制停止"
                kill -9 "$worker_pid" 2>/dev/null
            fi
        fi
        rm -f "$MCP_WORKER_PID_FILE"
    fi
    success "MCP Worker 已停止"
}

# 停止前端服务
stop_frontend() {
    info "停止前端服务..."
    
    if [ -f "$FRONTEND_PID_FILE" ]; then
        kill -9 $(cat "$FRONTEND_PID_FILE") 2>/dev/null
        rm -f "$FRONTEND_PID_FILE"
    fi
    
    kill_port $FRONTEND_PORT
    success "前端服务已停止"
}

# 查看服务状态
show_status() {
    echo ""
    echo "╔════════════════════════════════════════════╗"
    echo "║           AMA Web 服务状态                 ║"
    echo "╠════════════════════════════════════════════╣"
    
    if check_port $BACKEND_PORT; then
        echo -e "║  后端服务: ${GREEN}运行中${NC} → http://localhost:$BACKEND_PORT  ║"
    else
        echo -e "║  后端服务: ${RED}已停止${NC}                         ║"
    fi

    local worker_pid=""
    if [ -f "$BATCH_WORKER_PID_FILE" ]; then
        worker_pid=$(cat "$BATCH_WORKER_PID_FILE" 2>/dev/null)
    fi
    if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
        echo -e "║  批量 Worker: ${GREEN}运行中${NC}                       ║"
    else
        echo -e "║  批量 Worker: ${RED}已停止${NC}                       ║"
    fi

    local mcp_worker_pid=""
    if [ -f "$MCP_WORKER_PID_FILE" ]; then
        mcp_worker_pid=$(cat "$MCP_WORKER_PID_FILE" 2>/dev/null)
    fi
    if [ -n "$mcp_worker_pid" ] && kill -0 "$mcp_worker_pid" 2>/dev/null; then
        echo -e "║  MCP Worker: ${GREEN}运行中${NC}                        ║"
    else
        echo -e "║  MCP Worker: ${RED}已停止${NC}                        ║"
    fi
    
    if check_port $FRONTEND_PORT; then
        echo -e "║  前端服务: ${GREEN}运行中${NC} → http://localhost:$FRONTEND_PORT/ama/ ║"
    else
        echo -e "║  前端服务: ${RED}已停止${NC}                         ║"
    fi
    
    # 检查 LLM 服务状态
    if [ -f "$SCRIPT_DIR/.env" ]; then
        LLM_BASE_URL=$(grep -E '^LLM_BASE_URL=' "$SCRIPT_DIR/.env" | cut -d '=' -f2 | tr -d '"' | tr -d "'" | tr -d '\r' || true)
        LLM_API_KEY=$(grep -E '^LLM_API_KEY=' "$SCRIPT_DIR/.env" | cut -d '=' -f2 | tr -d '"' | tr -d "'" | tr -d '\r' || true)
        LLM_PROVIDER=$(grep -E '^LLM_PROVIDER=' "$SCRIPT_DIR/.env" | cut -d '=' -f2 | tr -d '"' | tr -d "'" | tr -d '\r' || true)
        
        # 对于非自部署的官方服务商，跳过 GET /models 探活
        if [ -n "$LLM_PROVIDER" ] && [ "$LLM_PROVIDER" != "vllm" ] && [ "$LLM_PROVIDER" != "openai_compatible" ]; then
            echo -e "║  LLM 服务: ${GREEN}已配置${NC} ($LLM_PROVIDER) 请用健康验证测试 ║"
        elif [ -n "$LLM_BASE_URL" ]; then
            LLM_TEST_URL="${LLM_BASE_URL%/}/models"
            
            # 使用临时文件捕获 HTTP 状态码和响应内容
            TMP_RESP=$(mktemp)
            if [ -n "$LLM_API_KEY" ]; then
                LLM_HTTP_CODE=$(curl -s -w "%{http_code}" -o "$TMP_RESP" --connect-timeout 2 --max-time 4 -H "Authorization: Bearer $LLM_API_KEY" "$LLM_TEST_URL" 2>/dev/null || echo "000")
            else
                LLM_HTTP_CODE=$(curl -s -w "%{http_code}" -o "$TMP_RESP" --connect-timeout 2 --max-time 4 "$LLM_TEST_URL" 2>/dev/null || echo "000")
            fi
            
            # 解析响应内容 (限制长度)
            LLM_MSG=$(cat "$TMP_RESP" | grep -oE '"message"\s*:\s*"[^"]+"|"error"\s*:\s*\{[^}]+\}|[Uu]nauthorized|[Ff]orbidden' | head -n 1 | cut -c 1-30)
            rm -f "$TMP_RESP"
            
            if [ "$LLM_HTTP_CODE" = "200" ]; then
                echo -e "║  LLM 服务: ${GREEN}健康${NC} (服务可达)                   ║"
            elif [ "$LLM_HTTP_CODE" = "401" ]; then
                echo -e "║  LLM 服务: ${RED}未授权 (401)${NC} ${LLM_MSG}      ║"
            elif [ "$LLM_HTTP_CODE" = "403" ]; then
                echo -e "║  LLM 服务: ${RED}拒绝访问 (403)${NC} ${LLM_MSG}    ║"
            elif [ "$LLM_HTTP_CODE" = "404" ]; then
                echo -e "║  LLM 服务: ${RED}未找到 (404)${NC} ${LLM_MSG}      ║"
            elif [ "$LLM_HTTP_CODE" = "000" ]; then
                echo -e "║  LLM 服务: ${RED}连接失败 / 超时${NC}                 ║"
            else
                echo -e "║  LLM 服务: ${RED}异常${NC} (状态码: $LLM_HTTP_CODE) ${LLM_MSG}      ║"
            fi
        else
            echo -e "║  LLM 服务: ${YELLOW}未配置 URL${NC}                       ║"
        fi
    else
        echo -e "║  LLM 服务: ${YELLOW}未找到 .env 配置${NC}                 ║"
    fi
    
    echo "╚════════════════════════════════════════════╝"
    echo ""
}

# 启动所有服务
start_all() {
    echo ""
    echo "╔════════════════════════════════════════════╗"
    echo "║           启动 AMA Web 服务                ║"
    echo "╚════════════════════════════════════════════╝"
    echo ""
    
    start_backend || return 1
    start_batch_worker || return 1
    start_mcp_worker || return 1
    start_frontend || return 1
    
    echo ""
    show_status
}

# 停止所有服务
stop_all() {
    echo ""
    echo "╔════════════════════════════════════════════╗"
    echo "║           停止 AMA Web 服务                ║"
    echo "╚════════════════════════════════════════════╝"
    echo ""
    
    stop_frontend
    stop_mcp_worker
    stop_batch_worker
    stop_backend
    
    success "所有服务已停止"
}

# 重启所有服务
restart_all() {
    stop_all
    sleep 1
    start_all
}

# 显示帮助
show_help() {
    echo ""
    echo "用法: $0 [命令]"
    echo ""
    echo "命令:"
    echo "  start    启动所有 Web 服务"
    echo "  stop     停止所有 Web 服务"
    echo "  restart  重启所有 Web 服务"
    echo "  status   查看服务状态"
    echo "  help     显示此帮助信息"
    echo ""
    echo "示例:"
    echo "  $0 start   # 启动服务"
    echo "  $0 stop    # 停止服务"
    echo "  BACKEND_PORT=8121 FRONTEND_PORT=8122 $0 start"
    echo "  BACKEND_URL=http://127.0.0.1:8121 FRONTEND_PORT=8122 $0 start"
    echo ""
}

# 主逻辑
case "${1:-}" in
    start)
        start_all
        ;;
    stop)
        stop_all
        ;;
    restart)
        restart_all
        ;;
    status)
        show_status
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        if [ -n "${1:-}" ]; then
            error "未知命令: $1"
        fi
        show_help
        ;;
esac
