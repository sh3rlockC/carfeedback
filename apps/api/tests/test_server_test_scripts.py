from __future__ import annotations

from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_isolated_test_compose_contains_full_real_flow_stack() -> None:
    compose = yaml.safe_load((ROOT / "ops/test/docker-compose.test.yml").read_text(encoding="utf-8"))

    services = compose["services"]
    assert {
        "nginx",
        "web",
        "api",
        "postgres",
        "redis",
        "worker",
        "temporal",
        "temporal-worker",
        "autohome-collector",
        "dongchedi-collector",
    }.issubset(services)
    for service_name in ("worker", "temporal-worker"):
        env = services[service_name]["environment"]
        assert env["JOB_ARTIFACT_CLEANUP_ENABLED"] == "false"
        assert "OPENCLAW_AUTOHOME_AGENT_IDS" in env
        assert "OPENCLAW_DCD_AGENT_IDS" in env
        assert "OPENCLAW_AGENT_LEASE_SECONDS" in env
        assert "OPENCLAW_AGENT_POOL_WAIT_SECONDS" in env


def test_setup_test_env_scales_workers_by_default() -> None:
    script = (ROOT / "scripts/server-test/setup-test-env.sh").read_text(encoding="utf-8")

    assert 'TEST_WORKER_SCALE="${TEST_WORKER_SCALE:-2}"' in script
    assert 'TEST_TEMPORAL_WORKER_SCALE="${TEST_TEMPORAL_WORKER_SCALE:-2}"' in script
    assert '--scale "worker=${TEST_WORKER_SCALE}"' in script
    assert '--scale "temporal-worker=${TEST_TEMPORAL_WORKER_SCALE}"' in script


def test_resource_watch_emits_alerts_from_sample_files(tmp_path: Path) -> None:
    stats = tmp_path / "docker-stats.tsv"
    stats.write_text("koubei-test-api\t95.3%\t91.1%\nkoubei-test-worker\t12.0%\t44.0%\n", encoding="utf-8")
    disk = tmp_path / "df.txt"
    disk.write_text("Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/disk 100 88 12 88% /\n", encoding="utf-8")

    result = subprocess.run(
        [
            "bash",
            "scripts/server-test/resource-watch.sh",
        ],
        cwd=ROOT,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "RESOURCE_WATCH_ONCE": "1",
            "RESOURCE_WATCH_SAMPLE_FILE": str(stats),
            "RESOURCE_WATCH_DISK_SAMPLE_FILE": str(disk),
            "RESOURCE_WATCH_REQUIRED_CPU_HITS": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "[resource-alert] cpu" in result.stdout
    assert "[resource-alert] memory" in result.stdout
    assert "[resource-alert] disk" in result.stdout
