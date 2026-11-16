param([Parameter(Mandatory=$true)][string]$Zig,[string]$AppName='FinalSpatialAudio.exe')
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
New-Item -ItemType Directory -Force "$root/bin" | Out-Null
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/audio_layout.c" "$root/main.c" -lole32 -luuid -lpropsys -municode -o "$root/bin/audio-layout.exe"
if ($LASTEXITCODE -ne 0) { throw 'Native build failed' }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror -DAL_BUILD_DLL -shared "$root/audio_layout.c" -lole32 -luuid -lpropsys -o "$root/bin/audio-layout.dll"
if ($LASTEXITCODE -ne 0) { throw 'DLL build failed' }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/audio_layout.c" "$root/test.c" -lole32 -luuid -lpropsys -o "$root/bin/audio-layout-test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Test build failed' }
& "$root/bin/audio-layout-test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Native tests failed' }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/spatial_policy.c" -lole32 -luuid -lpropsys -municode -o "$root/bin/spatial-policy.exe"
if ($LASTEXITCODE -ne 0) { throw 'Spatial policy build failed' }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/resolver_test.c" -o "$root/bin/resolver-test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Resolver test build failed' }
& "$root/bin/resolver-test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Resolver tests failed' }
Push-Location $root
try {
    & $Zig rc /fo "$root/bin/final_spatial_audio.res" "$root/final_spatial_audio.rc"
    if ($LASTEXITCODE -ne 0) { throw 'Icon resource build failed' }
} finally { Pop-Location }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/final_spatial_audio.c" "$root/fluent_menu.c" "$root/bin/final_spatial_audio.res" -lole32 -luuid -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode "-Wl,--subsystem,windows" -o "$root/bin/$AppName"
if ($LASTEXITCODE -ne 0) { throw 'Final Spatial Audio build failed' }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/icon_test.c" -luser32 -lgdi32 -municode -o "$root/bin/icon-test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Icon test build failed' }
& "$root/bin/icon-test.exe" "$root/bin/$AppName"
if ($LASTEXITCODE -ne 0) { throw 'Icon resource tests failed' }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/popup_test.c" "$root/fluent_menu.c" -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -o "$root/bin/popup-test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Popup test build failed' }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/profile_test.c" "$root/fluent_menu.c" -lole32 -luuid -lpropsys -lshell32 -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -municode -o "$root/bin/profile-test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Profile test build failed' }
& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/material_test.c" "$root/fluent_menu.c" -luser32 -lgdi32 -ldwmapi -luxtheme -ladvapi32 -o "$root/bin/material-test.exe"
if ($LASTEXITCODE -ne 0) { throw 'Material test build failed' }

& $Zig cc -target x86_64-windows-gnu -std=c11 -O2 -g0 -s -Wall -Wextra -Werror "$root/language_test.c" -luser32 -lgdi32 -o "$root/bin/language-test.exe"
if ($LASTEXITCODE -ne 0) { throw "Language test build failed" }
& "$root/bin/language-test.exe"
if ($LASTEXITCODE -ne 0) { throw "Language tests failed" }
