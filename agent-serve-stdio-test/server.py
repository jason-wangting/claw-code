#!/usr/bin/env python3
import asyncio
import json
import os
import signal
import uuid
from pathlib import Path

from aiohttp import web, WSMsgType


HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8787"))
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CLAW_RELATIVE = (BASE_DIR / ".." / "rust" / "target" / "debug" / "claw").resolve()
CLAW_CMD = os.environ.get(
    "CLAW_CMD",
    str(DEFAULT_CLAW_RELATIVE) if DEFAULT_CLAW_RELATIVE.exists() else "claw",
)
OPENAI_BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "openai/gpt-5.4-mini")
CLAW_WORKDIR = Path(
    os.environ.get(
        "CLAW_WORKDIR",
        "/Users/jason/Work/WFC/code/wmb-core/packages/site-scope/server/build/uploads/development/applications/cmnnzkyqh0037zzjp87rof705",
    )
)


def random_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


async def safe_ws_send(ws: web.WebSocketResponse, payload: dict) -> None:
    if not ws.closed:
        await ws.send_str(json.dumps(payload, ensure_ascii=True))


class ClawSession:
    def __init__(self, ws: web.WebSocketResponse) -> None:
        self.ws = ws
        self.process: asyncio.subprocess.Process | None = None
        self.stdout_task: asyncio.Task | None = None
        self.stderr_task: asyncio.Task | None = None
        self.session_id = random_id("sess")

    async def start(self) -> None:
        child_env = os.environ.copy()
        child_env["OPENAI_BASE_URL"] = OPENAI_BASE_URL
        child_env["OPENAI_API_KEY"] = OPENAI_API_KEY
        self.process = await asyncio.create_subprocess_exec(
            CLAW_CMD,
            "agent",
            "serve",
            "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(CLAW_WORKDIR),
            env=child_env,
        )
        self.stdout_task = asyncio.create_task(self._read_stdout())
        self.stderr_task = asyncio.create_task(self._read_stderr())

    async def _read_stdout(self) -> None:
        assert self.process and self.process.stdout
        while True:
            line = await self.process.stdout.readline()
            if not line:
                break
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                evt = json.loads(raw)
                await safe_ws_send(self.ws, {"type": "agent_event", "event": evt})
            except json.JSONDecodeError:
                await safe_ws_send(self.ws, {"type": "agent_stdout_raw", "text": raw})

    async def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        while True:
            line = await self.process.stderr.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            if text:
                await safe_ws_send(self.ws, {"type": "agent_stderr", "text": text})

    async def send_event(self, evt: dict) -> None:
        if not self.process or not self.process.stdin:
            return
        payload = (json.dumps(evt, ensure_ascii=True) + "\n").encode("utf-8")
        self.process.stdin.write(payload)
        await self.process.stdin.drain()

    async def stop(self) -> None:
        if self.process is None:
            return

        try:
            await self.send_event({"type": "shutdown"})
        except Exception:
            pass

        if self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=2)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()

        if self.stdout_task:
            self.stdout_task.cancel()
        if self.stderr_task:
            self.stderr_task.cancel()


async def index_handler(_request: web.Request) -> web.Response:
    index_path = BASE_DIR / "index.html"
    return web.Response(
        text=index_path.read_text(encoding="utf-8"),
        content_type="text/html",
    )


async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    await safe_ws_send(
        ws,
        {
            "type": "connected",
            "message": "WebSocket connected. First user message will start `claw agent serve --stdio`.",
        },
    )

    session: ClawSession | None = None

    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue

            try:
                data = json.loads(msg.data)
            except json.JSONDecodeError:
                await safe_ws_send(ws, {"type": "server_error", "message": "invalid client JSON"})
                continue

            msg_type = data.get("type")

            if msg_type == "user_input":
                text = str(data.get("text", "")).strip()
                if not text:
                    await safe_ws_send(ws, {"type": "server_error", "message": "text is required"})
                    continue

                if session is None:
                    session = ClawSession(ws)
                    try:
                        await session.start()
                    except Exception as err:
                        await safe_ws_send(
                            ws,
                            {"type": "server_error", "message": f"failed to start claw: {err}"},
                        )
                        session = None
                        continue

                    await safe_ws_send(ws, {"type": "session_started", "sessionId": session.session_id})
                    await session.send_event(
                        {
                            "type": "start_session",
                            "sessionId": session.session_id,
                            "cwd": str(CLAW_WORKDIR),
                            "model": OPENROUTER_MODEL,
                        }
                    )

                request_id = random_id("req")
                await session.send_event({"type": "turn", "requestId": request_id, "input": text})
                await safe_ws_send(ws, {"type": "turn_sent", "requestId": request_id, "text": text})
                continue

            if msg_type == "permission_decision":
                if session is None:
                    await safe_ws_send(ws, {"type": "server_error", "message": "session not started"})
                    continue
                request_id = data.get("requestId")
                permission_id = data.get("permissionId")
                decision = data.get("decision")
                if not request_id or not permission_id or not decision:
                    await safe_ws_send(
                        ws,
                        {
                            "type": "server_error",
                            "message": "requestId, permissionId, decision required",
                        },
                    )
                    continue
                await session.send_event(
                    {
                        "type": "permission_decision",
                        "requestId": request_id,
                        "permissionId": permission_id,
                        "decision": decision,
                    }
                )
                continue

            if msg_type == "cancel":
                if session is None:
                    continue
                request_id = data.get("requestId")
                if not request_id:
                    await safe_ws_send(
                        ws, {"type": "server_error", "message": "requestId required for cancel"}
                    )
                    continue
                await session.send_event({"type": "cancel", "requestId": request_id})
                continue

            if msg_type == "shutdown":
                if session is not None:
                    await session.send_event({"type": "shutdown"})
                continue

            await safe_ws_send(
                ws, {"type": "server_error", "message": f"unknown message type: {msg_type}"}
            )
    finally:
        if session is not None:
            await session.stop()
            code = session.process.returncode if session.process else None
            await safe_ws_send(ws, {"type": "agent_exit", "code": code, "signal": "terminated"})

    return ws


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index_handler)
    app.router.add_get("/index.html", index_handler)
    app.router.add_get("/ws", ws_handler)
    return app


def main() -> None:
    app = create_app()
    print(f"agent-serve-stdio-test running at http://{HOST}:{PORT}")
    print(f"WebSocket endpoint: ws://{HOST}:{PORT}/ws")
    web.run_app(app, host=HOST, port=PORT, handle_signals=True)


if __name__ == "__main__":
    main()
