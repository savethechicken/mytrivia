# Stream Trivia App

Starter implementation for a local trivia engine designed to work with Streamer.bot and browser overlays.

## Run

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8787
```

## Endpoints

- `GET /health`
- `POST /game/start`
- `POST /game/stop`
- `POST /game/reset`
- `POST /game/skip`
- `POST /chat/message`
- `GET /game/state`

## Overlays

- Trivia panel: `http://127.0.0.1:8787/overlays/trivia.html`
- Leaderboard panel: `http://127.0.0.1:8787/overlays/leaderboard.html`
