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
& $clang @flags "$src/menu_visibility_test.c" -luser32 -lgdi32 -ldwmapi -luxtheme -o "$bin/menu-visibility-test.exe"
if($LASTEXITCODE){throw 'Menu visibility regression build failed'}
& "$bin/menu-visibility-test.exe"
if($LASTEXITCODE){throw 'Menu visibility regression failed'}
& $clang @flags "$src/web_host_test.c" -ladvapi32 -luser32 -o "$bin/web-host-test.exe"
if($LASTEXITCODE){throw 'Web host contract build failed'}
& "$bin/web-host-test.exe"
if($LASTEXITCODE){throw 'Web host contract failed'}
& $clang @flags "$src/playback_location_test.c" -municode -o "$bin/playback-location-test.exe"
if($LASTEXITCODE){throw 'Playback location regression build failed'}
$locationFixture=Join-Path $bin ('playback-location-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $locationFixture | Out-Null
& "$bin/playback-location-test.exe" $locationFixture
if($LASTEXITCODE){throw 'Playback location regression failed'}
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
& $clang @flags "$src/pcm_format_test.c" "$src/fluent_menu.c" -lole32 -luuid -lksuser -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode -o "$bin/pcm-format-test.exe"
if($LASTEXITCODE){throw 'PCM format test build failed'}
$pcmFixture=Join-Path $destination ('pcm-format-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $pcmFixture | Out-Null
& "$bin/pcm-format-test.exe" $pcmFixture
if($LASTEXITCODE){throw 'PCM format tests failed'}
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
