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
GOOGLE_CLOUD_VISION_API_KEY=your_google_vision_api_key
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4.1-mini
```

| Variable | Description |
|---|---|
| `DISCORD_TOKEN` | Your bot token from the Discord Developer Portal |
| `LEAGUE_NAME` | Name displayed in the list embed |
| `LEAGUE_EMOJI` | Emoji displayed in the embed title |
| `ADMIN_IDS` | Comma-separated user IDs with admin permission |
| `IMAGE_CHANNEL_ID` | Discord channel ID for Dota match screenshot uploads |
| `GOOGLE_CLOUD_VISION_API_KEY` | API key for Google Cloud Vision OCR |
| `OPENAI_API_KEY` | API key for OpenAI to interpret OCR text with LLM |
| `OPENAI_MODEL` | Optional OpenAI model for match interpretation (default `gpt-4.1-mini`) |

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

---

## 🤝 Contributing

Found a bug or have an idea for a new feature? Feel free to [open an issue](https://github.com/fabiosabah/discord-bot-uefa/issues) — contributions are welcome!

---

## 📄 License

This project has no defined license. Feel free to use and adapt it for your own server.