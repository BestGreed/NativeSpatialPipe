param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ToolArguments)
$ErrorActionPreference='Stop'
$lsacRoot=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$lsacPython=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$lsacOldPath=$env:PYTHONPATH
try {
    $env:PYTHONPATH=(Join-Path $lsacRoot 'modules/lsac-capture')+';'+(Join-Path $lsacRoot 'work/lsac-python')
    $env:PYTHONIOENCODING='utf-8'
    & $lsacPython -u -m lsac_capture.tools_cli @ToolArguments
    if($LASTEXITCODE){throw "LSAC tools failed with exit code $LASTEXITCODE"}
} finally { $env:PYTHONPATH=$lsacOldPath }
