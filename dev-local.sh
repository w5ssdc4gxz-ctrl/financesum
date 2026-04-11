#!/bin/bash
# Local dev: backend on :8000, frontend on :3000 pointing to local backend.
# Run this once — opens two Terminal tabs automatically.

ROOT="$(cd "$(dirname "$0")" && pwd)"

osascript <<EOF
tell application "Terminal"
  -- Tab 1: Backend
  do script "cd '$ROOT/backend' && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"

  -- Tab 2: Frontend (pointed at local backend)
  tell application "System Events" to keystroke "t" using command down
  do script "cd '$ROOT/frontend' && NEXT_PUBLIC_API_URL=http://localhost:8000 BACKEND_API_URL=http://localhost:8000 npm run dev" in front window
end tell
EOF

echo ""
echo "Starting..."
echo "  Backend  → http://localhost:8000/docs"
echo "  Frontend → http://localhost:3000"
