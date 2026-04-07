# agent-serve-stdio-test

A minimal browser + Python WebSocket bridge to test `claw agent serve --stdio`.

## Files

- `index.html`: simple frontend WebSocket client
- `server.py`: aiohttp HTTP + WebSocket server, bridges to `claw` over stdio
- `requirements.txt`: Python dependency list

## Run

```bash
cd agent-serve-stdio-test
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

Open `http://127.0.0.1:8787` in your browser.

## Notes

- The backend starts `claw agent serve --stdio` only when the first `user_input` message is sent from the page.
- By default, the server tries this relative path first:
  - `../rust/target/debug/claw`
  and falls back to `claw` on PATH.
- Set `CLAW_CMD` to override.

Example:

```bash
CLAW_CMD="/absolute/path/to/claw" python server.py
```
