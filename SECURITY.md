# Segurança

O FIAP Switch controla energia e inventário. A implantação precisa de revisão da TI responsável, principalmente das permissões WinRM, IAM e da rede. Nenhum software oferece garantia de segurança de 100%.

## Limites de confiança

- O navegador só usa a API autenticada. Autorizações por perfil e andar são verificadas no servidor, inclusive antes da execução de comandos pendentes.
- Em AWS e Azure, a identidade do site acessa somente o escopo configurado. VMs precisam da tag `FIAPSwitchManaged=true`. Restrinja também as permissões IAM/RBAC aos recursos autorizados; a tag sozinha não substitui essa restrição.
- Em FIAP produção, o site não recebe a senha WinRM nem inicia conexões com a LAN. Um servidor aprovado consulta a fila por HTTPS e executa comandos fixos.
- O controlador local possui credenciais de gerenciamento. O comprometimento desse servidor pode permitir operações dentro dos direitos dessa conta. Restrinja sua administração e use uma conta de domínio dedicada.

## Autenticação e autorização

Contas locais usam e-mail `@fiap.com.br`, senha de 8 a 128 caracteres e hash scrypt com sal individual. Não há cadastro público ou senha padrão. O administrador inicial recebe uma senha aleatória no terminal; o instalador salva apenas o hash.

Sessões têm validade de oito horas, cookies HttpOnly, Secure e SameSite=Lax. Escritas de usuários exigem origem autorizada e CSRF. Limites persistentes controlam tentativas por conta e por IP. Alterar credenciais, perfil ou permissões revoga sessões.

Somente administradores gerenciam contas e controladores. Suporte opera todos os andares. Monitores operam um único andar. Lotes com qualquer equipamento fora do escopo são recusados. A fila revalida permissões, limita ações, usa idempotência e expiração e não repete automaticamente comandos com resultado incerto.

SSO Entra é opcional e exige aplicação cadastrada pela instituição. O sufixo do e-mail não autentica uma pessoa no diretório. O modo local usa senhas próprias da aplicação e não verifica a senha do domínio.

## Programa do servidor da FIAP

Cada pacote tem manifesto com hashes SHA-256, assinatura Ed25519 e convite individual, válido por 30 minutos e de uso único. A chave privada de assinatura fica no volume do site, fora dos pacotes e do repositório. Compare a impressão digital por um canal confiável antes da instalação.

**Essa assinatura valida a integridade do pacote; não é uma assinatura Authenticode de uma autoridade corporativa.** Para distribuição institucional, a FIAP deve definir certificado, publicação e confiança nos instaladores conforme sua política. Um servidor web comprometido pode emitir pacotes assinados; proteja o host e a chave emissora.

O controlador gera sua própria chave privada no servidor local. O painel apresenta nome, plataforma, IP de origem e impressão digital e exige aprovação do administrador. IP e nome são informações auxiliares; a autenticação depende da chave aprovada.

Cada requisição assina método, caminho, parâmetros, horário, nonce e hash do corpo. A API valida assinatura, estado da aprovação, relógio e nonce único. A janela de horário é de 60 segundos; mantenha sincronização NTP. Convites inválidos têm limite de tentativas. A API pública não aceita o token interno do executor de nuvem como credencial de controlador.

Revogar impede novas requisições. Isso não cancela operações já aceitas pelo sistema operacional ou pelo provedor. Somente um controlador pode estar aprovado por campus. A perda da chave exige novo pacote e aprovação.

No Linux, o serviço usa usuário sem login, `NoNewPrivileges`, sistema de arquivos protegido e escrita limitada ao diretório de dados. No Windows, usa Local Service com privilégio limitado; Python precisa estar instalado para todos os usuários. Arquivos privados usam permissões restritas. Local Service é uma identidade compartilhada do Windows; outros serviços comprometidos nessa mesma identidade representam risco e precisam ser considerados na revisão do servidor.

WinRM exige validação TLS, NTLM com criptografia de mensagens ou Kerberos. O programa não habilita Basic, não desativa certificados e não configura TrustedHosts com curinga. IPs, DNS e broadcasts são limitados por `ALLOWED_CIDRS`. As operações não aceitam scripts arbitrários do navegador.

Wake-on-LAN é um mecanismo da rede local sem autenticação própria. Segmente a rede e autorize encaminhamento entre VLANs com a equipe de redes. Não exponha WinRM ou UDP de Wake-on-LAN à internet.

## Hospedagem e dados

Use HTTPS e confie em cabeçalhos de proxy somente do proxy conhecido. Os contêineres rodam sem root, com sistema de arquivos somente para leitura e sem privilégios adicionais. O exemplo AWS usa SSM, sem porta SSH pública, IMDSv2 e volume EBS criptografado separado para os dados.

Execute uma réplica da aplicação com o banco SQLite persistente. Faça backups consistentes com a API de backup do SQLite e teste a restauração. Guarde banco, ambiente privado e chave emissora em armazenamento protegido fora do host. O repositório não configura sozinho retenção, recuperação de desastre ou disponibilidade entre zonas.

Ações de administrador são privadas para sua própria conta na interface e na API. Essa regra não oculta dados de quem administra o banco, o servidor ou os backups. Defina acesso, retenção e auditoria de infraestrutura com a instituição.

Não publique `.env`, chaves, bancos, pacotes de pareamento ou `runtime/`. Não inclua senhas em logs, argumentos de processo, issues ou capturas de tela. Não reutilize contas de apresentação em produção.

## Verificação e relato

A suíte cobre autenticação, CSRF, autorização por andar, revogação, privacidade da auditoria, filas, descoberta, integridade dos pacotes e proteção contra repetição. Testes automatizados e auditorias de dependências não substituem revisão independente, validação de rede ou teste de intrusão autorizado.

Para relatar uma falha, use o canal privado definido pelos responsáveis pela implantação. Não publique credenciais, inventário interno ou detalhes de exploração em uma issue pública.
