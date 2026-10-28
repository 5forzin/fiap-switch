# Operação do FIAP Switch

## Arquitetura na FIAP

O site roda na Contabo. Um único controlador no servidor da LAN consulta a fila por HTTPS, executa comandos fixos por WinRM e envia as leituras. Os computadores dos laboratórios usam os recursos nativos do Windows. A rede interna não precisa aceitar conexões de entrada da internet.

O acesso do servidor aos IPs permite consultar, reiniciar e desligar por WinRM. Para ligar, Wake-on-LAN depende do suporte da placa de rede, da configuração de BIOS/UEFI e da passagem dos pacotes pelos switches. Se os andares estiverem em VLANs diferentes, valide encaminhamento de broadcast ou retransmissor com a equipe de redes.

## Contas e permissões

O modo padrão é `AUTH_MODE=local`. Após preencher o ambiente privado, crie o primeiro administrador:

```sh
docker compose exec web python -m scripts.create_admin --email admin@fiap.com.br --name Administrador
```

A senha pertence à aplicação e é independente da senha do domínio. Em **Acessos**, o administrador cadastra usuários com e-mail `@fiap.com.br`, define o perfil e escolhe um único andar para cada monitor. Também pode redefinir senhas, desativar e excluir contas. Não há cadastro público nem senha predefinida.

| Perfil        | Permissão                                              |
| ------------- | ------------------------------------------------------ |
| Administrador | Operação completa e gerenciamento de contas.           |
| Monitor       | Consulta, verificação e comandos somente em seu andar. |
| Suporte       | Operação completa, sem gerenciamento de contas.        |

Senhas exigem 8–128 caracteres e usam scrypt com sal, `N=65536`, `r=8`, `p=2`. Há limite de cinco tentativas por IP e dez por conta em 15 minutos. O sucesso limpa as tentativas daquele IP/conta. Cookies de sessão são assinados, HttpOnly, Secure e SameSite=Lax, com validade de oito horas. Escritas exigem mesma origem e token CSRF.

Alterar ou redefinir senha, modificar permissões ou desativar uma conta revoga as sessões. A fila confere novamente a permissão atual antes de entregar cada comando. Lotes com qualquer computador não autorizado são rejeitados integralmente.

A exclusão remove credenciais e sessões e retira a conta da lista ativa. O registro de autoria é preservado para manter a integridade da fila e da auditoria.

Ações de administrador são visíveis somente à própria conta. Suporte acompanha ações de suporte e monitores. O monitor acompanha suas próprias ações e os eventos automáticos de seus equipamentos. A privacidade também cobre comandos enviados pelo controlador e verificações salvas; a autoria e o perfil são registrados no momento da ação.

A migração de contas antigas preserva ID, credenciais, sessões e auditoria. Bancos marcados como dados de demonstração antigos não podem ser abertos em produção.

## SSO corporativo opcional

Ative `AUTH_MODE=entra` somente após a TI registrar uma aplicação web no Microsoft Entra da FIAP. O diretório identificado na descoberta corporativa é `11dbbfe2-89b8-4549-be10-cec364e59551`.

1. Cadastre a URL exata de retorno: `https://<seu-host>/auth/callback`.
2. Preencha `ENTRA_TENANT_ID`, `ENTRA_CLIENT_ID` e `ENTRA_CLIENT_SECRET` no ambiente privado.
3. Defina `BOOTSTRAP_ADMIN_OID` com o ID do objeto do administrador inicial e reinicie o site.
4. Em **Acessos**, cadastre usuários pelos IDs de objeto do diretório e e-mails corporativos.

Authlib valida assinatura, emissor, audiência, estado, nonce e PKCE. A autorização usa o diretório e o ID do objeto; o sufixo do e-mail sozinho não concede acesso. No modo Entra, senhas e sessões locais são recusadas.

## Controlador da LAN

