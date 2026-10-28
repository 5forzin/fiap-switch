$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
python -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Instale Python 3.12 ou mais recente.' }
& .venv\Scripts\python -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Falha na instalação das dependências.' }
& .venv\Scripts\python -m scripts.setup @args
if ($LASTEXITCODE -ne 0) { throw 'A instalação não foi concluída.' }
