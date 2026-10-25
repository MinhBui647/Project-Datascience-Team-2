"""Windows scheduler contracts using mocked task registration and Python launch."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt" and POWERSHELL.is_file(), "Windows PowerShell required")
class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="EPL scheduler ")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.scheduling = self.root / "scripts/scheduling"
        self.scheduling.mkdir(parents=True)
        for filename in ("install_windows_task.ps1", "run_windows_task.ps1"):
            shutil.copyfile(ROOT / "scripts/scheduling" / filename, self.scheduling / filename)
        orchestration = self.root / "scripts/orchestration"
        orchestration.mkdir()
        for name in ("auto_ingest.py", "unified_incremental_runner.py"):
            (orchestration / name).write_text("# Never executed by these tests\n")
        python = self.root / "venv/Scripts/python.exe"
        python.parent.mkdir(parents=True)
        python.touch()

    def run_ps(self, command):
        return subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )

    def installer(self, flags="", mocks=""):
        return self.run_ps(mocks + "\n& " + quote(self.scheduling / "install_windows_task.ps1") + " " + flags)

    def status(self):
        files = list((self.root / "reports/scheduled_tasks").glob("*.status.json"))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text(encoding="utf-8-sig"))

    def test_preview_preserves_readonly_default_and_does_not_register(self):
        proc = self.installer(mocks="function Get-ScheduledTask { throw 'Preview accessed tasks' }")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Mode: Check; read-only", proc.stdout)
        self.assertIn("scripts.orchestration.unified_incremental_runner", proc.stdout)
        self.assertIn("PREVIEW ONLY", proc.stdout)
        self.assertFalse((self.root / "reports").exists())

    def test_preview_auto_mode(self):
        proc = self.installer("-Mode AutoIngest -Replace")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("scripts.orchestration.auto_ingest", proc.stdout)
        self.assertIn("will be replaced", proc.stdout)

    def test_install_refuses_unrequested_overwrite(self):
        proc = self.installer("-Install", "function Get-ScheduledTask { [pscustomobject]@{ State='Ready' } }")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("use -Replace -Install", proc.stderr)

    def test_replace_does_not_interrupt_running_task(self):
        proc = self.installer("-Mode AutoIngest -Replace -Install", "function Get-ScheduledTask { [pscustomobject]@{ State='Running' } }")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("wait for it to finish", proc.stderr)

    def test_registration_uses_wrapper_missed_runs_locking_and_no_retry(self):
        mocks = """
function Get-ScheduledTask { param($TaskName) [pscustomobject]@{ State='Ready'; TaskName=$TaskName } }
function New-ScheduledTaskAction {
    param($Execute, $Argument, $WorkingDirectory)
    if ($Execute -notlike '*powershell.exe' -or $Argument -notlike '*run_windows_task.ps1* -Mode AutoIngest') { throw 'Wrong action' }
    if ($WorkingDirectory -ne ROOT_TOKEN) { throw 'Wrong working directory' }
    [pscustomobject]@{ Execute=$Execute; Arguments=$Argument }
}
function New-ScheduledTaskTrigger { param([switch]$Daily, $At) if (!$Daily -or $At.ToString('HH:mm') -ne '19:00') { throw 'Wrong daily schedule' }; 'trigger' }
function New-ScheduledTaskSettingsSet {
    param([switch]$StartWhenAvailable, $MultipleInstances, $ExecutionTimeLimit, [switch]$AllowStartIfOnBatteries, [switch]$DontStopIfGoingOnBatteries)
    if (!$StartWhenAvailable -or $MultipleInstances -ne 'IgnoreNew' -or $ExecutionTimeLimit.TotalHours -ne 12) { throw 'Wrong settings' }
    'settings'
}
function New-ScheduledTaskPrincipal { param($UserId, $LogonType, $RunLevel) 'principal' }
function Register-ScheduledTask {
    param($TaskName, $Action, $Trigger, $Settings, $Principal, [switch]$Force)
    if (!$Force -or $TaskName -ne 'EPL Raw Storage - Safe Revision Check') { throw 'Replacement not explicit' }
    Write-Host 'MOCK_REGISTRATION_OK'
}
""".replace("ROOT_TOKEN", quote(self.root))
        proc = self.installer("-Mode AutoIngest -Replace -Install", mocks)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("MOCK_REGISTRATION_OK", proc.stdout)
        self.assertIn("TASK REGISTRATION: PASS (AutoIngest)", proc.stdout)

    def test_wrapper_propagates_python_failure_and_captures_logs(self):
        mocks = """
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$Wait, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError)
    if ($ArgumentList -notcontains '--run' -or $ArgumentList -notcontains 'scripts.orchestration.auto_ingest') { throw 'Wrong module' }
    if ($WindowStyle -ne 'Hidden' -or !$Wait -or !$PassThru) { throw 'Wrong process settings' }
    'stdout fixture' | Set-Content -LiteralPath $RedirectStandardOutput
    'stderr fixture' | Set-Content -LiteralPath $RedirectStandardError
    [pscustomobject]@{ ExitCode=2 }
}
"""
        proc = self.run_ps(mocks + "\n& " + quote(self.scheduling / "run_windows_task.ps1") + " -Mode AutoIngest\nexit $LASTEXITCODE")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        status = self.status()
        self.assertEqual(status["exit_code"], 2)
        self.assertEqual(status["mode"], "AutoIngest")
        self.assertIsNone(status["failure_type"])
        logs = self.root / "reports/scheduled_tasks"
        self.assertEqual(len(list(logs.glob("*.stdout.log"))), 1)
        self.assertEqual(len(list(logs.glob("*.stderr.log"))), 1)

    def test_wrapper_monitor_success_preserves_zero_status(self):
        mocks = """
function Start-Process {
    param($FilePath, $ArgumentList, $WorkingDirectory, $WindowStyle, [switch]$Wait, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError)
    if ($ArgumentList -notcontains '--check' -or $ArgumentList -contains '--run') { throw 'Monitor granted write mode' }
    [pscustomobject]@{ ExitCode=0 }
}
"""
        proc = self.run_ps(mocks + "\n& " + quote(self.scheduling / "run_windows_task.ps1") + "\nexit $LASTEXITCODE")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.status()["exit_code"], 0)

    def test_wrapper_launch_error_has_nonzero_status_without_secret(self):
        mocks = "function Start-Process { throw 'secret-password-sentinel' }"
        proc = self.run_ps(mocks + "\n& " + quote(self.scheduling / "run_windows_task.ps1") + " -Mode AutoIngest\nexit $LASTEXITCODE")
        self.assertEqual(proc.returncode, 3)
        self.assertNotIn("secret-password-sentinel", proc.stdout + proc.stderr)
        status = self.status()
        self.assertEqual(status["exit_code"], 3)
        self.assertIsNotNone(status["failure_type"])


if __name__ == "__main__":
    unittest.main()
