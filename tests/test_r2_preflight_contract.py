"""R2.0: preflight cache_dir forwarding regression."""
import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.tools import register_incremental_snapshot as m
from tests import test_register_incremental_snapshot_m46 as fixture


class PreflightCacheContract(unittest.TestCase):
    def test_explicit_cache_dir_forwarded_once(self):
        req = fixture.valid_req()
        cache = "reports/r2_custom_cache"
        command = ["python", "--plan", "--cache-dir", cache]
        result = SimpleNamespace(
            stdout=fixture.PreflightTests()._stdout(req),
            stderr="",
            returncode=0,
        )

        with patch.object(
            m, "preflight_command", return_value=command
        ) as builder, patch.object(
            m.subprocess, "run", return_value=result
        ) as runner, contextlib.redirect_stdout(io.StringIO()):
            m.run_preflight(req, cache_dir=cache)

        builder.assert_called_once_with(req, cache_dir=cache)
        runner.assert_called_once()
        self.assertEqual(runner.call_args.args[0], command)
