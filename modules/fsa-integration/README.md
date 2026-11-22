# FSA product integration

FSA owns the tray, spatial-mode selection and NSP session lifecycle. Zig owns
session orchestration, status and recovery. Build the product as described in
the [build guide](../../docs/build.md). `Build-Integration.ps1 -FsaOnly` builds
FSA against prepared components; add `-MediaFoundation` to copy the built MFT
components. Building does not deploy to an existing installation.

Click the tray icon three times in quick succession to open configuration and
visualization. Settings default to `%LOCALAPPDATA%/NativeSpatialPipe/playback.json`.
The web interface saves settings; FSA handles runtime control. See the
[user guide](../../docs/user-guide.md) for session changes and normal stop.

NSP startup initializes a temporary shared audio client on the selected endpoint
before checking its host. The HT stability wait and format readback remain.
Locked NSp interaction requests stop and recovery verification; after successful
cleanup, a subsequent click starts. Status can retire an old session under a
replacement online manager when the required evidence is present. Emergency
stop also follows teardown and recovery verification.

The public build uses Windows-native endpoint support and omits extra AudioSrv
enumeration. Its advanced submenu retains explicit HT Support. Capture-layout
status, update and offline import are described in
[capture compatibility](../../docs/capture-compatibility.md).

Optional MFT, browser identity, PCM, upmix and output components require their
own dependencies and playback settings. AoIP has a resident service, while
ASIO/WASAPI output follows the corresponding session. Restart FSA after resident
AoIP configuration/layout changes. Keep real-device acceptance separate from
isolated menu/queue and scheduler checks.

The [FSA license](LICENSE) and [artwork notice](native/assets/LICENSE.txt) retain
their own terms. Developer ownership and verification rules are in the
[developer guide](../../docs/development.md).
