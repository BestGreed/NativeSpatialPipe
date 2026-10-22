param([string]$Installation='',[switch]$SkipHookBuild,[switch]$FsaOnly,[switch]$LegacyPythonScheduler)
$ErrorActionPreference='Stop'
$root=(Resolve-Path "$PSScriptRoot/../..").Path
$llvm=$env:NSP_LLVM_BIN
$clang=Join-Path $llvm 'clang.exe'
$destination=Join-Path $root 'build/fsa-integration'
$bin=Join-Path $destination 'bin'
$src=Join-Path $PSScriptRoot 'native'
New-Item -ItemType Directory -Force $bin | Out-Null
if(-not $FsaOnly){
& powershell -NoProfile -ExecutionPolicy Bypass -File "$root/tools/zig-scheduler/Build.ps1"
if($LASTEXITCODE){throw 'Zig scheduler build failed'}
& powershell -NoProfile -ExecutionPolicy Bypass -File "$root/tools/telemetry/Build-Meter.ps1"
if($LASTEXITCODE){throw 'Telemetry meter build failed'}
if(-not $SkipHookBuild){
 & powershell -NoProfile -ExecutionPolicy Bypass -File "$root/tools/endpoint-probe/Build-NativeHook.ps1" -Managed -TestFixtures
 if($LASTEXITCODE){throw 'Managed hook build failed'}
}
}
$candidate=Join-Path $bin 'FinalSpatialAudio.exe'
$flags=@('-O2','-DUNICODE','-D_UNICODE','-D_WIN32_WINNT=0x0A00','-std=c11','-Wall','-Wextra','-Werror')
Push-Location $src
try {
 & "$llvm/llvm-windres.exe" -i final_spatial_audio.rc -o "$bin/fsa-resource.o"
 if($LASTEXITCODE){throw 'Resource build failed'}
 & "$llvm/llvm-windres.exe" -DFSA_TEST_RESOURCES -i final_spatial_audio.rc -o "$bin/fsa-test-resource.o"
 if($LASTEXITCODE){throw 'Test resource build failed'}
}finally{Pop-Location}
& $clang @flags "$src/final_spatial_audio.c" "$src/fluent_menu.c" "$bin/fsa-resource.o" -lole32 -luuid -lksuser -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode '-Wl,--subsystem,windows' -o $candidate
if($LASTEXITCODE){throw 'FSA build failed'}
foreach($name in @('resolver','language','nsp-state')){
 & $clang @flags "$src/$($name)_test.c" -luser32 -lgdi32 -o "$bin/$name-test.exe"
 if($LASTEXITCODE){throw "$name test build failed"}
 & "$bin/$name-test.exe"
 if($LASTEXITCODE){throw "$name tests failed"}
}
& $clang @flags "$src/icon_test.c" -luser32 -lgdi32 -municode -o "$bin/icon-test.exe"
if($LASTEXITCODE){throw 'Icon test build failed'}
& "$bin/icon-test.exe" $candidate
if($LASTEXITCODE){throw 'Icon tests failed'}
& $clang @flags "$src/nsp_ui_test.c" "$src/fluent_menu.c" -lole32 -luuid -lksuser -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode -o "$bin/nsp-ui-test.exe"
if($LASTEXITCODE){throw 'NSp UI contract build failed'}
& "$bin/nsp-ui-test.exe"
if($LASTEXITCODE){throw 'NSp UI contract failed'}
& $clang @flags "$src/nsp_lock_test.c" "$src/fluent_menu.c" -lole32 -luuid -lksuser -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode -o "$bin/nsp-lock-test.exe"
if($LASTEXITCODE){throw 'NSp Lock contract build failed'}
& "$bin/nsp-lock-test.exe"
if($LASTEXITCODE){throw 'NSp Lock contract failed'}
& $clang @flags "$src/nsp_exit_test.c" "$src/fluent_menu.c" -lole32 -luuid -lksuser -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode -o "$bin/nsp-exit-test.exe"
if($LASTEXITCODE){throw 'NSp exit regression build failed'}
1..2 | ForEach-Object {
 & "$bin/nsp-exit-test.exe"
 if($LASTEXITCODE){throw 'NSp exit/reopen regression failed'}
}
& $clang @flags "$src/tray_animation_test.c" "$src/fluent_menu.c" "$bin/fsa-test-resource.o" -lole32 -luuid -lksuser -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode -o "$bin/tray-animation-test.exe"
if($LASTEXITCODE){throw 'Tray animation test build failed'}
& "$bin/tray-animation-test.exe"
if($LASTEXITCODE){throw 'Tray animation tests failed'}
& $clang @flags "$src/popup_test.c" "$src/fluent_menu.c" -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -o "$bin/popup-test.exe"
if($LASTEXITCODE){throw 'Popup test build failed'}
& $clang @flags "$src/profile_test.c" "$src/fluent_menu.c" -lole32 -luuid -lksuser -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode -o "$bin/profile-test.exe"
if($LASTEXITCODE){throw 'Profile test build failed'}
# Prepare a separate runnable installation; preserve existing snapshots and rules.
if(-not(Test-Path "$destination/final-spatial-audio.ini")){
 if(Test-Path "$Installation/final-spatial-audio.ini"){Copy-Item -LiteralPath "$Installation/final-spatial-audio.ini" -Destination $destination}
 else{Copy-Item -LiteralPath "$src/final-spatial-audio.example.ini" -Destination "$destination/final-spatial-audio.ini"}
 if(Test-Path "$Installation/profiles"){Copy-Item -LiteralPath "$Installation/profiles" -Destination $destination -Recurse}
}
$scheduler=if($LegacyPythonScheduler){'Python'}else{'Zig'}
& "$PSScriptRoot/Configure-Scheduler.ps1" -Configuration "$destination/final-spatial-audio.ini" -Scheduler $scheduler
# Build directly into the official output. If it is in use, fail without
# closing FSA or creating another runnable copy.
Write-Output "Built and verified NSp: $candidate"
