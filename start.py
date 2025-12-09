#!/usr/bin/env python3
"""Simple one-command starter for FinanceSum.

Starts both the backend and frontend servers.

Usage:
  python3 start.py

Press Ctrl+C to stop both services.
"""

import getpass
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = ROOT_DIR / "backend"
FRONTEND_DIR = ROOT_DIR / "frontend"
BACKEND_VENV_DIR = BACKEND_DIR / ".venv"
BACKEND_PYTHON = BACKEND_VENV_DIR / "bin" / "python"
BACKEND_PID_FILE = ROOT_DIR / ".financesum_backend.pid"
FRONTEND_PID_FILE = ROOT_DIR / ".financesum_frontend.pid"

def load_env_file(path: Path) -> None:
    """Lightweight .env loader (KEY=VALUE, ignores comments/blank lines)."""
    if not path.exists():
        return
    try:
        for line in path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        # Do not block startup if the env file has an unexpected format
        pass


def log(message):
    """Print a message with flush."""
    print(message, flush=True)


def port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Return True if another process is already listening on the given port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def read_backend_pid() -> int | None:
    """Return the PID stored for the backend process, if any."""
    if not BACKEND_PID_FILE.exists():
        return None
    try:
        return int(BACKEND_PID_FILE.read_text().strip())
    except Exception:
        return None


def write_backend_pid(pid: int) -> None:
    try:
        BACKEND_PID_FILE.write_text(str(pid))
    except Exception:
        pass


def remove_backend_pid_file() -> None:
    try:
        BACKEND_PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def read_frontend_pid() -> int | None:
    if not FRONTEND_PID_FILE.exists():
        return None
    try:
        return int(FRONTEND_PID_FILE.read_text().strip())
    except Exception:
        return None


def write_frontend_pid(pid: int) -> None:
    try:
        FRONTEND_PID_FILE.write_text(str(pid))
    except Exception:
        pass


def remove_frontend_pid_file() -> None:
    try:
        FRONTEND_PID_FILE.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def describe_process(pid: int) -> str:
    try:
        output = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return output
    except Exception:
        return ""


