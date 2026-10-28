$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = 'C:\fiap-switch'
New-Item -ItemType Directory -Path $root -Force | Out-Null
$python = "$root\python\python.exe"
if (-not (Test-Path $python)) {
    Invoke-WebRequest -UseBasicParsing 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile "$root\python-installer.exe"
    $signature = Get-AuthenticodeSignature "$root\python-installer.exe"
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Python Software Foundation*') {
        throw 'A assinatura do instalador do Python é inválida.'
    }
    $installed = Start-Process "$root\python-installer.exe" -ArgumentList '/quiet InstallAllUsers=1 TargetDir=C:\fiap-switch\python PrependPath=0 Include_test=0 Include_launcher=0 Shortcuts=0' -Wait -PassThru -WindowStyle Hidden
    if ($installed.ExitCode -notin @(0, 3010)) { throw "Falha na instalação do Python: $($installed.ExitCode)" }
}
Invoke-WebRequest -UseBasicParsing 'https://codeload.github.com/5forzin/fiap-switch/zip/__REVISION__' -OutFile "$root\source.zip"
Expand-Archive "$root\source.zip" "$root\source" -Force
$source = "$root\source\fiap-switch-__REVISION__"
Set-Content "$root\source-path.txt" $source
& $python -m pip install --disable-pip-version-check -r "$source\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw 'Falha na instalação das dependências.' }
Invoke-RestMethod 'https://fiap-switch.13.140.191.31.sslip.io/healthz' | ConvertTo-Json -Compress
& $python -c "import sys, winrm, httpx; print('Dependências do controlador Windows prontas:', sys.version.split()[0])"
