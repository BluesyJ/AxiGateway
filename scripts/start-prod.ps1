[CmdletBinding(DefaultParameterSetName = 'Production')]
param(
    [switch]$CopyKey,
    [Parameter(Mandatory, ParameterSetName = 'Offline')][switch]$Offline,
    [Parameter(Mandatory, ParameterSetName = 'Offline')][string]$Config,
    [Parameter(Mandatory, ParameterSetName = 'Offline')][string]$DataDir,
    [Parameter(Mandatory, ParameterSetName = 'Offline')][ValidateRange(1, 65535)][int]$Port = 4000
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $PSScriptRoot 'start_p1.py'
$info = New-Object System.Diagnostics.ProcessStartInfo
$info.FileName = Join-Path $root '.venv-litellm\Scripts\python.exe'
$info.WorkingDirectory = $root
$info.UseShellExecute = $false
if ($Offline) {
    $profilePath = (Resolve-Path -LiteralPath $Config).ProviderPath
    $dataPath = (Resolve-Path -LiteralPath $DataDir).ProviderPath.TrimEnd('\')
    $info.Arguments = '-I -S "{0}" --offline --config "{1}" --data-dir "{2}" --port {3}' -f $entry,$profilePath,$dataPath,$Port
} else {
    $profilePath = Join-Path $root 'config\litellm.astrbot.example.yaml'
    $info.Arguments = '-I -S "{0}" --config "{1}"' -f $entry,$profilePath
}

# Populate the child process environment without assigning to the caller's Env: drive.
$info.EnvironmentVariables.Clear()
foreach ($name in @('SystemRoot','WINDIR','PATH','TEMP','TMP','COMSPEC','PATHEXT',
        'SystemDrive','USERPROFILE','APPDATA','LOCALAPPDATA','PROCESSOR_ARCHITECTURE',
        'PROCESSOR_ARCHITEW6432','LOG_CONTENT')) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ($null -ne $value) { $info.EnvironmentVariables[$name] = $value }
}
$secure = $null
$buffer = [IntPtr]::Zero
try {
    if ($CopyKey) {
        $info.Arguments += ' --copy-key'
    } else {
        $secure = Read-Host -Prompt 'DeepSeek upstream API Key' -AsSecureString
        if ($null -eq $secure -or $secure.Length -eq 0) { throw 'Empty upstream API Key.' }
        $buffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $info.EnvironmentVariables['UPSTREAM_API_KEY'] = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
    }
    $worker = [Diagnostics.Process]::Start($info)
} finally {
    $info.EnvironmentVariables.Remove('UPSTREAM_API_KEY')
    if ($buffer -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer) }
    if ($null -ne $secure) { $secure.Dispose() }
}
try {
    while (-not $worker.WaitForExit(250)) { }
    $exitCode = $worker.ExitCode
} finally {
    # The foreground worker shares the console's Ctrl+C and drains its event queue.
    $worker.Dispose()
}
exit $exitCode
