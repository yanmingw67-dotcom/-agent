#!/usr/bin/env bash
# P3.1-B 独立验收环境统一入口。只操作 product-knowledge-p31b 项目，不触碰共享部署。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export P31B_IMAGE_TAG="${P31B_IMAGE_TAG:-$(git rev-parse --short=7 HEAD)}"
COMPOSE=(docker compose --project-name product-knowledge-p31b --env-file .env.p31b -f docker-compose.p31b.yml)
PY="python3"
if [[ -x .venv/bin/python ]]; then PY=".venv/bin/python"; fi

usage() {
  cat <<'EOF'
用法: scripts/p31b_env.sh <命令>

命令:
  init      生成 .env.p31b（随机令牌，权限 600，不覆盖已有文件）
  build     从当前 HEAD 构建后端镜像，并构建前端镜像
  start     启动全部服务并等待健康检查
  stop      停止全部服务（保留容器、数据库与存储卷）
  restart   重启全部服务并等待健康检查
  status    输出服务、健康、迁移、路径与 worker 心跳状态
  logs      跟随全部服务日志
  ps        显示 Compose 服务状态
  down      删除容器与网络，保留数据卷
  reset     删除容器、网络和数据卷（需 CONFIRM=1，仅限本项目）
EOF
}

init() {
  "$PY" scripts/p31b_generate_env.py
}

build() {
  init
  echo "[p31b] 构建后端镜像 product-knowledge:p31b-$P31B_IMAGE_TAG"
  docker build --progress=plain -t "product-knowledge:p31b-$P31B_IMAGE_TAG" .
  echo "[p31b] 构建前端镜像 product-knowledge-p31b-frontend:$P31B_IMAGE_TAG"
  "${COMPOSE[@]}" build frontend
}

start() {
  init
  "${COMPOSE[@]}" up -d --wait --wait-timeout 300
  "$PY" scripts/p31b_status.py
}

stop() {
  "${COMPOSE[@]}" stop
}

restart() {
  "${COMPOSE[@]}" restart
  "${COMPOSE[@]}" up -d --wait --wait-timeout 300
  "$PY" scripts/p31b_status.py
}

status() {
  init
  "$PY" scripts/p31b_status.py
}

case "${1:-}" in
  init) init ;;
  build) build ;;
  start) start ;;
  stop) stop ;;
  restart) restart ;;
  status) status ;;
  logs) "${COMPOSE[@]}" logs -f --tail=200 ;;
  ps) "${COMPOSE[@]}" ps ;;
  down) "${COMPOSE[@]}" down ;;
  reset)
    if [[ "${CONFIRM:-}" != "1" ]]; then
      echo "拒绝执行：reset 会删除本项目全部数据卷。确认后使用 CONFIRM=1 scripts/p31b_env.sh reset" >&2
      exit 2
    fi
    "${COMPOSE[@]}" down -v --remove-orphans
    ;;
  *) usage; exit 1 ;;
esac
