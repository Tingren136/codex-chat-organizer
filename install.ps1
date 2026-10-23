[CmdletBinding()]
param(
    [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME '.codex' }),
    [string]$Version = 'v0.4.1',
    [string]$SourcePath = ''
)
$ErrorActionPreference = 'Stop'
$pythonExe = $null
$pythonArgs = @()
foreach ($candidate in @('py', 'python', 'python3')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $prefix = @()
        if ($candidate -eq 'py') { $prefix = @('-3.14') }
        $ErrorActionPreference = 'Continue'
        $detected = & $candidate @prefix -c 'import sys; print(sys.version_info.major*100+sys.version_info.minor)' 2>$null
        $detectedExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'
        if ($detectedExit -eq 0 -and $detected -eq '314') {
            $pythonExe = $candidate
            $pythonArgs = $prefix
            break
        }
    }
}
if (-not $pythonExe) { throw 'Python 3.14 required. Install from https://www.python.org/downloads/windows/ then retry.' }
if ($SourcePath) {
    $installer = Join-Path $SourcePath 'tools/install.py'
    & $pythonExe @pythonArgs -X utf8 $installer --codex-home $CodexHome --source $SourcePath
} else {
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ('organizer-installer-' + [guid]::NewGuid().ToString('N') + '.py')
    try {
        $ref = [uri]::EscapeDataString($Version)
        Invoke-WebRequest "https://raw.githubusercontent.com/Tingren136/codex-chat-organizer/$ref/tools/install.py" -OutFile $temporary -UseBasicParsing
        & $pythonExe @pythonArgs -X utf8 $temporary --codex-home $CodexHome --version $Version
        $installExit = $LASTEXITCODE
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force }
    }
    if ($installExit -ne 0) { throw "Installation failed (exit $installExit)." }
    return
}
if ($LASTEXITCODE -ne 0) { throw "Installation failed (exit $LASTEXITCODE)." }
