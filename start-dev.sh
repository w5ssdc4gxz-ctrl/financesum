#!/usr/bin/env bash

set -euo pipefail

# FinanceSum Development Start Script
# Starts Redis, FastAPI backend, Celery worker (Docker) + Next.js frontend (local)

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
cd "$ROOT_DIR"

log() {
  echo -e "$1"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    log "❌ Required command '$1' not found. Please install it first."
    exit 1
  fi
}

log "\n🚀 Starting FinanceSum Development Environment"
log "=============================================="

# Basic tool checks
require_cmd npm
require_cmd docker

if command -v docker-compose >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker-compose"
elif docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE="docker compose"
else
  log "❌ Docker Compose is required (install docker desktop or compose-plugin)."
  exit 1
fi

# Check environment files
if [ ! -f .env ]; then
  log "❌ .env file not found!"
  log "   Run: cp .env.example .env  # then fill in your credentials"
  exit 1
fi

if [ ! -f frontend/.env.local ]; then
  log "❌ frontend/.env.local file not found!"
  log "   Run: cp frontend/.env.local.example frontend/.env.local"
  exit 1
fi

# Ensure frontend dependencies are installed
if [ ! -d frontend/node_modules ]; then
  log "\n📦 Installing frontend dependencies..."
  (cd frontend && npm install)
fi

# Default frontend API URL if not provided
export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-http://localhost:8000}"

# Start Docker services (Redis, Backend, Celery)
log "\n📦 Starting Docker services..."
${DOCKER_COMPOSE} up -d --build

cleanup() {
  log "\n🛑 Stopping services..."
  ${DOCKER_COMPOSE} down >/dev/null 2>&1 || true
}

trap cleanup EXIT

# Wait for services to be ready
log "\n⏳ Waiting for backend to become healthy..."
ATTEMPTS=0
until curl -sf http://localhost:8000/health >/dev/null 2>&1; do
  ATTEMPTS=$((ATTEMPTS + 1))
  if [ "$ATTEMPTS" -ge 30 ]; then
    log "⚠️  Backend health check is still failing after 30 attempts."\
" You can continue, but API calls may error until it finishes starting."
    break
  fi
  sleep 2
done

if [ "$ATTEMPTS" -lt 30 ]; then
  log "✅ Backend is responding at http://localhost:8000"
fi

# Start frontend in development mode
log "\n🎨 Starting frontend..."
log "   URL: http://localhost:3000"
log "\nPress Ctrl+C to stop all services"

cd frontend
npm run dev


