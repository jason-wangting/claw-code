# Claw Agent Stdio Design

## 1. Goal

Define a stable machine protocol for driving `claw` as a long-lived process from external platforms (for example, a NestJS backend), using `stdin`/`stdout` with JSON lines.

This design targets:

- Multi-turn conversation over one persistent process
- Explicit permission handshakes (no terminal `y/N` parsing)
- Structured incremental output for UI streaming
- Cancel/shutdown semantics suitable for production services

## 2. Decisions (Locked)

- Conversation server command: `claw agent serve --stdio`
- Keep `--stdio` explicitly for transport clarity and future extensibility
- Multi-turn model in v0: one persistent process per conversation, one active turn at a time

## 3. Current Serve Status

Implemented in current code:

- `start_session` -> `ready`
- `turn` -> streaming `delta` events + terminal `final`
- `shutdown`
- `error` on invalid/unsupported/failure paths

Not implemented yet (returns `UNSUPPORTED_EVENT`):

- `permission_decision`
- `cancel`

## 4. Command Surface (Proposed)

Chosen command:

```bash
claw agent serve --stdio
```

Behavior:

- Runs as a long-lived process
- Reads one JSON event per line from `stdin`
- Writes one JSON event per line to `stdout`
- Writes diagnostics/logs only to `stderr`

## 5. Protocol (Draft v0)

### 5.1 Transport

- Encoding: UTF-8
- Framing: JSON Lines (`\n` delimited)
- One event object per line

### 5.2 Client -> Claw events

`start_session`

```json
{ "type": "start_session", "sessionId": "sess-1", "cwd": "/repo", "model": "sonnet" }
```

Current mapping in existing code:

- Functionality: create runtime + session handle and persist managed session file
- Main implementation:
  - `LiveCli::new(...)` in [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)
  - Uses `Session::new()`, `create_managed_session_handle(...)`, `build_runtime(...)`, `persist_session()`

`turn`

```json
{ "type": "turn", "requestId": "req-1", "input": "summarize this file" }
```

Current mapping in existing code:

- Functionality: run one conversation turn against current session state
- Main implementation:
  - `LiveCli::run_turn_with_output(...)` / `run_turn(...)` / `run_prompt_json(...)` in [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)
  - Core runtime loop: `ConversationRuntime::run_turn(...)` in [conversation.rs](/Users/jason/Work/open-source/claw-code/rust/crates/runtime/src/conversation.rs)

`permission_decision`

```json
{
  "type": "permission_decision",
  "requestId": "req-1",
  "permissionId": "perm-1",
  "decision": "allow"
}
```

Current mapping in existing code:

- Functionality: approve/deny tool execution when policy requires prompt
- Main implementation:
  - `CliPermissionPrompter::decide(...)` in [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)
  - Current transport is terminal text + stdin line (`y/yes` => allow, else deny)
  - In `serve --stdio`, this will be replaced by protocol event input

`cancel`

```json
{ "type": "cancel", "requestId": "req-1" }
```

Current mapping in existing code:

- Functionality: abort is currently tied to Ctrl+C hook monitor, not request-id API cancel
- Main implementation:
  - `HookAbortMonitor::spawn(...)` + `HookAbortSignal::abort()` in [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)
  - Runtime hook cancellation plumbing in [hooks.rs](/Users/jason/Work/open-source/claw-code/rust/crates/runtime/src/hooks.rs)
- Gap:
  - No existing structured per-request `cancel` event in current CLI surface

`shutdown`

```json
{ "type": "shutdown" }
```

Current mapping in existing code:

- Functionality: graceful loop exit and session persistence
- Main implementation:
  - REPL exit path in `run_repl(...)` on `/exit`/`/quit` with `persist_session()` in [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)
  - Process-level fatal handling in `main()`/`run()` paths
- In `serve --stdio`, `shutdown` should map to this graceful close behavior

### 5.3 Claw -> Client events

`ready`

```json
{ "type": "ready", "sessionId": "sess-1", "protocolVersion": "0" }
```

What this should send:

- Session initialized and ready to accept `turn`
- Suggested payload source: session id + protocol version

Closest current implementation:

- `LiveCli::new(...)` success means runtime is ready
- REPL currently sends human text banner (`startup_banner`) rather than structured event

`delta`

```json
{ "type": "delta", "requestId": "req-1", "text": "partial text" }
```

What this should send:

- Incremental assistant text chunks while model stream is in progress

Typical current emission path:

- Model stream delta handling in `AnthropicRuntimeClient::stream(...)`
- On text delta:
  - writes rendered chunk to stdout (`write!(out, ...)`)
  - records `AssistantEvent::TextDelta(text)`
