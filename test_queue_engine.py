"""Queue scheduler tests. Run with: python test_queue_engine.py"""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

_DATA = tempfile.mkdtemp(prefix="bulk_studio_queue_")
os.environ["STUDIO_DATA"] = _DATA

from app import core, worker


def add_projects(count):
    now = time.time()
    for index in range(count):
        path = str(Path(_DATA) / f"project-{index + 1}")
        Path(path).mkdir(parents=True, exist_ok=True)
        core.db(
            "INSERT INTO projects(path,name,status,category,message,output,progress,music,"
            "queued_at,updated,info,overrides,force) VALUES(?,?, 'ready','','', '',0,'',"
            "NULL,?,'{}','{}',0)",
            (path, Path(path).name, now),
        )


def wait_until(predicate, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


class QueueEngineTests(unittest.TestCase):
    def setUp(self):
        core.db("DELETE FROM projects")
        core.db("DELETE FROM kv")
        self.original_settings = core.load_settings
        self.original_process = worker.process
        self.active = 0
        self.max_active = 0
        self.guard = threading.Lock()
        self.duration = 0.16

    def tearDown(self):
        worker.process = self.original_process
        core.load_settings = self.original_settings

    def fake_process(self, path, ctl):
        with self.guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            deadline = time.monotonic() + self.duration
            while time.monotonic() < deadline:
                ctl.check()
                time.sleep(0.01)
            ctl.check()
            return Path(path) / "rendered.mp4", None
        finally:
            with self.guard:
                self.active -= 1

    def make_manager(self, workers):
        core.load_settings = lambda: {"batch": {"workers": workers}}
        worker.process = self.fake_process
        manager = worker.Manager()
        manager.start()
        return manager

    def test_five_projects_at_each_parallelism(self):
        for parallel in (1, 2, 3, 4):
            with self.subTest(parallel=parallel):
                core.db("DELETE FROM projects")
                with self.guard:
                    self.max_active = 0
                add_projects(5)
                manager = self.make_manager(parallel)
                try:
                    self.assertEqual(manager.enqueue(None), 5)
                    self.assertTrue(wait_until(
                        lambda: core.db(
                            "SELECT COUNT(*) n FROM projects WHERE status='completed'"
                        )[0]["n"] == 5
                    ), "not all five projects completed")
                    self.assertLessEqual(self.max_active, parallel)
                    self.assertEqual(
                        core.db(
                            "SELECT COUNT(*) n FROM projects WHERE status IN ('pending','processing')"
                        )[0]["n"], 0
                    )
                finally:
                    manager.shutdown()
                    if manager.dispatcher:
                        manager.dispatcher.join(timeout=2)

    def test_stop_then_immediate_restart(self):
        add_projects(5)
        manager = self.make_manager(2)
        try:
            self.assertEqual(manager.enqueue(None), 5)
            self.assertTrue(wait_until(lambda: bool(manager.ctls)))
            manager.stop()
            # Start again immediately, while the old render threads are still
            # unwinding. The queue must defer active paths without losing them.
            self.assertEqual(manager.enqueue(None), 5)
            self.assertTrue(wait_until(
                lambda: core.db(
                    "SELECT COUNT(*) n FROM projects WHERE status='completed'"
                )[0]["n"] == 5
            ))
        finally:
            manager.shutdown()
            if manager.dispatcher:
                manager.dispatcher.join(timeout=2)

    def test_reset_waits_for_active_work_and_clears_projects(self):
        add_projects(3)
        manager = self.make_manager(2)
        try:
            manager.enqueue(None)
            self.assertTrue(wait_until(lambda: bool(manager.ctls)))
            manager.reset_all(delete_videos=False)
            self.assertEqual(
                core.db("SELECT COUNT(*) n FROM projects")[0]["n"], 0
            )
            self.assertFalse(manager.ctls)
            self.assertFalse(manager.resetting)
        finally:
            manager.shutdown()
            if manager.dispatcher:
                manager.dispatcher.join(timeout=2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
