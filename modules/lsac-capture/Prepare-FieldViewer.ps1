param([Parameter(Mandatory=$true)][string]$OutputDirectory)
$ErrorActionPreference='Stop'
$lsacVendor=Join-Path $OutputDirectory 'vendor'
New-Item -ItemType Directory -Force -Path $lsacVendor | Out-Null
# Official Three.js r186, MIT. Upstream code stays in the generated output.
$lsacAssets=@(
    @('LICENSE','LICENSE','8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc'),
    @('OrbitControls.js','examples/jsm/controls/OrbitControls.js','3d79d07ecb686b4e5d93232eedab255331c1beef711e13164eaa1f68655a5f2b'),
    @('three.core.js','build/three.core.js','9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6'),
    @('three.module.js','build/three.module.js','9052042d676cb0fdc1ddfefe193053f34b7ac0513a616fdac4535d49987812ea')
)
foreach($lsacAsset in $lsacAssets){
    $lsacDestination=Join-Path $lsacVendor $lsacAsset[0]
    if(-not (Test-Path -LiteralPath $lsacDestination)){
        Invoke-WebRequest -UseBasicParsing -Uri ('https://raw.githubusercontent.com/mrdoob/three.js/r186/'+$lsacAsset[1]) -OutFile $lsacDestination
    }
    if((Get-FileHash -LiteralPath $lsacDestination -Algorithm SHA256).Hash.ToLower() -ne $lsacAsset[2]){
        throw ('Three.js checksum mismatch: '+$lsacDestination)
    }
}
$lsacManifest=[ordered]@{upstream='https://github.com/mrdoob/three.js';tag='r186';license='MIT';files=@($lsacAssets | ForEach-Object {[ordered]@{name=$_[0];sha256=$_[2]}})}
$lsacManifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $lsacVendor 'provenance.json') -Encoding utf8
Write-Output ('Verified local Three.js r186 assets: '+$lsacVendor)
