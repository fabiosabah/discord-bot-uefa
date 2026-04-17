# 🎮 Discord Bot — Signup List

> 🇧🇷 [Versão em Português](./README.pt-BR.md)

A Discord bot written in Python that manages **interactive signup lists** for Dota 2 inhouses. Although originally built for Dota, it is generic enough to be used for any event with a limited number of slots.

---

## ✨ Features

- **Interactive lobby** with a real-time updated embed
- **Configurable player limit** (default: 10)
- **Automatic waitlist** — when the list is full, new players join the queue
- **Auto-promotion** from the waitlist when someone leaves
- **Access control**: only the host or admins can add/remove people and close the lobby
- **Interactive buttons** directly on the Discord message (no need to type commands to join/leave)
- **Daily session IDs** — the counter resets every day
- **Railway deploy support** via `Procfile`

---

## 🖼️ OCR de placar Dota 2

Este bot agora suporta o fluxo de captura e importação de partidas a partir de imagens de placar.

### Como funciona

1. Envie uma imagem de placar no canal configurado para OCR.
2. O bot adiciona o job em `match_screenshots` com status `pending`.
3. O worker background processa a imagem usando o modelo Gemini/OpenAI.
4. O bot responde no canal com um resumo do OCR e orientações de próximos comandos.
5. O administrador usa `!importarimagem <job_id> <mapeamento>` para registrar a partida no banco.
6. Se necessário, corrija heróis ou nomes com `!fixhero` e `!nick`.

### Comandos principais de OCR

- `!registrarcanalimagem` / `!registrarcanalocr`
  - Registra o canal atual como canal de imagem para processamento OCR.
- `!pendenciaimagem` / `!pendingimages`
  - Lista os jobs de imagem que ainda não foram processados.
- `!imagemresumo <job_id>` / `!resumoimagem`
  - Mostra um resumo legível do resultado OCR para o job.
- `!detalhesimagem <job_id>` / `!imagemjson`
  - Exibe o JSON processado pelo OCR.
- `!rawtextimagem <job_id>` / `!rawtext`
  - Exibe o texto OCR bruto extraído da imagem.
- `!importarimagem <job_id> <mapeamento>` / `!ocrimport`
  - Importa a imagem como partida no banco. O mapeamento pode usar slots ou nomes extraídos:
    - `1=@123456789012345678`
    - `"NomeOCR"=@123456789012345678`
    - `1=@123456789012345678 hero=Rubick`
- `!fixhero <league_match_id> <slot> <herói>`
  - Corrige o herói de um slot específico após a importação.
- `!nick <league_match_id> <slot> <novo nick> @Usuario`
  - Atualiza o nick de jogador no slot e vincula ao Discord.

### Dicas de uso

- Use `!addalias @Usuario NomeOCR` quando um nick novo aparecer pela primeira vez e o bot não souber o usuário.
- O job de OCR mostra slots, jogadores e heróis, então você pode mapear rapidamente cada slot para o usuário certo.
- Após `!importarimagem`, consulte `!id <league_match_id>` para revisar os dados da partida.

---

## 🖼️ How it works

When the `!lista` command is run, the bot creates an embed message with the following buttons:

| Button | Description |
|---|---|
| ✋ Entrar | Join the list (or the waitlist if full) |
| 🚪 Sair | Leave the list or the waitlist |
| ➕ Adicionar pessoa | (Host/Admin) Add another user via selector |
| 👤 Remover pessoa | (Host/Admin) Remove someone from the list or waitlist |
| 🔒 Encerrar lista | (Host/Admin) Close the lobby |

---

## 🗂️ Project structure

```
discord-bot-uefa/
├── bot.py           # Bot initialization and intents setup
├── commands.py      # Command registration (!lista, !lobby, !inhouse)
├── models.py        # LobbySession model with all state logic
├── views.py         # Interactive views and buttons (discord.ui)
├── helpers.py       # Helper functions (e.g. close session)
├── config.py        # Configuration via environment variables
├── requirements.txt # Python dependencies
└── Procfile         # Railway deploy configuration
```

