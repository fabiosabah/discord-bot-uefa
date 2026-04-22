# 🎮 Liga Discord — Bot de Inhouse Dota 2

> 🇺🇸 [English version](./README.md)

Bot para Discord desenvolvido em Python que gerencia **lobbies de inhouse**, **importação automática de partidas via OCR** e **estatísticas completas de liga** para grupos de Dota 2.

---

## ✨ Funcionalidades

- **Lobby interativo** com embed atualizado em tempo real, lista de espera e auto-promoção
- **OCR de placar** — envia um print do scoreboard, o bot extrai jogadores, heróis, KDA e placar automaticamente via Gemini/OpenAI
- **Tabela de classificação** baseada no histórico real de partidas
- **Perfil de jogador** com winrate, KDA médio, heróis favoritos, parceiros e nemesis
- **Duelo entre jogadores** — histórico de partidas juntos e como rivais
- **Recordes de duração** — maiores stomps e partidas mais longas
- **Sistema de aliases** — vincula nicks do jogo a contas Discord
- **Controle de acesso** por IDs de admin configuráveis
- **Deploy no Railway** via `Procfile`

---

## 🖼️ Fluxo OCR

1. Um jogador envia um print do scoreboard no canal configurado para OCR
2. O bot enfileira o job e processa a imagem com Gemini/OpenAI
3. O bot posta um resumo no canal com jogadores, heróis, KDA e próximos passos
4. O admin corrige eventuais erros de leitura (`!ocrhero`, `!ocrnick`)
5. O admin confirma com `!ok <job_id> MM:SS` — a partida é registrada e o resumo apagado do chat
6. Stats e tabela são atualizados automaticamente

---

## 🗂️ Estrutura do projeto

```
liga-discord-py/
├── bot.py                        # Inicialização, worker OCR, eventos
├── core/
│   ├── config.py                 # Variáveis de ambiente
│   ├── ocr.py                    # Processamento de imagem com IA
│   ├── dota_heroes.py            # Lista e resolução de nomes de heróis
│   ├── utils/
│   └── db/
│       ├── connection.py         # Conexão SQLite e migrations
│       ├── player_repo.py        # Jogadores e aliases
│       ├── match_repo.py         # Partidas e estatísticas
│       ├── audit_repo.py         # Log de ações administrativas
│       ├── ocr_repo.py           # Jobs de OCR
│       └── lobby_repo.py         # Sessões de lobby
├── domain/
│   └── models.py                 # LobbySession
├── services/
│   └── lobby_service.py          # Lógica de encerramento de lobby
└── ui/
    ├── views/
    │   └── lobby_view.py         # Botões interativos do lobby
    └── commands/
        ├── lobby_commands.py     # !lista
        ├── player_commands.py    # !tabela, !perfil, !duelo, etc.
        ├── match_commands.py     # !venceu, !perdeu, !registrar, etc.
        ├── ocr_commands.py       # !ok, !ocrhero, !ocrnick, etc.
        ├── admin_commands.py     # !cadastro, !addalias, etc.
        └── score_helpers.py      # Utilitários compartilhados
```

---

## ⚙️ Configuração

### Variáveis de ambiente

```env
DISCORD_TOKEN=seu_token_aqui
LEAGUE_NAME=Nome da Liga
LEAGUE_EMOJI=🎮
ADMIN_IDS=123456789,987654321
IMAGE_CHANNEL_ID=111222333444555666
OPENAI_API_KEY=sua_chave_gemini_ou_openai
OPENAI_MODEL=gemini-2.0-flash
```

| Variável | Obrigatória | Descrição |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Token do bot no Discord Developer Portal |
| `LEAGUE_NAME` | ✅ | Nome da liga exibido nos embeds |
| `LEAGUE_EMOJI` | — | Emoji do título (padrão: 🎮) |
| `ADMIN_IDS` | ✅ | IDs Discord dos admins, separados por vírgula |
| `IMAGE_CHANNEL_ID` | — | Canal padrão para leitura de imagens (pode ser configurado via `!registrarcanalimagem`) |
| `OPENAI_API_KEY` | — | Chave Gemini ou OpenAI para OCR (sem ela o OCR fica desativado) |
| `OPENAI_MODEL` | — | Modelo de IA para OCR (padrão: `gemini-2.0-flash`) |

---

## 🚀 Como rodar localmente

```bash
git clone https://github.com/fabiosabah/discord-bot-uefa.git
cd discord-bot-uefa

pip install -r requirements.txt

# Crie um .env com as variáveis acima
python bot.py
```

### Permissões necessárias no Discord

- `Send Messages`
- `Manage Messages` (para apagar mensagens de comando)
- `Read Message History`
- `Embed Links`

Nos **Privileged Gateway Intents** do Developer Portal, habilite:
- `Message Content Intent`
- `Server Members Intent`

---

## 📦 Deploy no Railway

