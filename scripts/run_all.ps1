<#
.SYNOPSIS
    运行完整治理流水线（接入 → 标准化 → … → 报告）。

.DESCRIPTION
    先做环境自检，再执行 run.py。数据路径优先取参数，其次环境变量，最后代码默认值。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1 -CohortSize 100

.EXAMPLE
    .\scripts\run_all.ps1 -CohortSize 20 -Python "C:\Python313\python.exe"
#>
param(
    [int]$CohortSize = 100,
    [string]$Python = "python",
    [string]$MimicRoot = $env:MIMIC_ROOT,
    [string]$NoteRoot = $env:MIMIC_NOTE_ROOT,
    [string]$Output = $env:GOVERNANCE_OUTPUT
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

$runArgs = @("run.py", "--cohort-size", $CohortSize)
if ($MimicRoot) { $runArgs += @("--mimic-root", $MimicRoot) }
if ($NoteRoot) { $runArgs += @("--note-root", $NoteRoot) }
if ($Output) { $runArgs += @("--output", $Output) }

Push-Location $repo
try {
    Write-Host "== 环境自检 ==" -ForegroundColor Cyan
    & $Python "scripts\check_env.py"
    if ($LASTEXITCODE -ne 0) { throw "环境自检未通过（退出码 $LASTEXITCODE）" }

    Write-Host "`n== 治理流水线 ==" -ForegroundColor Cyan
    & $Python @runArgs
    if ($LASTEXITCODE -ne 0) { throw "流水线失败（退出码 $LASTEXITCODE）" }

    Write-Host "`n完成。产物在 output/ 下，注意不要提交。" -ForegroundColor Green
}
finally {
    Pop-Location
}