---

## ⚙️ Configuration

### Environment variables

Create a `.env` file at the project root with the following variables:

```env
DISCORD_TOKEN=your_token_here
LEAGUE_NAME=Your League Name
LEAGUE_EMOJI=🎮
ADMIN_IDS=123456789,987654321
IMAGE_CHANNEL_ID=1489444995667591339
OPENAI_API_KEY=your_openai_or_gemini_api_key
OPENAI_MODEL=gemini-1.5-flash
```

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Your bot token from the Discord Developer Portal |
| `LEAGUE_NAME` | Name displayed in the list embed |
| `LEAGUE_EMOJI` | Emoji displayed in the embed title |
| `ADMIN_IDS` | Comma-separated user IDs with admin permission |
| `IMAGE_CHANNEL_ID` | Discord channel ID for Dota match screenshot uploads |
| `OPENAI_API_KEY` | API key for Gemini/OpenAI for direct image OCR and match parsing |
| `OPENAI_MODEL` | Optional model for image and text processing (default `gemini-1.5-flash`) |

> **Note:** The maximum number of players (`MAX_PLAYERS`) is set to `10` directly in `config.py`.

---

## 🚀 Running locally

### Prerequisites

- Python 3.10+
- An application created on the [Discord Developer Portal](https://discord.com/developers/applications)

### Installation

```bash
# Clone the repository
git clone https://github.com/fabiosabah/discord-bot-uefa.git
cd discord-bot-uefa

# Install dependencies
pip install -r requirements.txt

# Set up your .env (see section above)

# Run the bot
python bot.py
```

### Required Discord permissions

When adding the bot to your server, make sure it has:
- `Send Messages`
- `Manage Messages` (to delete the original command message)
- `Read Message History`
- `Use External Emojis` (optional)

In the **Privileged Gateway Intents** section of the Developer Portal, enable:
- `Message Content Intent`
- `Server Members Intent`

---

## 📦 Deploy on Railway

The project includes a `Procfile` for deployment on [Railway](https://railway.app). Follow these steps:

1. Create a new project on Railway and connect your GitHub repository
2. Under the **Variables** tab, add your environment variables:

```
DISCORD_TOKEN=your_token
LEAGUE_NAME=Your League
LEAGUE_EMOJI=🎮
ADMIN_IDS=123456789
```

3. Railway will automatically detect the `Procfile` and start the bot

---

## 🛠️ Available commands

| Command | Aliases | Description |
|---|---|---|
| `!lista` | `!lobby`, `!inhouse` | Creates a new signup list |
| `!definirherois <league_match_id> hero1, hero2, ...` | `!setmatchheroes`, `!setherois` | Sets heroes in slot order for an existing league match |
| `!definirjogadores <league_match_id> @p1 @p2 ...` | `!setmatchplayers`, `!setplayers` | Sets player names from Discord mentions in slot order for an existing league match |
| `!nick <league_match_id> <slot> <new_nick> @player` | `!setnick`, `!renomear` | Updates the player name for the specified slot in an existing league match |

> **Note:** the commands above are admin-only. Only user IDs configured in `ADMIN_IDS` can run them.

### Example usage

```text
!definirherois 123 Phantom Assassin, Juggernaut, Crystal Maiden, Ember Spirit, Lion, Axe, Tidehunter, Vengeful Spirit, Sniper, Pudge
!definirjogadores 123 @Player1 @Player2 @Player3 @Player4 @Player5 @Player6 @Player7 @Player8 @Player9 @Player10
```

---

## 🤝 Contributing

Found a bug or have an idea for a new feature? Feel free to [open an issue](https://github.com/fabiosabah/discord-bot-uefa/issues) — contributions are welcome!

---

## 📄 License

This project has no defined license. Feel free to use and adapt it for your own server.