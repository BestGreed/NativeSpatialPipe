param([Parameter(ValueFromRemainingArguments=$true)][string[]]$ScanArguments)
$ErrorActionPreference='Stop'
$scanRoot=(Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$scanPython=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if(-not(Test-Path -LiteralPath $scanPython)){throw 'Python runtime missing; select an existing Python 3.12 runtime explicitly.'}
$scanOldPythonPath=$env:PYTHONPATH
try {
    $env:PYTHONPATH=(Join-Path $scanRoot 'modules/lsac-capture')+';'+(Join-Path $scanRoot 'work/lsac-python')
    $env:PYTHONIOENCODING='utf-8'
    & $scanPython -m lsac_capture @ScanArguments
    if($LASTEXITCODE){throw "LSAC command failed with exit code $LASTEXITCODE"}
} finally { $env:PYTHONPATH=$scanOldPythonPath }
