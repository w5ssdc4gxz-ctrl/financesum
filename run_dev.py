#!/usr/bin/env python3
"""One-command developer runner for FinanceSum without Docker.

This script starts Redis, the FastAPI backend, the Celery worker, and the
Next.js frontend using local executables. It also ensures required dependencies
are installed (backend virtual environment, frontend npm packages) and loads
environment variables from `.env` and `frontend/.env.local`.

Requirements:
  - Python 3.11+
  - npm / Node.js 18+
  - redis-server (e.g. `brew install redis` on macOS)

Usage:
  python3 run_dev.py

Press Ctrl+C to stop all services.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from shlex import split as shlex_split
from typing import Iterable


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_VENV_DIR = BACKEND_DIR / ".venv"
BACKEND_VENV_BIN = BACKEND_VENV_DIR / "bin"
BACKEND_PYTHON = BACKEND_VENV_BIN / "python"


class CommandError(RuntimeError):
    """Raised when a required command is missing or fails."""


def log(message: str) -> None:
    print(message, flush=True)


def load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env

    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def ensure_command(name: str, install_hint: str | None = None) -> str:
    from shutil import which

    path = which(name)
    if path:
        return path

    hint = f" {install_hint}" if install_hint else ""
    raise CommandError(f"Required command '{name}' not found.{hint}")


def run(cmd: Iterable[str] | str, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def ensure_backend_venv() -> None:
    if BACKEND_PYTHON.exists():
        return

    log("\n📦 Creating backend virtual environment...")
    run([sys.executable, "-m", "venv", str(BACKEND_VENV_DIR)])

    log("📦 Installing backend dependencies...")
    run([str(BACKEND_PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(BACKEND_PYTHON), "-m", "pip", "install", "-r", "requirements.txt"], cwd=BACKEND_DIR)


def ensure_frontend_deps() -> None:
    node_modules = FRONTEND_DIR / "node_modules"
    if node_modules.exists():
        return

    log("\n📦 Installing frontend dependencies (npm install)...")
    run(["npm", "install"], cwd=FRONTEND_DIR)


def start_process(
    command: Iterable[str] | str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    name: str,
) -> subprocess.Popen[bytes]:
    cmd_list = command if isinstance(command, list) else shlex_split(str(command))
    log(f"▶️  Starting {name}: {' '.join(cmd_list)}")
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    process = subprocess.Popen(cmd_list, cwd=cwd, env=process_env)
    return process


def main() -> None:
    os.chdir(ROOT_DIR)
    log("\n🚀 Starting FinanceSum (local stack, no Docker)")
    log("==============================================")

    if not (ROOT_DIR / ".env").exists():
        raise CommandError(".env file not found. Copy .env.example and fill in credentials.")
    if not (FRONTEND_DIR / ".env.local").exists():
        raise CommandError(
            "frontend/.env.local not found. Copy frontend/.env.local.example and fill in values."
        )

    ensure_command("npm", "Install Node.js 18+ (e.g. via https://nodejs.org).")
    redis_server_path = ensure_command("redis-server", "Install Redis (e.g. `brew install redis`).")

    ensure_backend_venv()
    ensure_frontend_deps()

    env_vars = load_env_file(ROOT_DIR / ".env")
    env_vars.update(load_env_file(FRONTEND_DIR / ".env.local"))

    os.environ.update(env_vars)
    os.environ.setdefault("NEXT_PUBLIC_API_URL", "http://localhost:8000")

    redis_url = env_vars.get("REDIS_URL", "").strip()
    if not redis_url or redis_url.startswith("redis://redis"):
        redis_url = "redis://127.0.0.1:6379/0"
    os.environ["REDIS_URL"] = redis_url

    processes: list[tuple[str, subprocess.Popen[bytes]]] = []

    try:
        redis_proc = start_process(
            [redis_server_path, "--save", "", "--appendonly", "no"],
            name="redis-server",
        )
        processes.append(("redis", redis_proc))
        time.sleep(0.5)

        backend_proc = start_process(
            [str(BACKEND_PYTHON), "-m", "uvicorn", "app.main:app", "--reload", "--port", "8000"],
            cwd=BACKEND_DIR,
            env={"PYTHONPATH": str(BACKEND_DIR)},
            name="FastAPI backend",
        )
        processes.append(("backend", backend_proc))

        celery_proc = start_process(
            [str(BACKEND_PYTHON), "-m", "celery", "-A", "app.tasks.celery_app", "worker", "--loglevel=info"],
            cwd=BACKEND_DIR,
            env={"PYTHONPATH": str(BACKEND_DIR)},
            name="Celery worker",
        )
        processes.append(("celery", celery_proc))

        frontend_proc = start_process(
            ["npm", "run", "dev"],
            cwd=FRONTEND_DIR,
            env={"PORT": "3000"},
            name="Next.js frontend",
        )
        processes.append(("frontend", frontend_proc))

        log("\n✅ All services started. Frontend available at http://localhost:3000")
        log("   Press Ctrl+C to stop.")

        while True:
            for name, proc in processes:
                ret = proc.poll()
                if ret is not None:
                    raise CommandError(f"{name} exited unexpectedly with code {ret}")
            time.sleep(1)

    except KeyboardInterrupt:
        log("\n🛑 Caught Ctrl+C. Shutting down...")
    except CommandError as exc:
        log(f"\n❌ {exc}")
    finally:
        for name, proc in reversed(processes):
            if proc.poll() is None:
                log(f"🔻 Stopping {name} (pid {proc.pid})")
                proc.send_signal(signal.SIGTERM)
        time.sleep(1)
        for _, proc in reversed(processes):
            if proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    try:
        main()
    except CommandError as error:
        log(f"❌ {error}")
        raise SystemExit(1) from error

