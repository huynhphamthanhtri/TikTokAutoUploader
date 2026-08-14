"""Single-threaded async runtime for Patchright browser objects."""

from __future__ import annotations

import asyncio
import inspect
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Awaitable, Callable, TypeVar


T = TypeVar("T")
PATCHRIGHT_VERSION = "1.61.2"


class RuntimeState(str, Enum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    state: RuntimeState
    thread_id: int | None
    pending_calls: int
    error: str | None = None


class RuntimeClosedError(RuntimeError):
    pass


def _default_playwright_factory() -> Any:
    try:
        installed_version = version("patchright")
        if installed_version != PATCHRIGHT_VERSION:
            raise RuntimeError(
                f"patchright=={PATCHRIGHT_VERSION} is required; found {installed_version}"
            )
        from patchright.async_api import async_playwright
    except (ImportError, PackageNotFoundError) as exc:
        raise RuntimeError(
            f"Patchright is required; install patchright=={PATCHRIGHT_VERSION}"
        ) from exc
    return async_playwright()


class BrowserRuntime:
    """Own an asyncio loop and Patchright instance on one daemon thread.

    Callables submitted with ``call`` execute on the owner thread and receive
    the started Playwright object. They must not return Page, Context, Browser,
    or Playwright objects to the caller.
    """

    def __init__(
        self,
        playwright_factory: Callable[[], Any] | None = None,
        *,
        thread_name: str = "patchright-runtime",
    ) -> None:
        self._factory = playwright_factory or _default_playwright_factory
        self._thread_name = thread_name
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._playwright: Any = None
        self._start_task: asyncio.Task[Any] | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._state = RuntimeState.NEW
        self._thread_id: int | None = None
        self._error: str | None = None

    def start(self, timeout: float = 10.0) -> RuntimeSnapshot:
        with self._lock:
            if self._state in (RuntimeState.STOPPING, RuntimeState.STOPPED):
                raise RuntimeClosedError("browser runtime is closed")
            if self._state is RuntimeState.NEW:
                self._state = RuntimeState.STARTING
                self._thread = threading.Thread(
                    target=self._thread_main,
                    name=self._thread_name,
                    daemon=True,
                )
                self._thread.start()
        if not self._started.wait(timeout):
            raise TimeoutError("browser runtime thread did not start")
        return self.snapshot()

    def call(self, function: Callable[[Any], Awaitable[T]]) -> Future[T]:
        self.start()
        with self._lock:
            loop = self._loop
            state = self._state
        if loop is None or state in (RuntimeState.STOPPING, RuntimeState.STOPPED):
            raise RuntimeClosedError("browser runtime is closed")
        return asyncio.run_coroutine_threadsafe(self._invoke(function), loop)

    async def acall(self, function: Callable[[Any], Awaitable[T]]) -> T:
        return await asyncio.wrap_future(self.call(function))

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(
                state=self._state,
                thread_id=self._thread_id,
                pending_calls=len(self._tasks),
                error=self._error,
            )

    def cancel_pending(self) -> None:
        with self._lock:
            loop = self._loop
        if loop is not None:
            loop.call_soon_threadsafe(self._cancel_tasks)

    def shutdown(self, timeout: float = 10.0) -> RuntimeSnapshot:
        if threading.get_ident() == self.snapshot().thread_id:
            raise RuntimeError("shutdown cannot block the browser runtime thread")
        with self._lock:
            state = self._state
            loop = self._loop
            thread = self._thread
            if state is RuntimeState.NEW:
                self._state = RuntimeState.STOPPED
                return self.snapshot_unlocked()
            if state is RuntimeState.STOPPED:
                return self.snapshot_unlocked()
            self._state = RuntimeState.STOPPING

        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(self._stop_playwright(), loop)
            try:
                future.result(timeout=timeout)
            finally:
                loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("browser runtime thread did not stop")
        return self.snapshot()

    def snapshot_unlocked(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            state=self._state,
            thread_id=self._thread_id,
            pending_calls=len(self._tasks),
            error=self._error,
        )

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
            self._thread_id = threading.get_ident()
        self._started.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()
            with self._lock:
                self._loop = None
                self._tasks.clear()
                self._state = RuntimeState.STOPPED

    async def _ensure_playwright(self) -> Any:
        if self._playwright is not None:
            return self._playwright
        if self._start_task is None:
            self._start_task = asyncio.create_task(self._create_playwright())
        return await self._start_task

    async def _create_playwright(self) -> Any:
        try:
            manager = self._factory()
            if inspect.isawaitable(manager):
                manager = await manager
            playwright = await manager.start() if hasattr(manager, "start") else manager
            self._playwright = playwright
            with self._lock:
                self._state = RuntimeState.RUNNING
                self._error = None
            return playwright
        except BaseException as exc:
            with self._lock:
                self._state = RuntimeState.FAILED
                self._error = f"{type(exc).__name__}: {exc}"
            raise

    async def _invoke(self, function: Callable[[Any], Awaitable[T]]) -> T:
        task = asyncio.current_task()
        if task is not None:
            with self._lock:
                self._tasks.add(task)
        try:
            playwright = await self._ensure_playwright()
            return await function(playwright)
        finally:
            if task is not None:
                with self._lock:
                    self._tasks.discard(task)

    def _cancel_tasks(self) -> None:
        current = asyncio.current_task()
        for task in tuple(self._tasks):
            if task is not current:
                task.cancel()

    async def _stop_playwright(self) -> None:
        self._cancel_tasks()
        current = asyncio.current_task()
        tasks = [task for task in tuple(self._tasks) if task is not current]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            finally:
                self._playwright = None
