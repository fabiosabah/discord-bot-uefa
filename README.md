# 🎮 Liga Discord — Dota 2 Inhouse Bot

> 🇧🇷 [Versão em Português](./README.pt-BR.md)

A Discord bot written in Python that manages **interactive inhouse lobbies**, **automatic match imports via OCR**, and **full league statistics** for Dota 2 groups.

---

## ✨ Features

- **Interactive lobby** with real-time embed, waitlist and auto-promotion
- **Scoreboard OCR** — upload a screenshot and the bot automatically extracts players, heroes, KDA and score via Gemini/OpenAI
- **League standings** based on real match history
- **Player profile** with winrate, average KDA, favorite heroes, best partners and nemesis
- **Head-to-head** — full history of two players together and as rivals
- **Duration records** — biggest stomps and longest matches
- **Alias system** — links in-game nicknames to Discord accounts
- **Role-based access** via configurable admin IDs
- **Railway deploy** via `Procfile`

---

## 🖼️ OCR Flow

1. A player uploads a scoreboard screenshot to the configured OCR channel
2. The bot queues the job and processes the image with Gemini/OpenAI
3. The bot posts a summary in the channel with players, heroes, KDA and next steps
4. The admin fixes any misread values (`!ocrhero`, `!ocrnick`)
5. The admin confirms with `!ok <job_id> MM:SS` — the match is saved and the summary is deleted from chat
6. Stats and standings update automatically

---

## 🗂️ Project structure

```
liga-discord-py/
├── bot.py                        # Bot init, OCR background worker, events
├── core/
│   ├── config.py                 # Environment variables
│   ├── ocr.py                    # AI image processing
│   ├── dota_heroes.py            # Hero list and name resolution
│   ├── utils/
│   └── db/
│       ├── connection.py         # SQLite connection and migrations
│       ├── player_repo.py        # Players and aliases
│       ├── match_repo.py         # Matches and statistics
│       ├── audit_repo.py         # Admin action log
│       ├── ocr_repo.py           # OCR jobs
│       └── lobby_repo.py         # Lobby sessions
├── domain/
│   └── models.py                 # LobbySession
├── services/
│   └── lobby_service.py          # Lobby close logic
└── ui/
    ├── views/
    │   └── lobby_view.py         # Interactive lobby buttons
    └── commands/
        ├── lobby_commands.py     # !lista
        ├── player_commands.py    # !tabela, !perfil, !duelo, etc.
        ├── match_commands.py     # !venceu, !perdeu, !registrar, etc.
        ├── ocr_commands.py       # !ok, !ocrhero, !ocrnick, etc.
        ├── admin_commands.py     # !cadastro, !addalias, etc.
        └── score_helpers.py      # Shared utilities
```

---

## ⚙️ Configuration

### Environment variables

```env
DISCORD_TOKEN=your_token_here
LEAGUE_NAME=My League
LEAGUE_EMOJI=🎮
ADMIN_IDS=123456789,987654321
IMAGE_CHANNEL_ID=111222333444555666
OPENAI_API_KEY=your_gemini_or_openai_key
OPENAI_MODEL=gemini-2.0-flash
```

| Variable | Required | Description |
|---|---|---|
| `DISCORD_TOKEN` | ✅ | Bot token from the Discord Developer Portal |
| `LEAGUE_NAME` | ✅ | League name shown in embeds |
| `LEAGUE_EMOJI` | — | Title emoji (default: 🎮) |
| `ADMIN_IDS` | ✅ | Comma-separated Discord user IDs with admin access |
| `IMAGE_CHANNEL_ID` | — | Default channel for image reading (can also be set with `!registrarcanalimagem`) |
| `OPENAI_API_KEY` | — | Gemini or OpenAI key for OCR (OCR is disabled without it) |
| `OPENAI_MODEL` | — | AI model for OCR (default: `gemini-2.0-flash`) |

---

## 🚀 Running locally

```bash
git clone https://github.com/fabiosabah/discord-bot-uefa.git
cd discord-bot-uefa

pip install -r requirements.txt

# Create a .env with the variables above
python bot.py
```

### Required Discord permissions

- `Send Messages`
- `Manage Messages` (to delete command messages)
- `Read Message History`
- `Embed Links`

Under **Privileged Gateway Intents** in the Developer Portal, enable:
- `Message Content Intent`
- `Server Members Intent`

---

## 📦 Deploy on Railway

