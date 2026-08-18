"""Persistent Chromium session service backed by :mod:`browser_runtime`."""

from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import uuid
from concurrent.futures import Future
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Awaitable, Callable, Mapping, Sequence, TypeVar

from browser_runtime import BrowserRuntime, RuntimeSnapshot


T = TypeVar("T")
PageOperation = Callable[[Any], Awaitable[T]]


class SessionMode(str, Enum):
    AUTOMATION = "automation"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class BrowserSessionConfig:
    profile_path: str | os.PathLike[str]
    mode: SessionMode = SessionMode.AUTOMATION
    executable_path: str | os.PathLike[str] | None = None
    headed: bool = False
    args: tuple[str, ...] = ()
    viewport: tuple[int, int] | None = None
    locale: str | None = None
    timezone_id: str | None = None
    geolocation: Mapping[str, float] | None = None
    permissions: tuple[str, ...] = ()
    proxy: Mapping[str, str] | None = None
    account_uuid: str | None = None
    profile_name: str | None = None
    stealth_config: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not str(self.profile_path).strip():
            raise ValueError("profile_path is required")
        object.__setattr__(self, "profile_path", str(self.profile_path))
        object.__setattr__(self, "mode", SessionMode(self.mode))
        if self.executable_path is not None:
            object.__setattr__(self, "executable_path", str(self.executable_path))
        if self.mode is SessionMode.MANUAL and not self.headed:
            object.__setattr__(self, "headed", True)
        object.__setattr__(self, "args", tuple(str(arg) for arg in self.args))
        if self.stealth_config is not None:
            object.__setattr__(self, "stealth_config", MappingProxyType(dict(self.stealth_config)))
        if self.viewport is not None:
            width, height = self.viewport
            if width <= 0 or height <= 0:
                raise ValueError("viewport dimensions must be positive")
            object.__setattr__(self, "viewport", (int(width), int(height)))
        if self.proxy is not None:
            object.__setattr__(
                self,
                "proxy",
                MappingProxyType({str(key): str(value) for key, value in self.proxy.items()}),
            )
        if self.geolocation is not None:
            geolocation = {str(key): float(value) for key, value in self.geolocation.items()}
            if not -90 <= geolocation.get("latitude", 91) <= 90:
                raise ValueError("geolocation latitude is invalid")
            if not -180 <= geolocation.get("longitude", 181) <= 180:
                raise ValueError("geolocation longitude is invalid")
            object.__setattr__(self, "geolocation", MappingProxyType(geolocation))
        object.__setattr__(self, "permissions", tuple(str(value) for value in self.permissions))


@dataclass(frozen=True, slots=True)
class SessionHandle:
    session_id: str
    generation: int
    profile_path: str
    mode: SessionMode


@dataclass(frozen=True, slots=True)
class SessionResult:
    handle: SessionHandle
    page_count: int


@dataclass(frozen=True, slots=True)
class OperationResult:
    handle: SessionHandle
    value: Any


@dataclass(frozen=True, slots=True)
class CookieImportReport:
    """Reflects the cookies actually kept by the context, not just requested."""

    requested: int
    accepted: int
    auth_requested: int
    auth_accepted: int
    missing_auth_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CookieExport:
    handle: SessionHandle
    cookies_json: str


@dataclass(frozen=True, slots=True)
class BrowserStatus:
    runtime: RuntimeSnapshot
    active_sessions: int
    opening_sessions: int
    profile_paths: tuple[str, ...]
    closed: bool


class ProfileInUseError(RuntimeError):
    pass


class StaleSessionError(RuntimeError):
    pass


class BrowserServiceClosedError(RuntimeError):
    pass


@dataclass(slots=True)
class _Session:
    handle: SessionHandle
    context: Any
    page: Any
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)


