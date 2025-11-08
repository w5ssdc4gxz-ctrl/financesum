#!/usr/bin/env python3
"""Simple one-command starter for FinanceSum.

Starts both the backend and frontend servers.

Usage:
  python3 start.py

Press Ctrl+C to stop both services.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_VENV_DIR = BACKEND_DIR / ".venv"
BACKEND_PYTHON = BACKEND_VENV_DIR / "bin" / "python"


def log(message):
    """Print a message with flush."""
    print(message, flush=True)


def main():
    """Start backend and frontend servers."""
    os.chdir(ROOT_DIR)
    
    log("\n🚀 Starting FinanceSum")
    log("=" * 50)
    
    # Check if backend venv exists
    if not BACKEND_PYTHON.exists():
        log("❌ Backend virtual environment not found!")
        log("   Run: cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt")
        sys.exit(1)
    
    # Check if frontend node_modules exists
    if not (FRONTEND_DIR / "node_modules").exists():
        log("❌ Frontend dependencies not installed!")
        log("   Run: cd frontend && npm install")
        sys.exit(1)
    
    processes = []
    
    try:
        # Start backend
        log("\n▶️  Starting Backend (http://localhost:8000)...")
        backend_proc = subprocess.Popen(
            [str(BACKEND_PYTHON), "run_backend.py"],
            cwd=BACKEND_DIR,
        )
        processes.append(("Backend", backend_proc))
        time.sleep(2)  # Give backend time to start
        
        # Start frontend
        log("▶️  Starting Frontend (http://localhost:3000)...")
        frontend_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=FRONTEND_DIR,
        )
        processes.append(("Frontend", frontend_proc))
        
        log("\n✅ Both services started!")
        log("   🌐 Frontend: http://localhost:3000")
        log("   🔌 Backend:  http://localhost:8000")
        log("\n   Press Ctrl+C to stop both services.\n")
        
        # Keep running until Ctrl+C
        while True:
            for name, proc in processes:
                if proc.poll() is not None:
                    log(f"\n❌ {name} stopped unexpectedly!")
                    raise SystemExit(1)
            time.sleep(1)
    
    except KeyboardInterrupt:
        log("\n\n🛑 Stopping services...")
    
    finally:
        # Stop all processes
        for name, proc in reversed(processes):
            if proc.poll() is None:
                log(f"   Stopping {name}...")
                proc.send_signal(signal.SIGTERM)
        
        # Wait a bit for graceful shutdown
        time.sleep(1)
        
        # Force kill if still running
        for _, proc in reversed(processes):
            if proc.poll() is None:
                proc.kill()
        
        log("✅ All services stopped.\n")


if __name__ == "__main__":
    main()








