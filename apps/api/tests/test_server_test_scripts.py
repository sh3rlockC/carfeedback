from __future__ import annotations

from pathlib import Path
import subprocess


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
        cwd=Path(__file__).resolve().parents[3],
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
