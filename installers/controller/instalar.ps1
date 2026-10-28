param(
    [Parameter(Mandatory=$true)][string]$Fingerprint,
    [string]$Config,
    [string]$Ca,
    [string]$PythonPath = 'python'
)
$ErrorActionPreference = 'Stop'
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { throw 'Execute o PowerShell como administrador.' }
$basePython = & $PythonPath -c "import sys; assert sys.version_info >= (3, 12); print(sys.base_prefix)"
if ($LASTEXITCODE -ne 0 -or $basePython -like "$env:SystemDrive\Users\*") {
    throw 'Instale Python 3.12+ para todos os usuários. A conta Local Service precisa ler essa instalação.'
}
$root = 'C:\ProgramData\FIAPSwitch\Controller'
New-Item -ItemType Directory -Path "$root\bootstrap","$root\app","$root\data" -Force | Out-Null
& $PythonPath -m venv "$root\bootstrap"
if ($LASTEXITCODE -ne 0) { throw 'Instale Python 3.12+ para todos os usuários ou informe -PythonPath.' }
& "$root\bootstrap\Scripts\python.exe" -m pip install --disable-pip-version-check cryptography==50.0.2
if ($LASTEXITCODE -ne 0) { throw 'Falha ao preparar a verificação da assinatura.' }
& "$root\bootstrap\Scripts\python.exe" "$PSScriptRoot\verify.py" --fingerprint $Fingerprint
if ($LASTEXITCODE -ne 0) { throw 'A assinatura do pacote não foi confirmada.' }
Copy-Item -LiteralPath "$PSScriptRoot\collector" -Destination "$root\app" -Recurse -Force
& $PythonPath -m venv "$root\venv"
& "$root\venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "$PSScriptRoot\requirements-controller.txt"
if ($LASTEXITCODE -ne 0) { throw 'Falha nas dependências do controlador.' }
$arguments = @('--root',$root,'--enrollment',"$PSScriptRoot\enrollment.json")
if ($Config) { $arguments += @('--config',$Config) }
if ($Ca) { $arguments += @('--ca',$Ca) }
& "$root\venv\Scripts\python.exe" "$PSScriptRoot\configure.py" @arguments
if ($LASTEXITCODE -ne 0) { throw 'A configuração não foi concluída.' }
& icacls.exe $root /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-19:(OI)(CI)RX' /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Falha ao proteger os arquivos do controlador.' }
& icacls.exe "$root\*" /reset /T /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Falha ao aplicar a herança das permissões.' }
& icacls.exe "$root\data" /grant:r '*S-1-5-19:(OI)(CI)M' /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Falha ao proteger a identidade local.' }
$command = "`$env:COLLECTOR_ENV='$root\.env.collector'; `$env:PYTHONDONTWRITEBYTECODE='1'; Set-Location '$root\app'; & '$root\venv\Scripts\python.exe' -m collector.service *> '$root\data\controller.log'; exit `$LASTEXITCODE"
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($command))
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand $encoded"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'S-1-5-19' -LogonType ServiceAccount -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName 'FIAP-Switch-Production-Controller' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName 'FIAP-Switch-Production-Controller'
Write-Output 'Controlador iniciado com conta Local Service. Aprove a solicitação no painel.'
