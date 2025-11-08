"""Helper script to run the FinanceSum backend locally."""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn


def str_to_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def main() -> None:
    backend_dir = Path(__file__).resolve().parent
    os.chdir(backend_dir)

    host = os.getenv("BACKEND_HOST", "0.0.0.0")
    port = int(os.getenv("BACKEND_PORT", "8000"))
    reload = str_to_bool(os.getenv("BACKEND_RELOAD"), True)

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        reload_dirs=[
            str(backend_dir / "app"),
            str(backend_dir / "services"),
            str(backend_dir / "tasks"),
        ],
        app_dir=str(backend_dir),
    )


if __name__ == "__main__":
    main()