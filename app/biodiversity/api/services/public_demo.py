"""Bounded resource policy and retention for the unauthenticated public demo."""

from __future__ import annotations

import shutil
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.biodiversity.api.dependencies import APISettings
    from app.biodiversity.api.repositories import RunCatalog
    from app.biodiversity.api.services.operations import GraphRuntime


def _path_bytes(path: Path) -> int:
    candidates = [path, Path(f"{path}-wal"), Path(f"{path}-shm")]
    total = sum(item.stat().st_size for item in candidates if item.is_file())
    if path.is_dir():
        total += sum(
            item.stat().st_size
            for item in path.rglob("*")
            if item.is_file()
        )
    return total


class PublicDemoGuard:
    """Apply process-local admission limits before a graph mutation is queued."""

    def __init__(self, settings: "APISettings", catalog: "RunCatalog") -> None:
        self.settings = settings
        self.catalog = catalog
        self._lock = threading.Lock()
        self._mutation_times: deque[float] = deque()

    def storage_bytes(self) -> int:
        return sum(
            _path_bytes(path)
            for path in (
                self.settings.checkpoint_db,
                self.settings.catalog_db,
                self.settings.report_root,
            )
        )

    def admit(self, *, kind: Literal["start", "resume", "replay", "fork"]) -> None:
        if not self.settings.public_demo:
            return
        now = time.monotonic()
        with self._lock:
            while self._mutation_times and now - self._mutation_times[0] >= 60:
                self._mutation_times.popleft()
            if (
                self.settings.mutations_per_minute
                and len(self._mutation_times) >= self.settings.mutations_per_minute
            ):
                raise RuntimeError("demo_rate_limit")
            if (
                kind == "start"
                and self.settings.max_demo_threads
                and self.catalog.thread_count() >= self.settings.max_demo_threads
            ):
                raise RuntimeError("demo_run_capacity")
            if (
                self.settings.max_storage_bytes
                and self.storage_bytes() >= self.settings.max_storage_bytes
            ):
                raise RuntimeError("demo_storage_capacity")
            self._mutation_times.append(now)


class DemoRetention:
    """Delete expired demo threads through public catalog/checkpointer APIs."""

    def __init__(
        self,
        *,
        settings: "APISettings",
        catalog: "RunCatalog",
        runtime: "GraphRuntime",
    ) -> None:
        self.settings = settings
        self.catalog = catalog
        self.runtime = runtime

    def cleanup(self) -> int:
        if not self.settings.public_demo or not self.settings.run_retention_seconds:
            return 0
        cutoff = datetime.now(UTC) - timedelta(
            seconds=self.settings.run_retention_seconds
        )
        removed = 0
        for thread_id in self.catalog.expired_threads(before=cutoff):
            report_references = self.catalog.report_directories_for_thread(thread_id)
            self.runtime.delete_thread(thread_id)
            self.catalog.delete_thread(thread_id)
            self._delete_reports(report_references)
            removed += 1
        return removed

    def _delete_reports(self, references: list[str]) -> None:
        root = self.settings.report_root.resolve()
        for reference in references:
            directory = (root / reference).resolve()
            try:
                directory.relative_to(root)
            except ValueError:
                continue
            if directory.is_dir():
                shutil.rmtree(directory)
