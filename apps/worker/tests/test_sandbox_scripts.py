from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_sync_server_env_copies_only_allowed_keys_without_printing_secrets(tmp_path: Path) -> None:
    server_env = tmp_path / "server.env"
    server_env.write_text(
        "\n".join(
            [
                "TAVILY_API_KEY=tavily-secret",
                "LLM_PROVIDER=deepseek",
                "LLM_API_KEY=llm-secret",
                "LLM_BASE_URL=https://api.example.invalid",
                "LLM_MODEL_BATCH=deepseek-v4-flash",
                "LLM_MODEL_REPORT=deepseek-v4-pro",
                "LLM_MODEL_QA=deepseek-v4-pro",
                "DATABASE_URL=postgresql+psycopg://prod:prod@postgres:5432/prod",
                "SESSION_SECRET=server-session-secret",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    output_env = tmp_path / "sandbox" / ".env"
    script = REPO_ROOT / "scripts" / "sandbox" / "sync-server-env.sh"

    completed = subprocess.run(
        [str(script)],
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "SERVER_ENV_SOURCE": str(server_env),
            "SANDBOX_ENV_PATH": str(output_env),
            "SANDBOX_ROOT": str(tmp_path / "sandbox"),
            "SANDBOX_OPENCLAW_TOKEN_SOURCE": "",
        },
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    combined_output = completed.stdout + completed.stderr
    assert "tavily-secret" not in combined_output
    assert "llm-secret" not in combined_output
    assert "server-session-secret" not in combined_output

    content = output_env.read_text(encoding="utf-8")
    assert "APP_ENV=sandbox" in content
    assert "HTTP_PORT=18080" in content
    assert "NEXT_PUBLIC_BASE_PATH=/car-user-feedback" in content
    assert "TAVILY_API_KEY=tavily-secret" in content
    assert "LLM_API_KEY=llm-secret" in content
    assert "DATABASE_URL=postgresql+psycopg://koubei:koubei@postgres:5432/koubei" in content
    assert "SESSION_SECRET=server-session-secret" not in content
    assert output_env.parent.name == "sandbox"


def test_nginx_redirects_stay_relative_for_preview_ports() -> None:
    nginx_config = REPO_ROOT / "ops" / "nginx" / "default.conf"

    content = nginx_config.read_text(encoding="utf-8")

    assert "absolute_redirect off;" in content