class PatchrightBrowser:
    """Manage persistent contexts without exposing browser objects cross-thread."""

    def __init__(self, runtime: BrowserRuntime | None = None) -> None:
        self._runtime = runtime or BrowserRuntime()
        self._owns_runtime = runtime is None
        self._sessions: dict[str, _Session] = {}
        self._profiles: dict[str, str] = {}
        self._generations: dict[str, int] = {}
        self._opening_tasks: set[asyncio.Task[Any]] = set()
        self._status_lock = threading.Lock()
        self._active = 0
        self._opening = 0
        self._profile_snapshot: tuple[str, ...] = ()
        self._closed = False

    def open_session(self, config: BrowserSessionConfig) -> Future[SessionResult]:
        self._check_open()
        return self._runtime.call(lambda playwright: self._open(playwright, config))

    async def open_session_async(self, config: BrowserSessionConfig) -> SessionResult:
        return await asyncio.wrap_future(self.open_session(config))

    def run(self, handle: SessionHandle, operation: PageOperation[T]) -> Future[OperationResult]:
        self._check_open()
        return self._runtime.call(lambda _playwright: self._run(handle, operation))

    async def run_async(
        self, handle: SessionHandle, operation: PageOperation[T]
    ) -> OperationResult:
        return await asyncio.wrap_future(self.run(handle, operation))

    def export_cookies(self, handle: SessionHandle) -> Future[CookieExport]:
        self._check_open()
        return self._runtime.call(lambda _playwright: self._export_cookies(handle))

    async def export_cookies_async(self, handle: SessionHandle) -> CookieExport:
        return await asyncio.wrap_future(self.export_cookies(handle))

    def import_cookies(
        self,
        handle: SessionHandle,
        cookies: str | Sequence[Mapping[str, Any]],
    ) -> Future[OperationResult]:
        self._check_open()
        if isinstance(cookies, str):
            decoded = json.loads(cookies)
        else:
            decoded = [dict(cookie) for cookie in cookies]
        if not isinstance(decoded, list) or not all(isinstance(item, dict) for item in decoded):
            raise ValueError("cookies must be a JSON array of cookie objects")
        return self._runtime.call(
            lambda _playwright: self._import_cookies(handle, decoded)
        )

    async def import_cookies_async(
        self,
        handle: SessionHandle,
        cookies: str | Sequence[Mapping[str, Any]],
    ) -> OperationResult:
        return await asyncio.wrap_future(self.import_cookies(handle, cookies))

    def cancel_session(self, handle: SessionHandle) -> Future[OperationResult]:
        self._check_open()
        return self._runtime.call(lambda _playwright: self._cancel_session(handle))

    async def cancel_session_async(self, handle: SessionHandle) -> OperationResult:
        return await asyncio.wrap_future(self.cancel_session(handle))

    def close_session(self, handle: SessionHandle) -> Future[OperationResult]:
        self._check_open()
        return self._runtime.call(lambda _playwright: self._close_session(handle))

    async def close_session_async(self, handle: SessionHandle) -> OperationResult:
        return await asyncio.wrap_future(self.close_session(handle))

    def status(self) -> BrowserStatus:
        with self._status_lock:
            return BrowserStatus(
                runtime=self._runtime.snapshot(),
                active_sessions=self._active,
                opening_sessions=self._opening,
                profile_paths=self._profile_snapshot,
                closed=self._closed,
            )

    def shutdown(self, timeout: float = 10.0) -> BrowserStatus:
        if threading.get_ident() == self._runtime.snapshot().thread_id:
            raise RuntimeError("shutdown cannot block the browser runtime thread")
        with self._status_lock:
            if self._closed:
                return self.status_unlocked()
            self._closed = True
        try:
            future = self._runtime.call(lambda _playwright: self._close_all())
            future.result(timeout=timeout)
        finally:
            if self._owns_runtime:
                self._runtime.shutdown(timeout=timeout)
        return self.status()

    async def shutdown_async(self, timeout: float = 10.0) -> BrowserStatus:
        return await asyncio.to_thread(self.shutdown, timeout)

    def status_unlocked(self) -> BrowserStatus:
        return BrowserStatus(
            runtime=self._runtime.snapshot(),
            active_sessions=self._active,
            opening_sessions=self._opening,
            profile_paths=self._profile_snapshot,
            closed=self._closed,
        )

    def _check_open(self) -> None:
        with self._status_lock:
            if self._closed:
                raise BrowserServiceClosedError("browser service is closed")

    @staticmethod
    def _canonical_profile(path: str | os.PathLike[str]) -> str:
        return os.path.normcase(str(Path(path).expanduser().resolve()))

    async def _open(self, playwright: Any, config: BrowserSessionConfig) -> SessionResult:
        task = asyncio.current_task()
        if task is not None:
            self._opening_tasks.add(task)
        profile = self._canonical_profile(config.profile_path)
        if profile in self._profiles:
            if task is not None:
                self._opening_tasks.discard(task)
            raise ProfileInUseError(f"profile is already in use: {profile}")

        session_id = uuid.uuid4().hex
        self._profiles[profile] = session_id
        self._sync_status(opening_delta=1)
        context = None
        try:
            generation = self._generations.get(profile, 0) + 1
            self._generations[profile] = generation
            kwargs: dict[str, Any] = {
                "user_data_dir": profile,
                "headless": not config.headed,
                "args": list(config.args),
            }
            if config.executable_path is not None:
                kwargs["executable_path"] = str(config.executable_path)
            if config.viewport is not None:
                kwargs["viewport"] = {
                    "width": config.viewport[0],
                    "height": config.viewport[1],
                }
            if config.locale is not None:
                kwargs["locale"] = config.locale
            if config.timezone_id is not None:
                kwargs["timezone_id"] = config.timezone_id
            if config.geolocation is not None:
                kwargs["geolocation"] = dict(config.geolocation)
            if config.permissions:
                kwargs["permissions"] = list(config.permissions)
            if config.proxy is not None:
                kwargs["proxy"] = dict(config.proxy)

            context = await playwright.chromium.launch_persistent_context(**kwargs)

            # Prepare Anti-Detect configuration: write data.huynhthang for C++ HT Browser & inject JS stealth for standard Chrome
            try:
                exe_str = str(config.executable_path or "").lower()
                is_ht_browser = "orbita" in exe_str or "ht-browser" in exe_str or "donglao" in exe_str or "huynhthang" in exe_str
                from profile_config_engine import generate_stealth_profile_config, write_profile_config_files
                from vibe_stealth_engine import generate_stealth_js

                resolved_id = str(
                    config.account_uuid
                    or config.profile_name
                    or Path(profile).parent.name
                    or Path(profile).name
                )
                stealth_cfg = dict(config.stealth_config) if config.stealth_config else generate_stealth_profile_config(
                    account_uuid=resolved_id,
                    proxy_info=dict(config.proxy) if config.proxy else None,
                    geoip_info={
                        "timezone": config.timezone_id,
                        "latitude": config.geolocation.get("latitude") if config.geolocation else None,
                        "longitude": config.geolocation.get("longitude") if config.geolocation else None,
                    } if (config.timezone_id or config.geolocation) else None,
                    profile_name=str(config.profile_name or Path(profile).parent.name),
                )
                write_profile_config_files(profile, stealth_cfg)

                if not is_ht_browser:
                    stealth_js = generate_stealth_js(stealth_cfg)
                    await context.add_init_script(stealth_js)
            except Exception:
                pass

            pages = list(context.pages)
            page = pages[0] if pages else await context.new_page()
            handle = SessionHandle(session_id, generation, profile, config.mode)
            self._sessions[session_id] = _Session(handle, context, page)
            self._sync_status(opening_delta=-1)
            return SessionResult(handle, len(context.pages))
        except BaseException:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            self._profiles.pop(profile, None)
            self._sync_status(opening_delta=-1)
            raise
        finally:
            if task is not None:
                self._opening_tasks.discard(task)

    def _get_session(self, handle: SessionHandle) -> _Session:
        session = self._sessions.get(handle.session_id)
        if session is None or session.handle != handle:
            raise StaleSessionError("session handle is stale or closed")
        return session

    async def _run(
        self, handle: SessionHandle, operation: PageOperation[T]
    ) -> OperationResult:
        session = self._get_session(handle)
        task = asyncio.current_task()
        if task is not None:
            session.tasks.add(task)
        try:
            value = await operation(session.page)
            self._get_session(handle)
            return OperationResult(
                handle,
                _freeze_value(value, forbidden=(session.page, session.context)),
            )
        finally:
            if task is not None:
                session.tasks.discard(task)

    async def _export_cookies(self, handle: SessionHandle) -> CookieExport:
        session = self._get_session(handle)
        cookies = await session.context.cookies()
        payload = json.dumps(cookies, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return CookieExport(handle, payload)

    async def _import_cookies(
        self, handle: SessionHandle, cookies: list[dict[str, Any]]
    ) -> OperationResult:
        session = self._get_session(handle)
        before = await session.context.cookies()
        tiktok_before = [cookie for cookie in before if _is_tiktok_cookie(cookie)]
        try:
            await session.context.clear_cookies(domain=_TIKTOK_DOMAIN_PATTERN)
            await session.context.add_cookies(cookies)
            after = await session.context.cookies()
        except Exception:
            try:
                await session.context.clear_cookies(domain=_TIKTOK_DOMAIN_PATTERN)
                await session.context.add_cookies(tiktok_before)
            except Exception:
                pass
            raise
        report = _build_import_report(cookies, after)
        return OperationResult(handle, report)

    async def _cancel_session(self, handle: SessionHandle) -> OperationResult:
        session = self._get_session(handle)
        current = asyncio.current_task()
        tasks = [task for task in tuple(session.tasks) if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return OperationResult(handle, len(tasks))

    async def _close_session(self, handle: SessionHandle) -> OperationResult:
        session = self._get_session(handle)
        await self._dispose(session)
        return OperationResult(handle, None)

    async def _dispose(self, session: _Session) -> None:
        self._sessions.pop(session.handle.session_id, None)
        self._profiles.pop(session.handle.profile_path, None)
        current = asyncio.current_task()
        tasks = [task for task in tuple(session.tasks) if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await session.context.close()
        finally:
            self._sync_status()

    async def _close_all(self) -> None:
        current = asyncio.current_task()
        opening = [task for task in tuple(self._opening_tasks) if task is not current]
        for task in opening:
            task.cancel()
        if opening:
            await asyncio.gather(*opening, return_exceptions=True)
        sessions = tuple(self._sessions.values())
        results = await asyncio.gather(
            *(self._dispose(session) for session in sessions),
            return_exceptions=True,
        )
        errors = [result for result in results if isinstance(result, BaseException)]
        if errors:
            raise RuntimeError(f"failed to close {len(errors)} browser context(s)") from errors[0]

    def _sync_status(self, opening_delta: int = 0) -> None:
        with self._status_lock:
            self._opening += opening_delta
            self._active = len(self._sessions)
            self._profile_snapshot = tuple(sorted(self._profiles))


_TIKTOK_DOMAIN_PATTERN = re.compile(r"(^|\.)tiktok\.com$")


def _is_tiktok_cookie(cookie):
    try:
        return bool(_TIKTOK_DOMAIN_PATTERN.match(str(cookie.get("domain", ""))))
    except (AttributeError, ValueError):
        return False


def _stored_cookie_key(cookie):
    """Normalized identity of a cookie as Chromium would keep it.

    ``url``-based cookies are derived to their host domain. Domain keys are
    compared without a leading dot and lowercased so ``.tiktok.com``,
    ``tiktok.com`` and ``www.tiktok.com`` resolve consistently."""
    name = str(cookie.get("name", "") or "")
    raw_domain = str(cookie.get("domain", "") or "")
    url = cookie.get("url")
    if not raw_domain and url:
        try:
            from urllib.parse import urlparse

            raw_domain = urlparse(str(url)).hostname or ""
        except Exception:
            raw_domain = ""
    domain = str(raw_domain or "").strip().lower().lstrip(".")
    path = str(cookie.get("path", "/") or "/")
    value = str(cookie.get("value", "") or "")
    return (name, domain, path, value)


def _build_import_report(requested, present):
    present_keys = {_stored_cookie_key(cookie) for cookie in present}
    accepted = sum(
        1 for cookie in requested if _stored_cookie_key(cookie) in present_keys
    )
    from patchright_cookie_codec import TIKTOK_AUTH_COOKIES

    auth_names = [c.get("name") for c in requested if c.get("name") in TIKTOK_AUTH_COOKIES]
    auth_accepted = [
        name for name in auth_names if any(c.get("name") == name for c in present)
    ]
    missing = tuple(name for name in auth_names if name not in auth_accepted)
    return CookieImportReport(
        requested=len(requested),
        accepted=accepted,
        auth_requested=len(auth_names),
        auth_accepted=len(auth_accepted),
        missing_auth_names=missing,
    )


def _freeze_value(value: Any, forbidden: tuple[Any, ...] = ()) -> Any:
    if any(value is item for item in forbidden):
        raise TypeError("browser objects cannot cross the runtime thread boundary")
    value_type = type(value)
    if value_type.__name__ in {"Page", "BrowserContext", "Browser", "Playwright"} and (
        value_type.__module__.startswith("patchright")
        or value_type.__module__.startswith("playwright")
    ):
        raise TypeError("browser objects cannot cross the runtime thread boundary")
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(item, forbidden) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item, forbidden) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item, forbidden) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        params = getattr(type(value), "__dataclass_params__", None)
        if params is None or not params.frozen:
            raise TypeError("operation result dataclasses must be frozen")
        replacements = {
            item.name: _freeze_value(getattr(value, item.name), forbidden)
            for item in fields(value)
        }
        return replace(value, **replacements)
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    raise TypeError(
        f"operation result type {type(value).__name__} is not safely immutable"
    )
