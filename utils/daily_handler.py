"""按日期+进程启动批次组织的 JSONL 日志 Handler。"""
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

# 级别 → 文件名
_LEVEL_FILES = {
    logging.NOTSET: "all.log",
    logging.WARNING: "warning.log",
    logging.ERROR: "error.log",
}


class DailyDirectoryHandler(logging.Handler):
    """按日期目录 + 启动批次组织日志文件，按级别分流。"""

    def __init__(
        self,
        base_dir: str | os.PathLike[str] = "logs",
        level: int = logging.NOTSET,
        *,
        retention_days: int = 14,
        process_id: int | None = None,
    ):
        super().__init__()
        self._file = None
        self.setLevel(level)
        self._base_dir = Path(base_dir)
        self._retention_days = max(1, int(retention_days))
        self._process_id = process_id if process_id is not None else os.getpid()

        # 进程启动时锁定一次 run_dir。PID 防止同一服务在同一秒重启时复用目录。
        now = datetime.now()
        self._cleanup_expired_days(now)
        self._run_dir = self._base_dir / now.strftime("%Y-%m-%d") / (
            f"{now.strftime('%H-%M-%S')}-p{self._process_id}"
        )
        self._run_dir.mkdir(parents=True, exist_ok=True)
        filename = _LEVEL_FILES.get(level, "all.log")
        self._path = self._run_dir / filename

    @property
    def run_dir(self) -> Path:
        return self._run_dir

    @property
    def path(self) -> Path:
        return self._path

    def _cleanup_expired_days(self, now: datetime) -> None:
        """Remove only date directories older than the configured retention."""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        cutoff = now.date().fromordinal(
            now.date().toordinal() - self._retention_days
        )
        for child in self._base_dir.iterdir():
            if not child.is_dir():
                continue
            try:
                day = datetime.strptime(child.name, "%Y-%m-%d").date()
            except ValueError:
                continue
            if day < cutoff:
                try:
                    shutil.rmtree(child)
                except OSError:
                    # Retention is housekeeping; a locked directory must not
                    # prevent the application from starting.
                    continue

    def _ensure_file(self):
        if self._file is None:
            self._file = self._path.open("a", encoding="utf-8")

    def emit(self, record):
        try:
            self._ensure_file()
            msg = self.format(record)
            if msg:
                self._file.write(msg + "\n")
                self._file.flush()
        except Exception:
            self.handleError(record)

    def close(self):
        if self._file:
            self._file.close()
        super().close()