1. Crie um projeto no [Railway](https://railway.app) e conecte o repositório GitHub
2. Adicione as variáveis de ambiente na aba **Variables**
3. O Railway detecta o `Procfile` automaticamente e inicia o bot

---

## 🛠️ Comandos

### Lobby

| Comando | Aliases | Descrição |
|---|---|---|
| `!lista` | `!lobby`, `!inhouse` | Cria uma nova lista interativa |

### Tabela e Rankings

| Comando | Aliases | Descrição |
|---|---|---|
| `!tabela` | `!tabela2` | Tabela baseada no histórico real de partidas |
| `!tabela1` | `!tabelamanual` | Tabela manual (vitórias/derrotas registradas com `!venceu`/`!perdeu`) |
| `!top [n]` | — | Top N jogadores (padrão: 10, máx: 15) |

### Perfil e Estatísticas

| Comando | Aliases | Descrição |
|---|---|---|
| `!perfil [@jogador]` | `!perfil2` | Perfil completo com stats de partidas OCR |
| `!perfil1 [@jogador]` | — | Perfil baseado em dados manuais |
| `!ultimas [@jogador]` | `!ultimaspartidas`, `!recentes` | Últimas partidas (até 200) |
| `!listarpartidas [@jogador] [limite]` | `!partidas` | Lista detalhada com KDA e data |
| `!historico [@jogador] [limite]` | `!history` | Histórico de partidas (tabela manual) |
| `!heroes [herói]` | `!herois`, `!heropool` | Stats globais de heróis ou detalhes de um herói |
| `!duelo @a [@b]` | `!vs`, `!versus`, `!rivalidade` | Duelo entre dois jogadores (se `@b` omitido, usa quem digitou) |
| `!recordes` | `!tempos`, `!duracoes` | 5 maiores stomps e 5 partidas mais longas |
| `!id <match_id>` | — | Detalhes completos de uma partida |

### Registro de Partidas (admin)

| Comando | Aliases | Descrição |
|---|---|---|
| `!registrar @usuario wins losses` | — | Define placar manual de um jogador |
| `!venceu @usuario...` | `!venceu_id` | Registra vitória |
| `!perdeu @usuario...` | `!perdeu_id` | Registra derrota |
| `!desfazer` | `!undo`, `!z` | Desfaz última ação de `!venceu`/`!perdeu` |
| `!deletar @usuario` | — | Remove jogador do ranking |
| `!registrarmatch <id> @wins -- @losses` | `!matchmanual`, `!matchfix` | Registra partida manualmente |
| `!apagarid <match_id>` | `!apagarmatch`, `!delmatch` | Apaga partida (limite: 1/dia, máx 24h após criação) |
| `!debugpartidas [limite]` | `!auditmatches` | Log de eventos de partidas |

### OCR (admin)

| Comando | Aliases | Descrição |
|---|---|---|
| `!ok <job_id> [MM:SS]` | `!ocrok` | Confirma e importa a partida; apaga o resumo do chat |
| `!ocrtime <match_id> <MM:SS>` | `!settime` | Corrige duração de uma partida já importada |
| `!ocrhero <job_id> <slot> <herói>` | — | Corrige herói de um slot no job |
| `!ocrnick <job_id> <slot> <nick>` | — | Corrige nick de um slot no job |
| `!ocruser <job_id> [slot] @usuario...` | — | Vincula usuários Discord aos slots do job |
| `!setjobwinner <job_id> radiant\|dire` | — | Define o time vencedor no job |
| `!imagemresumo <job_id>` | `!resumoimagem` | Mostra resumo legível do OCR |
| `!detalhesimagem <job_id>` | `!imagedetails` | Exibe JSON processado |
| `!rawtextimagem <job_id>` | `!rawtext` | Exibe texto bruto extraído da imagem |
| `!pendenciaimagem [limite]` | `!pendingimages` | Lista jobs pendentes |
| `!removerimagem <job_id> confirmar` | `!deleteimage` | Remove um job de OCR |
| `!reenfileirarimagens [canal] [limite]` | `!scanimages` | Re-escaneia histórico de mensagens |
| `!fixhero <match_id> <slot> <herói>` | — | Corrige herói numa partida já importada |
| `!nick <match_id> <slot> <nick> @usuario` | `!setnick` | Corrige nick numa partida importada |
| `!definirherois <match_id> h1, h2...` | `!setmatchheroes` | Define heróis na ordem dos slots |
| `!definirjogadores <match_id> @p1 @p2...` | `!setmatchplayers` | Define jogadores na ordem dos slots |

### Administração

| Comando | Aliases | Descrição |
|---|---|---|
| `!registrarcanalimagem` | `!registrarcanalocr` | Configura o canal atual para leitura de imagens |
| `!limparcanalimagem` | `!limparcanalocr` | Remove o canal OCR configurado |
| `!canalimagem` | — | Mostra o canal OCR atual |
| `!cadastro <nick> @usuario` | — | Cadastra nick de jogo e vincula ao Discord |
| `!addalias @usuario <alias>` | `!alias` | Adiciona alias para um jogador |
| `!removealias @usuario <alias>` | `!delalias` | Remove alias de um jogador |
| `!aliases @usuario` | `!aliaslist` | Lista aliases de um jogador |
| `!listaraliases` | `!allaliases`, `!todosaliases` | Lista todos os aliases cadastrados |
| `!jogadoresfaltando` | `!faltando` | Lista jogadores em partidas sem cadastro |
| `!fixkda [sim]` | `!corrigirkda` | Diagnóstica e corrige valores de KDA inválidos |

---

## 📄 Licença

Este projeto não possui licença definida. Sinta-se livre para usar e adaptar para o seu servidor.