- Main code: [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)
- Current status: implemented in `agent serve --stdio` (emits JSONL `delta` per streamed text chunk)

`permission_request`

```json
{
  "type": "permission_request",
  "requestId": "req-1",
  "permissionId": "perm-1",
  "tool": "Bash",
  "reason": "command may modify state",
  "input": "{\"command\":\"rm -rf tmp\"}"
}
```

What this should send:

- Structured permission request with tool name, reason, input, and permission id

Typical current emission path:

- `CliPermissionPrompter::decide(...)` prints:
  - `Permission approval required`
  - tool / current mode / required mode / reason / input
  - `Approve this tool call? [y/N]:`
- Main code: [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)
- In `serve --stdio`, this print path should become `permission_request` JSON event

`final`

```json
{
  "type": "final",
  "requestId": "req-1",
  "text": "final assistant text",
  "usage": { "input_tokens": 10, "output_tokens": 20 }
}
```

What this should send:

- Final assistant text for the turn plus summary metadata (`usage`, etc.)

Typical current emission path:

- JSON one-shot output in `run_prompt_json(...)` prints final payload with:
  - `message`, `model`, `iterations`, `tool_uses`, `tool_results`, `usage`, `estimated_cost`, ...
- Main code: [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)

`cancelled`

```json
{ "type": "cancelled", "requestId": "req-1" }
```

What this should send:

- Confirmation that an active turn has been cancelled and closed

Current status:

- No dedicated structured cancellation output event today (gap to implement in serve mode)

`error`

```json
{ "type": "error", "requestId": "req-1", "code": "INVALID_EVENT", "message": "..." }
```

What this should send:

- Protocol validation/runtime errors with machine-readable `code` and message

Typical current emission path:

- CLI/user-facing errors are printed via `eprintln!` in `main()`/`run()` and related failure branches
- MCP server uses structured JSON-RPC errors in `mcp_server` dispatch paths
- Main code references:
  - [main.rs](/Users/jason/Work/open-source/claw-code/rust/crates/rusty-claude-cli/src/main.rs)
  - [mcp_server.rs](/Users/jason/Work/open-source/claw-code/rust/crates/runtime/src/mcp_server.rs)

## 6. State Model (Draft)

Process-level states:

- `booting` -> `ready` -> `closing`

Turn-level states (per request):

- `accepted` -> `streaming`
- `streaming` -> `awaiting_permission` (optional, repeatable)
- `streaming` -> `final` | `cancelled` | `error`

Rule:

- One active `turn` at a time per process in v0.
- If a new `turn` arrives while one is running: return `error` with `TURN_ALREADY_RUNNING`.

## 7. NestJS Integration Model

Recommended mapping:

- 1 WebSocket conversation -> 1 `claw agent serve --stdio` child process
- NestJS keeps:
  - process handle
  - line buffer remainder for stdout
  - active `requestId`
  - pending permission requests map

WebSocket event mapping (draft):

- `copilot:input` -> send `turn`
- `copilot:cancel` -> send `cancel`
- `copilot:permission` -> send `permission_decision`
- child `delta` -> emit `copilot:chunk`
- child `final` -> emit `copilot:done`
- child `error` -> emit `copilot:error`

## 8. Error Handling Requirements

- Invalid JSON line: emit `error` and continue process
- Unknown event type: emit `error`
- Missing required fields: emit `error`
- Broken stdout pipe: terminate with non-zero exit code
- On `shutdown`: flush final state and exit `0`

## 9. Observability

- Structured logs to `stderr` only
- Include `sessionId` and `requestId` in all error logs
- Optional heartbeat event in a later version (`ping`/`pong`)

## 10. Security Considerations

- Honor existing permission policy
- Never execute tools before permission resolution where required
- Validate `cwd` boundaries against configured sandbox policy
- Do not leak secrets in protocol errors

## 11. Milestones

M1: Protocol skeleton

- Parse/validate JSONL input
- Emit `ready`, `error`
- Implement `shutdown`

M2: Single-turn streaming

- Implement `turn` -> `delta`/`final`
- Implement `cancel`

M3: Permission handshake

- Implement `permission_request` + `permission_decision`

M4: NestJS reference adapter

- Example service + gateway mapping with reconnection and cleanup

## 12. Open Questions

1. Should v0 allow multiple concurrent turns per process?
2. Should `start_session` be mandatory or optional with defaults?
3. Should `final` include full tool traces, or only assistant text + usage?
4. Do we need protocol-level auth between parent process and child?
