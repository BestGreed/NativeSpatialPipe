param([Parameter(Mandatory=$true)][string]$Python,
      [Parameter(Mandatory=$true)][string]$HostExecutable,
      [switch]$Child)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
Set-Location -LiteralPath $root
if($Child) {
    foreach($language in @('en','zh','fr')) {
        & $Python modules/zig-scheduler/Test-WebHost.py --host $HostExecutable --language $language --report "build/ci-logs/web-host-$language.json"
        if($LASTEXITCODE){exit $LASTEXITCODE}
    }
    exit 0
}
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=[Security.Principal.WindowsPrincipal]::new($identity)
if(-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    & $PSCommandPath -Python $Python -HostExecutable $HostExecutable -Child
    exit $LASTEXITCODE
}
if($env:GITHUB_ACTIONS -ne 'true'){throw 'Run web contracts from a non-elevated user session'}
# GitHub's hosted Windows runner is elevated. Exercise the product's actual
# non-elevated boundary instead of weakening WebHostRequiresUserToken.
$name='nspweb'+[Guid]::NewGuid().ToString('N').Substring(0,8)
$password=[Guid]::NewGuid().ToString('N')+'aA1!'
$secret=ConvertTo-SecureString $password -AsPlainText -Force
$credential=[Management.Automation.PSCredential]::new("$env:COMPUTERNAME\$name",$secret)
$account=$null
try {
    $account=New-LocalUser -Name $name -Password $secret -Description 'Ephemeral NSP web contract user' -PasswordNeverExpires
    $group=Get-LocalGroup -SID 'S-1-5-32-545'
    Add-LocalGroupMember -Group $group -Member $name
    & icacls.exe $root /grant "${env:COMPUTERNAME}\${name}:(OI)(CI)M" /Q
    if($LASTEXITCODE){throw 'Cannot grant fixture checkout access'}
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -Python "'+$Python+'" -HostExecutable "'+$HostExecutable+'" -Child'
    $process=Start-Process -FilePath powershell.exe -ArgumentList $arguments -Credential $credential -WorkingDirectory $root -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput "$root/build/ci-logs/web-user.txt" -RedirectStandardError "$root/build/ci-logs/web-user-errors.txt"
    Get-Content "$root/build/ci-logs/web-user.txt"
    Get-Content "$root/build/ci-logs/web-user-errors.txt"
    if($process.ExitCode){throw "User-token web contracts failed: $($process.ExitCode)"}
} finally {
    if($account) {
        & icacls.exe $root /remove:g "*$($account.SID.Value)" /Q | Out-Null
        Remove-LocalUser -Name $name
    }
}
