from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


class _DebounceHandler:
    """Collects filesystem events and flushes them after a quiet period.

    Prevents double-processing of files still being written (e.g. a large PDF
    being copied triggers dozens of modified events during the copy).
    """

    def __init__(
        self,
        enqueue_fn: Callable[[str], bool],
        supported_exts: set[str],
        debounce_s: float,
        stop_event: threading.Event,
    ) -> None:
        self._enqueue = enqueue_fn
        self._exts = supported_exts
        self._debounce = debounce_s
        self._stop = stop_event
        self._pending: dict[str, float] = {}
        self._lock = threading.Lock()

        flush_thread = threading.Thread(target=self._flush_loop, daemon=True)
        flush_thread.start()

    def _is_supported(self, path: str) -> bool:
        return Path(path).suffix.lstrip(".").lower() in self._exts

    def maybe_enqueue(self, path: str) -> None:
        if path and self._is_supported(path):
            with self._lock:
                self._pending[path] = time.monotonic()

    def _flush_loop(self) -> None:
        while not self._stop.is_set():
            time.sleep(0.5)
            now = time.monotonic()
            with self._lock:
                ready = [p for p, t in self._pending.items() if now - t >= self._debounce]
                for p in ready:
                    del self._pending[p]
            for p in ready:
                try:
                    self._enqueue(p)
                except Exception:
                    logger.exception("Failed to enqueue %s", p)


class InboxWatcher:
    """Watches a folder and enqueues new or changed files for indexing.

    Runs a background watchdog Observer. Call start() then stop() (or use as
    a context manager). File deletions are intentionally ignored in v1.
    """

    def __init__(
        self,
        folder: str | Path,
        enqueue_fn: Callable[[str], bool],
        debounce_s: float = 2.0,
    ) -> None:
        try:
            from watchdog.events import FileSystemEventHandler  # type: ignore[import-untyped]
            from watchdog.observers import Observer  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                'watchdog is required for folder watching. '
                'Install with: pip install "aksharamd[index]"'
            ) from exc

        # Trigger parser registration so the extension list is populated.
        import aksharamd.plugins.registry as _reg
        from aksharamd.plugins import parsers as _parsers_pkg  # noqa: F401
        supported_exts = set(_reg._parsers.keys())

        self._folder = Path(folder)
        self._stop_event = threading.Event()
        self._observer = Observer()

        handler = _DebounceHandler(enqueue_fn, supported_exts, debounce_s, self._stop_event)

        class _Adapter(FileSystemEventHandler):
            def on_created(self_, event) -> None:  # noqa: N805
                if not event.is_directory:
                    handler.maybe_enqueue(event.src_path)

            def on_modified(self_, event) -> None:  # noqa: N805
                if not event.is_directory:
                    handler.maybe_enqueue(event.src_path)

            def on_moved(self_, event) -> None:  # noqa: N805
                if not event.is_directory:
                    handler.maybe_enqueue(event.dest_path)

        self._observer.schedule(_Adapter(), str(self._folder), recursive=True)

    def start(self) -> None:
        self._observer.start()
        logger.info("Watching %s", self._folder)

    def stop(self) -> None:
        self._stop_event.set()
        self._observer.stop()
        self._observer.join()

    def __enter__(self) -> InboxWatcher:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
