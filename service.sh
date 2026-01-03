#!/bin/bash
# ============================================================================
# AMA Web 服务管理脚本
# 用法: ./web.sh [start|stop|restart|status]
# ============================================================================

# 获取脚本所在目录的绝对路径
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 配置
BACKEND_PORT=8011
FRONTEND_PORT=8012
CONDA_ENV="ama"
PID_DIR="$SCRIPT_DIR/.pids"

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
    echo $! > "$PID_DIR/backend.pid"
    
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

# 启动前端服务
start_frontend() {
    info "启动前端服务 (端口: $FRONTEND_PORT)..."
    
    if check_port $FRONTEND_PORT; then
        warn "端口 $FRONTEND_PORT 已被占用，正在释放..."
        kill_port $FRONTEND_PORT
        sleep 1
    fi
    
    cd "$SCRIPT_DIR/web/frontend"
    nohup npm run dev > "$PID_DIR/frontend.log" 2>&1 &
    echo $! > "$PID_DIR/frontend.pid"
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
    
    if [ -f "$PID_DIR/backend.pid" ]; then
        kill -9 $(cat "$PID_DIR/backend.pid") 2>/dev/null
        rm -f "$PID_DIR/backend.pid"
    fi
    
    kill_port $BACKEND_PORT
    success "后端服务已停止"
}

# 停止前端服务
stop_frontend() {
    info "停止前端服务..."
    
    if [ -f "$PID_DIR/frontend.pid" ]; then
        kill -9 $(cat "$PID_DIR/frontend.pid") 2>/dev/null
        rm -f "$PID_DIR/frontend.pid"
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
    
    if check_port $FRONTEND_PORT; then
        echo -e "║  前端服务: ${GREEN}运行中${NC} → http://localhost:$FRONTEND_PORT/ama/ ║"
    else
        echo -e "║  前端服务: ${RED}已停止${NC}                         ║"
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
    
    start_backend
    start_frontend
    
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
