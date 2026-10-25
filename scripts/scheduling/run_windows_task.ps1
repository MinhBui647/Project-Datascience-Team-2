# Scheduled action: preserve Python's exit code and retain output for diagnosis.
[CmdletBinding()]
param(
    [ValidateSet('Check', 'AutoIngest')]
    [string]$Mode = 'Check'
)
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $root 'venv\Scripts\python.exe'
$logDirectory = Join-Path $root 'reports\scheduled_tasks'
$started = [datetime]::UtcNow
$runId = '{0}-{1}-{2}' -f $started.ToString('yyyyMMddTHHmmssfffZ'), $Mode, $PID
$exitCode = 3
$failureType = $null
try {
    if (!(Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Python venv not found' }
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $module = if ($Mode -eq 'AutoIngest') { 'auto_ingest' } else { 'unified_incremental_runner' }
    $operation = if ($Mode -eq 'AutoIngest') { '--run' } else { '--check' }
    $pythonArguments = @('-B', '-m', "scripts.orchestration.$module", $operation, '--revision-ref', 'main')
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    $process = Start-Process -FilePath $python -ArgumentList $pythonArguments -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput (Join-Path $logDirectory "$runId.stdout.log") -RedirectStandardError (Join-Path $logDirectory "$runId.stderr.log")
    if ($null -eq $process.ExitCode) { throw 'Python process exit status unavailable' }
    $exitCode = [int]$process.ExitCode
} catch {
    # Do not copy exception messages that might contain connection credentials.
    $failureType = $_.Exception.GetType().FullName
    [Console]::Error.WriteLine("Scheduled task failed: $failureType; inspect local logs")
} finally {
    if (Test-Path -LiteralPath $logDirectory -PathType Container) {
        $status = [ordered]@{
            run_id = $runId
            mode = $Mode
            started_at = $started.ToString('o')
            finished_at = [datetime]::UtcNow.ToString('o')
            exit_code = $exitCode
            failure_type = $failureType
        }
        try {
            $status | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logDirectory "$runId.status.json") -Encoding UTF8
        } catch {
            [Console]::Error.WriteLine('Unable to save scheduled-task status')
            if ($exitCode -eq 0) { $exitCode = 3 }
        }
    }
}
exit $exitCode
