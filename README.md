<div align="center">

<img src="frontend/public/brand/fiap.svg" alt="FIAP" width="160" />

# FIAP Switch

**Energia, inventário e verificação dos computadores dos laboratórios.**

[![Validação](https://github.com/5forzin/fiap-switch/actions/workflows/ci.yml/badge.svg)](https://github.com/5forzin/fiap-switch/actions/workflows/ci.yml)

[Painel](https://fiap-switch.13.140.191.31.sslip.io/) · [Telas](#o-painel) · [Arquitetura](#como-funciona) · [Instalação](#executar-localmente) · [Operação](docs/operations.md)

</div>

![Visão geral dos laboratórios](docs/imagens/visao-geral.jpg)

O FIAP Switch centraliza o trabalho dos monitores: consultar os computadores do andar, conferir o hardware e enviar comandos de energia. O suporte acompanha todo o prédio; o administrador gerencia as contas e define o andar de cada monitor.

O controlador roda em Windows ou Linux, com acesso à rede dos computadores. Na FIAP, energia usa WinRM e Wake-on-LAN. Nos modos AWS e Azure, energia usa as APIs do provedor. Estado e hardware continuam usando o WinRM nativo do Windows, sem agente nos computadores.

## O painel

### Computadores por andar

Seleção inicial de andar e laboratório, busca por IP ou nome e páginas de 50 máquinas. Os andares atendidos são **1, 2, 3, 4, 5, 6, 7, 10 e 11**.

![Computadores e filtros do laboratório](docs/imagens/computadores.jpg)

### Inventário e hardware

Cada sala tem seu próprio padrão de CPU, RAM, armazenamento, quantidade de discos e GPU. A comparação pode exigir o inventário exato ou capacidades mínimas. Uma divergência registra o equipamento, os valores esperados e detectados e a data da primeira ocorrência.

<table>
<tr>
<td width="50%"><img src="docs/imagens/padrao-sala.jpg" alt="Configuração do padrão de hardware da sala" /></td>
<td width="50%"><img src="docs/imagens/notificacao.jpg" alt="Hardware real e divergência de armazenamento detectada" /></td>
</tr>
<tr>
<td align="center">Padrão configurável por laboratório</td>
<td align="center">Leitura real obtida pelo WinRM</td>
</tr>
</table>

Para configurar uma sala, abra **Inventário → andar → Configurar sala**. Na aba **Padrão de hardware**, escolha um **Computador de referência** conforme e use **Copiar hardware do computador**. Confira os valores e salve o padrão.

![Clonagem a partir de um computador conforme da própria sala](docs/imagens/clonagem.jpg)

Na aba **Computadores**, a quantidade pode ficar **Indefinida**. Ao verificar o andar, o controlador procura novos computadores de `.1` a `.99` e o professor em `.100`, somente na sub-rede da sala. Cadastra apenas alvos que respondem ao WinRM autenticado, já com hardware e histórico. Repetir o scan preserva o inventário e não duplica IPs ou números de computador. Máquinas desligadas que nunca foram cadastradas serão descobertas quando estiverem acessíveis.

Definir o total depois preserva os equipamentos cadastrados e adiciona os que faltam. A verificação aguarda o término da descoberta antes de salvar o resultado completo.

<details>
<summary>Ver quantidade indefinida e inventário detectado</summary>

![Quantidade indefinida por sala](docs/imagens/quantidade.jpg)
![Inventário real obtido pelo WinRM](docs/imagens/hardware.jpg)

</details>

### Verificações e histórico

Uma verificação solicita leituras novas e salva o resultado. O histórico diário guarda a situação mais recente de cada computador e a última resposta confirmada, no fuso de São Paulo. Resolver uma divergência mantém seu registro anterior.

![Verificação salva com situação e hardware de cada computador](docs/imagens/historico.jpg)

### Acesso por responsabilidade

| Perfil        | O que pode fazer                                                                    |
| ------------- | ----------------------------------------------------------------------------------- |
| Administrador | Gerenciar contas e andares; operar todos os laboratórios e seus inventários.        |
| Monitor       | Consultar e controlar somente os computadores do andar definido pelo administrador. |
| Suporte       | Operar todos os laboratórios e inventários, sem criar, excluir ou alterar contas.   |

As permissões são verificadas no servidor. Alterações de andar ou perfil encerram sessões antigas e são conferidas novamente antes da execução de um comando na fila. O suporte acompanha as ações dos monitores. Ações de administrador ficam privadas para a própria conta; administradores acompanham suporte e monitores.

<table>
<tr>
<td width="50%"><img src="docs/imagens/acessos.jpg" alt="Administração dos usuários e seus andares" /></td>
<td width="50%"><img src="docs/imagens/monitor.jpg" alt="Conta de monitor com acesso somente ao terceiro andar" /></td>
</tr>
<tr>
<td align="center">Administração de acessos</td>
<td align="center">Visão restrita do monitor</td>
</tr>
</table>

As capturas mostram leituras reais de um laboratório Windows na AWS. Os IPs desse laboratório reproduzem o padrão de endereçamento informado para o projeto; nenhum inventário da rede real da FIAP é publicado.

## Como funciona

```mermaid
flowchart LR
    U[Administrador, suporte ou monitor] -->|HTTPS| W[Aplicação web]
    C[Controlador no servidor da FIAP] -->|Consulta a fila por HTTPS| W
    W --> D[(Inventário e histórico)]
    C -->|WinRM: consultar, reiniciar e desligar| S[Switches da rede local]
    C -->|Wake-on-LAN: ligar| S
    C -->|Modos AWS e Azure: API de energia| N[Provedor de nuvem]
    S --> P[Computadores dos laboratórios]
```

O site pode ficar hospedado fora do campus. O controlador faz conexões de saída por HTTPS e executa apenas os comandos fixos permitidos. A rede da FIAP não precisa receber conexões da internet.

| Ação                        | Execução                                               |
| --------------------------- | ------------------------------------------------------ |
| Consultar estado e hardware | WinRM com consultas CIM do Windows.                    |
| Reiniciar ou desligar       | WinRM com aviso de 60 segundos para salvar o trabalho. |
| Ligar                       | Pacote Wake-on-LAN para o MAC cadastrado.              |

Essa tabela descreve o modo **FIAP produção**. Em **AWS**, ligar, desligar e reiniciar usam `StartInstances`, `StopInstances` e `RebootInstances`. Em **Azure**, usam `start`, `deallocate` e `restart`. Não há fallback de uma API de nuvem para Wake-on-LAN ou comandos WinRM de energia. O controlador encontra a VM pelo IP privado dentro da VPC ou do grupo de recursos configurado; correspondências ambíguas são recusadas.

**Wake-on-LAN depende da rede e do equipamento.** BIOS/UEFI e placa de rede precisam permitir o ligamento remoto. Entre VLANs, pode ser necessário encaminhar broadcast ou usar um retransmissor autorizado. Acesso por IP atende ao WinRM, mas não garante a passagem do pacote de Wake-on-LAN.

### Endereçamento

O terceiro octeto junta o número do andar com o número da sala dentro daquele andar. O computador do professor usa `.100`.

| Sala | Andar | Rede            | Exemplo                        |
| ---- | ----- | --------------- | ------------------------------ |
| 301  | 3     | `10.3.31.0/24`  | `10.3.31.1`                    |
| 202  | 2     | `10.3.22.0/24`  | `10.3.22.100` para o professor |
| 1001 | 10    | `10.3.101.0/24` | `10.3.101.1`                   |

### O que cada situação significa

- **Ligado:** o WinRM respondeu a uma consulta válida.
- **Sem resposta:** a consulta falhou; a causa pode ser energia, rede ou WinRM.
- **Não verificado:** não há leitura atual ou o controlador está sem comunicação.

Um comando enviado não comprova que a máquina já mudou de estado. A confirmação vem da próxima leitura. Hardware sem leitura atual aparece como não verificado, preservando o último inventário e as divergências abertas.

## Executar localmente

### Instalação automática em Linux ou Windows

Requisitos: **Python 3.12+ e Docker com Compose**. O instalador prepara o ambiente, constrói a interface, inicia o site e mostra no terminal o e-mail e a senha aleatória do administrador inicial. Sem `--mode`, apresenta a escolha entre FIAP produção, AWS e Azure.

Linux:

```sh
sh setup.sh --mode fiap
```

Windows:

```powershell
.\setup.ps1 --mode fiap
```

Abra [localhost:8000](http://localhost:8000). Para um domínio com HTTPS configurado no proxy reverso, acrescente `--url https://switch.exemplo.com`. Configurações e contas existentes são preservadas em execuções posteriores. Guarde a senha exibida; o arquivo de configuração armazena somente seu hash.

Para escolher nuvem sem perguntas adicionais:

```sh
sh setup.sh --mode aws --aws-region us-east-2 --aws-vpc-id vpc-0123456789abcdef0
sh setup.sh --mode azure --azure-subscription-id SEU_UUID --azure-resource-group fiap-labs
```

Os exemplos representam instalações alternativas. Credenciais de nuvem ficam no **controlador**, usando perfil IAM, identidade gerenciada ou login de desenvolvimento pelo SDK. Nenhuma chave de nuvem é enviada ao navegador ou gravada pelo instalador. O site pode estar em Linux independentemente do modo escolhido.

O instalador também gera `.env.collector` com a URL e o token corretos. Transfira esse arquivo de forma privada ao servidor da LAN, preencha a conta WinRM e a autoridade certificadora e execute o controlador. Use `--no-start` para gerar os arquivos e iniciar a aplicação manualmente.

### Instalação manual

Requisitos: **Python 3.12+**, **Node.js 24** e **npm**. No PowerShell:

```powershell
git clone https://github.com/5forzin/fiap-switch.git
cd fiap-switch
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
npm ci --prefix frontend
npm run build --prefix frontend

$env:ENVIRONMENT='development'
$env:BASE_URL='http://localhost:8000'
$env:COOKIE_SECURE='false'
$env:DATABASE_PATH='data/switch.db'
$env:SESSION_SECRET=[Guid]::NewGuid().ToString()+[Guid]::NewGuid().ToString()

.venv\Scripts\python -m scripts.create_admin --email admin@fiap.com.br --name Administrador
.venv\Scripts\python -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8000
```

Defina a senha quando o terminal solicitar. Abra [localhost:8000](http://localhost:8000), entre e cadastre os laboratórios e usuários. Um banco novo começa sem computadores fictícios e sem senha padrão.

### Controlador da LAN

No servidor que alcança os computadores da FIAP:

```powershell
Copy-Item collector/.env.example .env.collector
# Preencha o arquivo com a URL, token e conta de serviço autorizada.
.venv\Scripts\python -m collector.service
```

Use uma conta de serviço do domínio com os direitos necessários. O padrão é WinRM HTTPS na porta `5986`, autenticação NTLM, mensagens criptografadas e validação do certificado. Kerberos também é suportado. Consulte o [guia de operação](docs/operations.md) para certificados, tarefa de inicialização e implantação com Docker.

## Autenticação e segurança

- Contas individuais com nome completo, e-mail `@fiap.com.br` e senha de pelo menos oito caracteres.
- Senhas com hash scrypt; não há cadastro público nem credenciais fixas.
- Cookies seguros, sessão de oito horas, proteção CSRF e limite de tentativas de entrada.
- Fila persistente com idempotência, expiração e autorização antes da execução.
- Validação dos IPs, nomes e broadcasts permitidos pelo controlador.
- SSO Microsoft Entra opcional, sujeito ao cadastro da aplicação pela TI da FIAP.

<details>
<summary>Ver tela de entrada</summary>

![Entrada por e-mail corporativo e senha](docs/imagens/login.jpg)

</details>

Arquivos de ambiente, chaves, bancos e credenciais ficam fora do Git. Nunca publique o conteúdo de `runtime/`.

## Validação

```powershell
.venv\Scripts\python -m pytest -q
npm run build --prefix frontend
```

A suíte possui **81 testes automatizados**. A validação de **27/10/2026** utilizou um controlador Windows Server 2022 e dois alvos Windows reais, em redes de laboratório diferentes. Foram confirmados telemetria, restrição por andar, reinicialização com mudança do horário de inicialização, desligamento efetivo, expiração da fila, persistência, histórico diário e divergências de hardware. A interface também foi exercitada com **1.188 computadores em uma base isolada**.

Os três pacotes reais de Wake-on-LAN foram recebidos no controlador e comparados com o MAC do alvo. **A AWS não oferece broadcast em VPC nem ligamento de EC2 desligada por Wake-on-LAN**; o teste de ligar fisicamente depende da LAN do campus. [Limitação documentada pela AWS](https://docs.aws.amazon.com/vpc/latest/userguide/subnet-sizing.html).

Para a apresentação, o computador `301-4` recebeu um disco EBS real de **8 GiB**: o WinRM detecta **38 GiB e dois discos**, enquanto o padrão continua em **30 GiB e um disco**. A máquina do laboratório 202 permanece conforme. Os três Windows ficam ligados.

[Evidências das verificações](docs/validation-2026-10-27.json) · [Operação, contas e infraestrutura](docs/operations.md)

## Estrutura

| Diretório    | Responsabilidade                                                |
| ------------ | --------------------------------------------------------------- |
| `frontend/`  | Interface React e TypeScript, com identidade visual FIAP.       |
| `backend/`   | API FastAPI, autorização, fila SQLite, inventário e histórico.  |
| `collector/` | Controlador da LAN e comandos WinRM/Wake-on-LAN.                |
| `infra/`     | Preparação do laboratório AWS e dos servidores Windows.         |
| `scripts/`   | Administração, implantação e validação real.                    |
| `tests/`     | Testes de autenticação, permissões, fila, histórico e hardware. |
| `docs/`      | Capturas do painel, guia de operação e evidências.              |

## Identidade visual

O painel usa a [logo oficial da FIAP](https://www.fiap.com.br/svg/fiap.svg) e as fontes Gotham Book, Light e Bold observadas na [intranet](https://intranet.fiap.com.br/home). Marca e fontes pertencem aos respectivos titulares.

Projeto acadêmico independente. Este repositório não representa um serviço oficial da FIAP.
