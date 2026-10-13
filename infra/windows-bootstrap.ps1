$ErrorActionPreference = 'Stop'
$password = ConvertTo-SecureString '__PASSWORD__' -AsPlainText -Force
New-LocalUser -Name 'LabOperator' -Password $password -PasswordNeverExpires
Add-LocalGroupMember -Group 'Administrators' -Member 'LabOperator'
Enable-PSRemoting -Force
Set-Item WSMan:\localhost\Service\AllowUnencrypted -Value $false
Set-Item WSMan:\localhost\Service\Auth\Basic -Value $false
# The disposable lab uses a local account; production uses a domain service account.
New-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name LocalAccountTokenFilterPolicy -Value 1 -PropertyType DWord -Force
$cert = New-SelfSignedCertificate -DnsName '10.3.31.1' -CertStoreLocation Cert:\LocalMachine\My -TextExtension @('2.5.29.17={text}IPAddress=10.3.31.1')
New-Item WSMan:\localhost\Listener -Transport HTTPS -Address * -CertificateThumbPrint $cert.Thumbprint -Force
New-NetFirewallRule -DisplayName 'FIAP WinRM lab' -Direction Inbound -Protocol TCP -LocalPort 5986 -RemoteAddress '10.3.31.0/24' -Action Allow
Disable-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*'
$bytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
$pem = "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($bytes,[Base64FormattingOptions]::InsertLineBreaks) + "`n-----END CERTIFICATE-----"
Set-Content -Path C:\winrm-lab.pem -Value $pem