1. Create a new project on [Railway](https://railway.app) and connect your GitHub repository
2. Add environment variables under the **Variables** tab
3. Railway detects the `Procfile` automatically and starts the bot

---

## 🛠️ Commands

### Lobby

| Command | Aliases | Description |
|---|---|---|
| `!lista` | `!lobby`, `!inhouse` | Creates a new interactive signup list |

### Standings & Rankings

| Command | Aliases | Description |
|---|---|---|
| `!tabela` | `!tabela2` | Standings based on real match history |
| `!tabela1` | `!tabelamanual` | Manual standings (from `!venceu`/`!perdeu`) |
| `!top [n]` | — | Top N players (default: 10, max: 15) |

### Profile & Stats

| Command | Aliases | Description |
|---|---|---|
| `!perfil [@player]` | `!perfil2` | Full profile with OCR match stats |
| `!perfil1 [@player]` | — | Profile based on manual data |
| `!ultimas [@player]` | `!ultimaspartidas`, `!recentes` | Recent matches (up to 200) |
| `!listarpartidas [@player] [limit]` | `!partidas` | Detailed match list with KDA and date |
| `!historico [@player] [limit]` | `!history` | Match history (manual table) |
| `!heroes [hero]` | `!herois`, `!heropool` | Global hero stats or details for a single hero |
| `!duelo @a [@b]` | `!vs`, `!versus`, `!rivalidade` | Head-to-head between two players (if `@b` omitted, uses command author) |
| `!recordes` | `!tempos`, `!duracoes` | 5 biggest stomps and 5 longest matches |
| `!id <match_id>` | — | Full details for a match |

### Match Registration (admin)

| Command | Aliases | Description |
|---|---|---|
| `!registrar @user wins losses` | — | Manually set a player's record |
| `!venceu @user...` | `!venceu_id` | Register a win |
| `!perdeu @user...` | `!perdeu_id` | Register a loss |
| `!desfazer` | `!undo`, `!z` | Undo last `!venceu`/`!perdeu` |
| `!deletar @user` | — | Remove player from rankings |
| `!registrarmatch <id> @wins -- @losses` | `!matchmanual`, `!matchfix` | Manually register a match |
| `!apagarid <match_id>` | `!apagarmatch`, `!delmatch` | Delete a match (limit: 1/day, max 24h after creation) |
| `!debugpartidas [limit]` | `!auditmatches` | Match event log |

### OCR (admin)

| Command | Aliases | Description |
|---|---|---|
| `!ok <job_id> [MM:SS]` | `!ocrok` | Confirm and import a match; deletes the summary from chat |
| `!ocrtime <match_id> <MM:SS>` | `!settime` | Fix duration of an already-imported match |
| `!ocrhero <job_id> <slot> <hero>` | — | Fix hero for a slot in the job |
| `!ocrnick <job_id> <slot> <nick>` | — | Fix nickname for a slot in the job |
| `!ocruser <job_id> [slot] @user...` | — | Map Discord users to job slots |
| `!setjobwinner <job_id> radiant\|dire` | — | Set the winning team in the job |
| `!imagemresumo <job_id>` | `!resumoimagem` | Show human-readable OCR summary |
| `!detalhesimagem <job_id>` | `!imagedetails` | Show processed JSON |
| `!rawtextimagem <job_id>` | `!rawtext` | Show raw OCR text |
| `!pendenciaimagem [limit]` | `!pendingimages` | List pending jobs |
| `!removerimagem <job_id> confirmar` | `!deleteimage` | Remove an OCR job |
| `!reenfileirarimagens [channel] [limit]` | `!scanimages` | Re-scan message history for images |
| `!fixhero <match_id> <slot> <hero>` | — | Fix hero in an imported match |
| `!nick <match_id> <slot> <nick> @user` | `!setnick` | Fix player nick in an imported match |
| `!definirherois <match_id> h1, h2...` | `!setmatchheroes` | Set heroes in slot order |
| `!definirjogadores <match_id> @p1 @p2...` | `!setmatchplayers` | Set players in slot order |

### Administration

| Command | Aliases | Description |
|---|---|---|
| `!registrarcanalimagem` | `!registrarcanalocr` | Set current channel as OCR image channel |
| `!limparcanalimagem` | `!limparcanalocr` | Remove configured OCR channel |
| `!canalimagem` | — | Show current OCR channel |
| `!cadastro <nick> @user` | — | Register a player's in-game nick |
| `!addalias @user <alias>` | `!alias` | Add alias for a player |
| `!removealias @user <alias>` | `!delalias` | Remove alias from a player |
| `!aliases @user` | `!aliaslist` | List aliases for a player |
| `!listaraliases` | `!allaliases`, `!todosaliases` | List all registered aliases |
| `!jogadoresfaltando` | `!faltando` | List players in matches without a Discord account |
| `!fixkda [sim]` | `!corrigirkda` | Diagnose and fix invalid KDA values |

---

## 📄 License

No license defined. Feel free to use and adapt for your own server.
