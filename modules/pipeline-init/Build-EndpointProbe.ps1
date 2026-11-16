# Existing Windows Framework probe used by explicit EDID preflight/inspect only.
# Not the native audio capture Hook, and not a .NET 10/WPF build.
param()
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$compiler=Join-Path $env:SystemRoot 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
if(-not(Test-Path -LiteralPath $compiler)){throw 'Windows .NET Framework x64 compiler is required for this legacy endpoint preflight probe'}
$output=Join-Path $root 'work\OemEndpointProbe.exe'
$probe=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'Probe.cs')).Path
$privateProbe=(Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'PrivateBlockProbe.cs')).Path
New-Item -ItemType Directory -Force (Split-Path $output) | Out-Null
& $compiler /nologo /platform:x64 /r:System.Web.Extensions.dll "/out:$output" $probe $privateProbe
if($LASTEXITCODE){throw 'Endpoint preflight probe build failed'}
Write-Output 'Built legacy read-only endpoint probe. No endpoint opened or changed by this build.'