Instale Python 3.12+ no servidor Windows ou Linux com acesso aos laboratórios:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
Copy-Item collector/.env.example .env.collector
.venv\Scripts\python -m collector.service
```

No Linux, use `.venv/bin/python` e o serviço systemd em `infra/`. No Windows, configure o Agendador de Tarefas para iniciar `python -m collector.service` na inicialização, com a raiz do repositório como diretório de trabalho. Use a conta de serviço autorizada, impeça execuções duplicadas e configure reinicialização em caso de falha. Restrinja `.env.collector` à conta de serviço e aos administradores.

Preencha `SWITCH_URL`, `COLLECTOR_TOKEN`, `WINRM_USERNAME` e `WINRM_PASSWORD`. O token deve corresponder ao configurado no site. Use uma conta do domínio dedicada, com os direitos necessários para gerenciamento remoto e desligamento. Se utilizar Just Enough Administration, valide os comandos fixos antes da implantação.

O padrão é WinRM HTTPS na porta `5986`, NTLM, criptografia das mensagens e validação do certificado. `WINRM_CA_BUNDLE` aceita PEM com a autoridade certificadora da instituição. Para Kerberos, instale `pywinrm[kerberos]`, configure domínio, renovação dos tíquetes da conta de serviço e nomes DNS que resolvam para o IP cadastrado. Kerberos permite HTTP na porta `5985` com criptografia das mensagens.

A implementação não desativa validação TLS, não habilita autenticação Basic e não usa TrustedHosts com curinga.

Cadastre os MACs antes de usar Wake-on-LAN. Uma leitura bem-sucedida preenche MACs ausentes usando o adaptador com rota padrão e preserva os já cadastrados. Confira a seleção em máquinas com múltiplas interfaces. `ALLOWED_CIDRS` limita IPs, nomes resolvidos e broadcasts.

## Inventário e endereçamento

Andares permitidos: **1, 2, 3, 4, 5, 6, 7, 10 e 11**. Cada andar aceita salas de 1 a 9, preservando o terceiro octeto concatenado sem colisões.

| Sala | Rede            | Aluno        | Professor      |
| ---- | --------------- | ------------ | -------------- |
| 301  | `10.3.31.0/24`  | `10.3.31.1`  | `10.3.31.100`  |
| 202  | `10.3.22.0/24`  | `10.3.22.1`  | `10.3.22.100`  |
| 1001 | `10.3.101.0/24` | `10.3.101.1` | `10.3.101.100` |

O cadastro cria alunos a partir de `.1`, até a quantidade informada, e opcionalmente o professor `.100`. A opção **Indefinido** permite cadastrar a sala sem informar o total de alunos. Em **Configurar sala → Computadores**, é possível manter esse estado ou definir a quantidade depois, preservando os equipamentos existentes. A quantidade definida não pode ser menor que o cadastro atual. O estado inicial dos equipamentos é não verificado. O controlador consulta apenas o inventário autorizado.

## Estado e comandos

A visão geral resume os andares sem renderizar cada computador. **Computadores** abre no primeiro andar acessível e em sua primeira sala. Trocar de andar seleciona sua primeira sala. **Todos os andares permitidos** habilita operações entre andares quando autorizadas; a lista continua paginada.

- **Ligado:** o WinRM retornou uma leitura válida.
- **Sem resposta:** a consulta falhou; isso não comprova desligamento elétrico.
- **Não verificado:** não há leitura ou ela tem mais de 120 segundos.

Comandos usam chave de idempotência, expiram após cinco minutos na fila e têm prazo de execução de dois minutos. Se o prazo de execução expirar, o resultado é incerto e o comando não é repetido automaticamente.

Reiniciar e desligar programam aviso de 60 segundos no Windows. Ao final, aplicativos podem ser encerrados à força; os usuários precisam salvar o trabalho. A interface exige confirmação. **Enviado** significa que o Windows aceitou o comando ou que o pacote Wake-on-LAN foi enviado. A confirmação de estado vem da próxima leitura.

## Verificações e histórico diário

Em **Verificações e histórico**, solicite uma leitura nova para um andar ou para todos os andares permitidos. O controlador antecipa seu ciclo e salva os resultados. Solicitações pendentes idênticas são reutilizadas. Verificações concluídas são imutáveis, inclusive o hardware observado. A solicitação expira após 15 minutos se não for concluída.

Cada leitura real atualiza o registro diário em `America/Sao_Paulo`. A primeira do dia cria o registro. São preservados situação mais recente, primeira e última consulta, última resposta confirmada e avaliação do hardware. Dias sem leituras não recebem registros artificiais. As permissões atuais também restringem o histórico; resultados são paginados no servidor.

## Padrões de hardware

Em **Inventário**, selecione o andar, abra **Configurar sala** e use a aba **Padrão de hardware**. Escolha um computador conforme da própria sala em **Computador de referência** e clique em **Copiar hardware do computador** para preencher os requisitos. Em salas ainda sem padrão, uma leitura atual pode servir de referência inicial. Computadores sem resposta ou com leitura antiga não são oferecidos para clonagem. Requisitos opcionais: modelo de CPU, CPUs lógicas, RAM, armazenamento físico total, quantidade de discos e modelo de GPU. Os modelos usam busca parcial sem diferenciar maiúsculas. Capacidades são informadas em GiB.

**Inventário exato** compara quantidades e capacidades cadastradas. **Capacidades mínimas** aceita valores iguais ou superiores. RAM e armazenamento têm tolerância de 5% para considerar a capacidade utilizável. Campos vazios são ignorados. A ausência de leitura obrigatória nunca produz conformidade.

A primeira divergência abre uma ocorrência datada. Leituras seguintes mantêm a data inicial. Uma leitura conforme registra a resolução. Padrão esperado, hardware detectado, diferenças e resolução permanecem no histórico. Desativar um padrão é registrado separadamente de obter conformidade. Alterar o padrão exige nova leitura.

Antes da negociação WinRM, o controlador verifica TCP com prazo curto. Resultados são enviados em lotes à medida que terminam, evitando bloqueio por máquinas sem resposta. `TCP_CONNECT_TIMEOUT` vale dois segundos por padrão e aceita de 0,5 a 10 segundos.

## Hospedagem e backup

Copie `.env.example` para `.env`, preencha a configuração privada, execute `docker compose up -d --build` e crie o administrador. O contêiner deve ouvir somente em loopback, atrás de Caddy ou outro proxy HTTPS. Ele usa usuário sem privilégios, sistema de arquivos somente para leitura e volume próprio para SQLite. Execute uma única réplica web.

Use a API de backup on-line do SQLite; não copie um banco em uso com WAL diretamente. Guarde também o ambiente privado em local seguro. Rotacione o token nas duas pontas. Defina retenção de auditoria e backups com a TI.

## Laboratório Windows na AWS

O ambiente tem controlador Windows em `10.3.31.10`, aluno em `10.3.31.4` e professor em `10.3.22.100`. Todos usam Windows Server 2022, discos EBS criptografados e IMDSv2. WinRM aceita apenas tráfego privado entre as instâncias. SSM administra o laboratório; não é um agente da aplicação instalado na FIAP.

```powershell
aws login --profile fiap-switch --region us-east-2
.venv\Scripts\python infra/aws_lab.py provision --region us-east-2 --state runtime/aws-validation-20261027.json --name fiap-switch-e2e-20261027 --windows-controller --targets 2
.venv\Scripts\python infra/aws_lab.py status --state runtime/aws-validation-20261027.json
```

Não execute `provision` se o arquivo já descreve um laboratório existente. O script registra os recursos criados para recuperação e remoção direcionada. O provisionamento antigo usa controlador Linux e um alvo Windows; mantenha arquivos de estado distintos.

O instalador Python tem sua assinatura oficial verificada. O controlador Windows roda como tarefa de inicialização, com configurações restritas a SYSTEM e administradores. Certificados WinRM incluem IP privado e DNS.

`scripts/apresentacao.py` prepara salas e contas usando o ambiente já registrado. Senhas ficam exclusivamente em `runtime/apresentacao.json`. A ação `suporte` prepara a conta de suporte e atualiza o arquivo privado de acessos. A ação `hardware` anexa um disco real de 8 GiB à máquina `301-4`, preservando o padrão de 30 GiB e um disco; o volume é criptografado e configurado para exclusão junto com a instância. O controlador continua usando WinRM e Wake-on-LAN; não aciona energia pela API da AWS.

**Mantenha as três instâncias ligadas para a apresentação solicitada.** Não execute limpeza ou desligamento ao concluir a validação desse ambiente. Após o responsável encerrar seu uso:

```powershell
.venv\Scripts\python infra/aws_lab.py stop --state runtime/aws-validation-20261027.json
.venv\Scripts\python infra/aws_lab.py destroy --state runtime/aws-validation-20261027.json
```

`stop` preserva discos EBS cobrados. `destroy` remove instâncias, volumes e recursos deste laboratório. Nunca opere recursos externos ao arquivo de estado conferido.

A AWS reserva `.0` a `.3`, por isso o aluno usa `.4`. VPC não oferece broadcast e EC2 desligada não recebe Wake-on-LAN. A validação comprova pacotes emitidos, WinRM e desligamentos/reinicializações; ligar fisicamente exige a LAN do campus.

## Acesso local pelo proxy

O ambiente autorizado usa SOCKS5 em `127.0.0.1:1080`. Para acessar pelo navegador:

```powershell
.venv\Scripts\python -m scripts.browser_gateway
```

Abra [localhost:8011](http://localhost:8011). Esse acesso ouve somente em loopback, encaminha ao HTTPS publicado pelo SOCKS5 e preserva validação TLS e cookies de produção. É uma ferramenta local de acesso, sem dados fictícios.

Os testes remotos usam credenciais privadas em `runtime/login.json`, estado do laboratório e SSH autorizado à Contabo. As verificações reproduzíveis estão em `scripts/live_validation.py` e `scripts/validate_features.py`. Rotinas de falha, expiração, desligamento e limpeza alteram o ambiente: execute-as apenas quando ele não estiver reservado para apresentação.

## Referências

- [OIDC do Microsoft Entra](https://learn.microsoft.com/en-us/entra/identity-platform/v2-protocols-oidc)
- [Segurança do WinRM](https://learn.microsoft.com/en-us/powershell/scripting/learn/remoting/winrmsecurity)
- [pywinrm](https://github.com/diyan/pywinrm)
- [Autenticação da AWS CLI pelo console](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)
- [Reservas de endereços e limitação de broadcast na AWS](https://docs.aws.amazon.com/vpc/latest/userguide/subnet-sizing.html)
- [Armazenamento de senhas — OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
