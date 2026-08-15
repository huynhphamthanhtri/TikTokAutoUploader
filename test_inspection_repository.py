import json
import tempfile
import unittest
from pathlib import Path

from inspection_repository import InspectionRepository
from tiktok_capability_models import (
    AccountCapabilities,
    CapabilityResult,
    CapabilityState,
    DashboardCapability,
    MoneyAmount,
)


class InspectionRepositoryTests(unittest.TestCase):
    def test_save_and_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = InspectionRepository(Path(directory) / "inspection.db")
            capabilities = AccountCapabilities(
                (
                    CapabilityResult(
                        "dashboard",
                        CapabilityState.SUCCESS,
                        DashboardCapability(total_amount=MoneyAmount(0, "$", formatted="$0.00")),
                        endpoint_id="dashboard_overview",
                        checked_at="2026-08-15T00:00:00Z",
                        schema_hash="abc",
                    ),
                )
            )
            repository.save_capabilities("uuid-1", "AUTO 6", capabilities)
            latest = repository.latest("uuid-1")
            self.assertEqual(latest["dashboard"]["state"], "SUCCESS")
            self.assertEqual(latest["dashboard"]["payload"]["total_amount"]["minor_units"], 0)

    def test_rejects_forbidden_payload_key(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = InspectionRepository(Path(directory) / "inspection.db")
            capabilities = AccountCapabilities(
                (CapabilityResult("bad", CapabilityState.SUCCESS, {"cookie": "secret"}),)
            )
            with self.assertRaises(ValueError):
                repository.save_capabilities("uuid-1", "AUTO 6", capabilities)

    def test_schema_initialization_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = InspectionRepository(Path(directory) / "inspection.db")
            repository.initialize()
            repository.initialize()
            self.assertTrue(repository.path.exists())


if __name__ == "__main__":
    unittest.main()
