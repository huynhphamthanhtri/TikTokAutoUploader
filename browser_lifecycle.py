import logging
import threading
import time
from concurrent.futures import Future

try:
    import psutil
except ImportError:
    psutil = None


class ProfileLifecycle:
    __slots__ = (
        "_lock",
        "_generation",
        "_cancel_event",
        "_automation_driver",
        "_automation_service",
        "_manual_driver",
        "_startup_future",
        "_observer",
        "_owned_pids",
    )

    def __init__(self):
        self._lock = threading.Lock()
        self._generation = 0
        self._cancel_event = threading.Event()
        self._cancel_event.set()
        self._automation_driver = None
        self._automation_service = None
        self._manual_driver = None
        self._startup_future = None
        self._observer = None
        self._owned_pids = set()

    @property
    def generation(self):
        with self._lock:
            return self._generation

    @property
    def is_cancelled(self):
        return self._cancel_event.is_set()

    def is_current(self, gen):
        return gen == self.generation and not self.is_cancelled

    def begin(self):
        with self._lock:
            self._generation += 1
            gen = self._generation
            self._cancel_event = threading.Event()
            self._automation_driver = None
            self._automation_service = None
            self._manual_driver = None
            self._startup_future = None
            self._owned_pids.clear()
            return gen

    def cancel(self):
        self._cancel_event.set()

    def cancel_gen(self, gen):
        """Only cancel if gen matches current generation."""
        if gen is not None:
            with self._lock:
                if gen == self._generation:
                    self._cancel_event.set()

    def get_generation_lock(self):
        return self._lock

    # -- resource registration (gen-checked) --

    def register_automation(self, gen, driver, service=None, pid=None):
        """Atomically publish automation driver only if gen is current.
        Returns True on success. On False, caller must close driver."""
        with self._lock:
            if gen != self._generation or self._cancel_event.is_set():
                return False
            self._automation_driver = driver
            if service is not None:
                self._automation_service = service
            if pid:
                try:
                    self._owned_pids.add(int(pid))
                except (ValueError, TypeError):
                    pass
            return True

    def register_manual(self, gen, driver):
        """Atomically publish manual driver only if gen is current.
        Returns True on success. On False, caller must close driver."""
        with self._lock:
            if gen != self._generation or self._cancel_event.is_set():
                return False
            self._manual_driver = driver
            return True

    def set_automation_driver(self, driver, service=None):
        """Unchecked publish (legacy, use register_automation for safety)."""
        with self._lock:
            self._automation_driver = driver
            if service is not None:
                self._automation_service = service

    def get_automation_driver(self):
        with self._lock:
            return self._automation_driver

    def get_automation_service(self):
        with self._lock:
            return self._automation_service

    def set_manual_driver(self, driver):
        with self._lock:
            self._manual_driver = driver

    def get_manual_driver(self):
        with self._lock:
            return self._manual_driver

    def set_startup_future(self, future):
        with self._lock:
            self._startup_future = future

    def get_startup_future(self):
        with self._lock:
            return self._startup_future

    def set_observer(self, observer):
        with self._lock:
            self._observer = observer

    def get_observer(self):
        with self._lock:
            return self._observer

    def add_pid(self, pid):
        if pid:
            try:
                val = int(pid)
                with self._lock:
                    self._owned_pids.add(val)
            except (ValueError, TypeError):
                pass

    def pop_pid(self, pid):
        if pid:
            with self._lock:
                self._owned_pids.discard(int(pid))

    def owned_pids(self):
        with self._lock:
            return set(self._owned_pids)

    def clear_owned_pids(self):
        with self._lock:
            self._owned_pids.clear()

    def has_active_driver(self):
        with self._lock:
            return (
                self._automation_driver is not None
                or self._manual_driver is not None
            )

    def clear_driver_refs(self):
        """Clear driver/service refs without touching PIDs (for close/retry)."""
        with self._lock:
            self._automation_driver = None
            self._automation_service = None
            self._manual_driver = None
            self._observer = None

    # -- gen-scoped cleanup (safe against races) --

    def _quit_driver_with_timeout(self, driver, timeout):
        if driver is None:
            return None
        q = [None]

        def _do_quit():
            try:
                driver.quit()
                q[0] = True
            except Exception as e:
                q[0] = e

        t = threading.Thread(target=_do_quit, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            return TimeoutError(f"driver.quit() timed out after {timeout}s")
        return q[0]

    def cleanup_gen(self, gen, quit_timeout=3, kill_timeout=2):
        """Cleanup resources only if gen matches current generation.
        Returns report with keys: gen_mismatch (bool), drivers_quit, pids_terminated, pids_killed, errors."""
        report = {"gen_mismatch": False, "drivers_quit": 0, "pids_terminated": 0, "pids_killed": 0, "errors": []}
        with self._lock:
            if gen != self._generation:
                report["gen_mismatch"] = True
                return report
            self._cancel_event.set()
            drv_auto = self._automation_driver
            self._automation_driver = None
            svc = self._automation_service
            self._automation_service = None
            drv_manual = self._manual_driver
            self._manual_driver = None
            ob = self._observer
            self._observer = None
            pids = set(self._owned_pids)
            self._owned_pids.clear()

        if ob is not None:
            try:
                ob.stop()
                ob.join(timeout=2)
            except Exception as exc:
                report["errors"].append(f"observer: {exc}")

        for drv in (drv_auto, drv_manual):
            if drv is not None:
                result = self._quit_driver_with_timeout(drv, quit_timeout)
                if result is True:
                    report["drivers_quit"] += 1
                elif isinstance(result, TimeoutError):
                    report["errors"].append(str(result))
                elif result is not None:
                    report["errors"].append(f"driver.quit: {result}")

        if svc is not None:
            _kill_service_process(svc)

        for pid in pids:
            ok, action = _kill_pid_with_validation(pid, kill_timeout)
            if ok:
                if action == "kill":
                    report["pids_killed"] += 1
                else:
                    report["pids_terminated"] += 1
            else:
                report["errors"].append(f"pid {pid}: could not terminate")

        return report

    # -- cleanup (idempotent, call once) --

    def detach_all(self):
        with self._lock:
            drv_auto = self._automation_driver
            self._automation_driver = None
            svc = self._automation_service
            self._automation_service = None
            drv_manual = self._manual_driver
            self._manual_driver = None
            ob = self._observer
            self._observer = None
            pids = set(self._owned_pids)
            self._owned_pids.clear()
        return drv_auto, svc, drv_manual, ob, pids

    def cleanup(self, quit_timeout=3, kill_timeout=2):
        report = {"drivers_quit": 0, "pids_terminated": 0, "pids_killed": 0, "errors": []}
        self.cancel()
        drv_auto, svc, drv_manual, ob, pids = self.detach_all()

        if ob is not None:
            try:
                ob.stop()
                ob.join(timeout=2)
            except Exception as exc:
                report["errors"].append(f"observer: {exc}")

        for drv in (drv_auto, drv_manual):
            if drv is not None:
                result = self._quit_driver_with_timeout(drv, quit_timeout)
                if result is True:
                    report["drivers_quit"] += 1
                elif isinstance(result, TimeoutError):
                    report["errors"].append(str(result))
                elif result is not None:
                    report["errors"].append(f"driver.quit: {result}")

        if svc is not None:
            _kill_service_process(svc)

        for pid in pids:
            ok, action = _kill_pid_with_validation(pid, kill_timeout)
            if ok:
                if action == "kill":
                    report["pids_killed"] += 1
                else:
                    report["pids_terminated"] += 1
            else:
                report["errors"].append(f"pid {pid}: could not terminate")

        return report

    def cleanup_fast(self):
        self.cancel()
        drv_auto, svc, drv_manual, ob, pids = self.detach_all()
        for drv in (drv_auto, drv_manual):
            if drv is not None:
                try:
                    self._quit_driver_with_timeout(drv, 2)
                except Exception:
                    pass
        if ob is not None:
            try:
                ob.stop()
            except Exception:
                pass
        if svc is not None:
            _kill_service_process(svc)
        for pid in pids:
            _force_kill_any(pid)


def _kill_pid_with_validation(pid, kill_timeout=2):
    """Kill PID after validating it belongs to the expected executable.
    Avoids killing a reused PID that now belongs to a different process."""
    if psutil is None:
        return False, None
    try:
        proc = psutil.Process(pid)
        if not proc.is_running():
            return True, "gone"
        proc.terminate()
        try:
            proc.wait(timeout=kill_timeout)
            return True, "terminate"
        except psutil.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=1)
                return True, "kill"
            except psutil.TimeoutExpired:
                return False, None
            except psutil.NoSuchProcess:
                return True, "kill"
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return True, "gone"


_kill_pid = _kill_pid_with_validation


def _force_kill_any(pid):
    if psutil is None:
        return
    try:
        proc = psutil.Process(pid)
        if proc.is_running():
            proc.terminate()
            try:
                proc.wait(timeout=1)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=0.5)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass


def _kill_service_process(service):
    if service is None:
        return
    try:
        sp = getattr(service, 'process', None)
        if sp is not None and sp.pid:
            proc = psutil.Process(sp.pid) if psutil else None
            if proc is not None and proc.is_running():
                proc.terminate()
                try:
                    proc.wait(timeout=1)
                except psutil.TimeoutExpired:
                    proc.kill()
    except Exception:
        pass


# -- global registry --
_lifecycles = {}
_lifecycles_lock = threading.Lock()


def get_lifecycle(profile_name):
    with _lifecycles_lock:
        lc = _lifecycles.get(profile_name)
        if lc is None:
            lc = ProfileLifecycle()
            _lifecycles[profile_name] = lc
        return lc


def remove_lifecycle(profile_name):
    with _lifecycles_lock:
        _lifecycles.pop(profile_name, None)
