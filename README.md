# 🎮 Discord Bot — Lista de Presença

Bot para Discord desenvolvido em Python que gerencia **listas de presença interativas** para inhouses de Dota 2. Apesar do contexto original ser Dota, o bot é genérico e pode ser usado para organizar qualquer evento com vagas limitadas.

---

## ✨ Funcionalidades

- **Criação de lobby interativo** com embed atualizado em tempo real
- **Limite configurável de jogadores** (padrão: 10)
- **Lista de espera automática** — quando a lista está cheia, novos jogadores entram na fila
- **Promoção automática** da espera ao principal quando alguém sai
- **Controle de acesso**: apenas o criador da lista ou admins podem adicionar/remover pessoas e encerrar o lobby
- **Botões interativos** diretamente na mensagem do Discord (sem necessidade de digitar comandos para entrar/sair)
- **IDs de sessão diários** — o contador reinicia todo dia
- **Suporte a deploy no Railway** via `Procfile`

---

## 🖼️ Como funciona

Ao executar o comando `!lista`, o bot cria uma mensagem embed com os seguintes botões:

| Botão | Descrição |
|---|---|
| ✋ Entrar | Entra na lista (ou na espera se estiver cheia) |
| 🚪 Sair | Sai da lista ou da espera |
| ➕ Adicionar pessoa | (Host/Admin) Adiciona outro usuário via seletor |
| 👤 Remover pessoa | (Host/Admin) Remove alguém da lista ou da espera |
| 🔒 Encerrar lista | (Host/Admin) Fecha o lobby |

---

## 🗂️ Estrutura do projeto

```
discord-bot-uefa/
├── bot.py           # Inicialização do bot e configuração de intents
├── commands.py      # Registro dos comandos (!lista, !lobby, !inhouse)
├── models.py        # Modelo LobbySession com toda a lógica de estado
├── views.py         # Views e botões interativos (discord.ui)
├── helpers.py       # Funções auxiliares (ex: encerrar sessão)
├── config.py        # Configurações via variáveis de ambiente
├── requirements.txt # Dependências Python
└── Procfile         # Configuração para deploy no Railway
```

---

## ⚙️ Configuração

### Variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto com as seguintes variáveis:

```env
DISCORD_TOKEN=seu_token_aqui
LEAGUE_NAME=Nome da sua liga
LEAGUE_EMOJI=🎮
ADMIN_IDS=123456789,987654321
```

| Variável | Descrição |
|---|---|
| `DISCORD_TOKEN` | Token do bot no Discord Developer Portal |
| `LEAGUE_NAME` | Nome exibido no embed da lista |
| `LEAGUE_EMOJI` | Emoji exibido no título do embed |
| `ADMIN_IDS` | IDs dos usuários com permissão de admin (separados por vírgula) |

> **Nota:** O número máximo de jogadores (`MAX_PLAYERS`) está definido como `10` diretamente em `config.py`.

---

## 🚀 Como rodar

### Pré-requisitos

- Python 3.10+
- Uma aplicação criada no [Discord Developer Portal](https://discord.com/developers/applications) com as permissões necessárias

### Instalação

```bash
# Clone o repositório
git clone https://github.com/fabiosabah/discord-bot-uefa.git
cd discord-bot-uefa

# Instale as dependências
pip install -r requirements.txt

# Configure o .env (veja seção acima)

# Rode o bot
python bot.py
```

### Permissões necessárias no Discord

Ao adicionar o bot ao servidor, garanta que ele tenha:
- `Send Messages`
- `Manage Messages` (para deletar o comando original)
- `Read Message History`
- `Use External Emojis` (opcional)

Nos **Privileged Gateway Intents** do Developer Portal, habilite:
- `Message Content Intent`
- `Server Members Intent`

---

## 📦 Deploy no Railway

O projeto já inclui um `Procfile` para deploy no [Railway](https://railway.app). Siga os passos:

1. Crie um novo projeto no Railway e conecte ao repositório GitHub
2. Na aba **Variables**, adicione as variáveis de ambiente:

```
DISCORD_TOKEN=seu_token
LEAGUE_NAME=Minha Liga
LEAGUE_EMOJI=🎮
ADMIN_IDS=123456789
```

3. O Railway detectará o `Procfile` automaticamente e iniciará o bot

---

## 🛠️ Comandos disponíveis

| Comando | Aliases | Descrição |
|---|---|---|
| `!lista` | `!lobby`, `!inhouse` | Cria uma nova lista de presença |

---

## 📄 Licença

Este projeto não possui uma licença definida. Sinta-se livre para usar e adaptar para o seu servidor.