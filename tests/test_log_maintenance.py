from pathlib import Path

from agent_mem_bridge.log_maintenance import rotate_log_if_needed


def test_log_rotation_keeps_bounded_backups(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    log_path.write_text("first", encoding="utf-8")

    assert rotate_log_if_needed(log_path, incoming_bytes=10, max_bytes=8, backup_count=2) is True
    log_path.write_text("second", encoding="utf-8")
    assert rotate_log_if_needed(log_path, incoming_bytes=10, max_bytes=8, backup_count=2) is True
    log_path.write_text("third", encoding="utf-8")
    assert rotate_log_if_needed(log_path, incoming_bytes=10, max_bytes=8, backup_count=2) is True

    assert (tmp_path / "events.log.1").read_text(encoding="utf-8") == "third"
    assert (tmp_path / "events.log.2").read_text(encoding="utf-8") == "second"
    assert not (tmp_path / "events.log.3").exists()


def test_log_rotation_can_discard_history_when_backup_count_is_zero(tmp_path: Path) -> None:
    log_path = tmp_path / "events.log"
    log_path.write_text("oversized", encoding="utf-8")

    assert rotate_log_if_needed(log_path, incoming_bytes=1, max_bytes=4, backup_count=0) is True
    assert not log_path.exists()