def terminate_process(pid: int, timeout: float = 5) -> bool:
    """Attempt to terminate the given PID. Returns True if exited."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.2)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except Exception:
        return False

    return True


def force_free_port(port: int) -> bool:
    """Attempt to terminate any same-user process listening on the specified port."""
    try:
        output = subprocess.check_output(
            ["lsof", "-t", "-i", f":{port}", "-sTCP:LISTEN"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return False

    pids = [line.strip() for line in output.splitlines() if line.strip()]
    if not pids:
        return False

    current_user = getpass.getuser()
    killed_any = False

    for pid_str in pids:
        try:
            pid = int(pid_str)
        except ValueError:
            continue
        try:
            owner = subprocess.check_output(
                ["ps", "-o", "user=", "-p", str(pid)],
                text=True,
                stderr=subprocess.DEVNULL,
            ).strip()
        except Exception:
            owner = ""
        if owner and owner != current_user:
            continue
        log(f"   Attempting to stop process {pid} occupying port {port}...")
        terminate_process(pid)
        killed_any = True

    if not killed_any:
        return False

    for _ in range(25):
        if not port_in_use(port):
            return True
        time.sleep(0.2)
    return not port_in_use(port)


def ensure_backend_port_available(port: int, host: str) -> bool:
    """Ensure backend port is free, attempting to stop stale FinanceSum backend if needed."""
    if not port_in_use(port, host):
        return True

    stale_pid = read_backend_pid()
    if stale_pid:
        desc = describe_process(stale_pid)
        log(f"\n⚠️  Backend port {port} is busy (stale PID {stale_pid}{' - ' + desc if desc else ''}). Attempting cleanup...")
        if terminate_process(stale_pid):
            remove_backend_pid_file()
            for _ in range(20):  # wait up to 4 seconds
                if not port_in_use(port, host):
                    log("   Previous backend stopped successfully.")
                    return True
                time.sleep(0.2)
        else:
            log("   Unable to terminate the stale backend process automatically.")

    if port_in_use(port, host):
        if host in {"127.0.0.1", "0.0.0.0", "::", ""} and force_free_port(port):
            return True
    return not port_in_use(port, host)


def select_backend_port(preferred_port: int, host: str, attempts: int = 10) -> int:
    """Select an available backend port, trying nearby ports if necessary."""
    candidate = preferred_port
    for offset in range(attempts + 1):
        candidate = preferred_port + offset
        if ensure_backend_port_available(candidate, host):
            if offset > 0:
                log(f"\nℹ️  Preferred backend port {preferred_port} was busy. Using fallback port {candidate} instead.")
            return candidate
    log(f"\n❌ Unable to find a free backend port starting from {preferred_port}.")
    log("   Stop other processes using those ports or set BACKEND_PORT to a free port.")
    raise SystemExit(1)


def ensure_frontend_port_available(port: int, allow_fallback: bool) -> bool:
    """Ensure desired frontend port is available. Returns True if free."""
    if not port_in_use(port):
        return True

    stale_pid = read_frontend_pid()
    if stale_pid:
        desc = describe_process(stale_pid)
        log(f"\n⚠️  Frontend port {port} is busy (stale PID {stale_pid}{' - ' + desc if desc else ''}). Attempting cleanup...")
        if terminate_process(stale_pid):
            remove_frontend_pid_file()
            for _ in range(20):
                if not port_in_use(port):
                    log("   Previous frontend stopped successfully.")
                    return True
                time.sleep(0.2)
        else:
            log("   Unable to terminate the stale frontend process automatically.")

    if port_in_use(port):
        if force_free_port(port):
            return True
        if allow_fallback:
            return False
        log(f"\n❌ Frontend port {port} is already in use.")
        log("   Stop the running dev server or set FRONTEND_PORT to a free port.")
        log(f"   Tip (macOS/Linux): lsof -i :{port}")
        raise SystemExit(1)

    return True


def select_frontend_port(preferred_port: int, allow_fallback: bool, attempts: int = 10) -> int:
    """Select frontend port. If fallback disallowed, always returns preferred_port or exits."""
    if not allow_fallback:
        ensure_frontend_port_available(preferred_port, allow_fallback=False)
        return preferred_port

    for offset in range(attempts + 1):
        candidate = preferred_port + offset
        if ensure_frontend_port_available(candidate, allow_fallback=True):
            if offset > 0:
                log(f"\nℹ️  Preferred frontend port {preferred_port} was busy. Using fallback port {candidate} instead.")
            return candidate

    log(f"\n❌ Unable to find a free frontend port starting from {preferred_port}.")
    log("   Stop other dev servers or set FRONTEND_PORT to a free port.")
    raise SystemExit(1)


def main():
    """Start backend and frontend servers."""
    os.chdir(ROOT_DIR)

    # Load env files early so both backend and frontend receive required keys
    load_env_file(ROOT_DIR / ".env")
    load_env_file(BACKEND_DIR / ".env")
    load_env_file(FRONTEND_DIR / ".env")

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
    
    backend_port_env = int(os.getenv("BACKEND_PORT", "8000"))
    backend_host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port_check_host = "127.0.0.1" if backend_host in {"0.0.0.0", "::", ""} else backend_host
    frontend_port_env = int(os.getenv("FRONTEND_PORT", "3000"))
    allow_frontend_fallback = os.getenv("ALLOW_FRONTEND_PORT_FALLBACK", "false").lower() in {"1", "true", "yes"}

    backend_port = select_backend_port(backend_port_env, port_check_host)

    processes = []

    try:
        # Start backend
        log(f"\n▶️  Starting Backend (http://localhost:{backend_port})...")
        backend_env = os.environ.copy()
        backend_env["BACKEND_PORT"] = str(backend_port)
        backend_proc = subprocess.Popen(
            [str(BACKEND_PYTHON), "run_backend.py"],
            cwd=BACKEND_DIR,
            env=backend_env,
        )
        processes.append(("Backend", backend_proc))
        write_backend_pid(backend_proc.pid)
        time.sleep(2)  # Give backend time to start

        if backend_proc.poll() is not None:
            log("❌ Backend failed to start. Check the log output above for details.")
            raise SystemExit(1)

        # Ensure frontend port is free
        frontend_port = select_frontend_port(frontend_port_env, allow_frontend_fallback)

        # Start frontend
        log(f"▶️  Starting Frontend (http://localhost:{frontend_port})...")
        frontend_env = os.environ.copy()
        frontend_env["PORT"] = str(frontend_port)
        frontend_env["NEXT_PUBLIC_API_URL"] = f"http://localhost:{backend_port}"
        # Bypass Next.js rewrite proxy to avoid dev-server timeouts on long summary requests
        frontend_env["NEXT_PUBLIC_API_PROXY_BASE"] = ""
        frontend_proc = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=FRONTEND_DIR,
            env=frontend_env,
        )
        processes.append(("Frontend", frontend_proc))
        write_frontend_pid(frontend_proc.pid)
        
        log("\n✅ Both services started!")
        log(f"   🌐 Frontend: http://localhost:{frontend_port}")
        log(f"   🔌 Backend:  http://localhost:{backend_port}")
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
            if name == "Backend":
                remove_backend_pid_file()
            elif name == "Frontend":
                remove_frontend_pid_file()
        
        # Wait a bit for graceful shutdown
        time.sleep(1)
        
        # Force kill if still running
        for name, proc in reversed(processes):
            if proc.poll() is None:
                proc.kill()
            if name == "Backend":
                remove_backend_pid_file()
            elif name == "Frontend":
                remove_frontend_pid_file()
        
        log("✅ All services stopped.\n")


if __name__ == "__main__":
    main()




