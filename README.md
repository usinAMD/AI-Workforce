# AI Workforce

Local-first AI workspace for Windows. Includes a persistent chat interface with conversation history and context-aware follow-ups, task history, a free-first provider registry, model calls, failover to up to three enabled free providers, checkpoints, pause and retry controls, and a live activity feed. It also includes a per-model token meter with context-window headroom and cumulative usage, plus a responsive spatial-glass dashboard with frosted panels and soft depth.

## Start on Windows
1. Install Python 3.10+ from https://www.python.org/downloads/windows/ and select Add Python to PATH.
2. Double-click start.bat.
3. Open http://127.0.0.1:8765.
4. For local inference, install Ollama from https://ollama.com/download, run ollama pull llama3.2, then configure Ollama under AI providers. Alternatively configure a free Gemini or OpenRouter API key. Consumer subscriptions do not necessarily include API access.

The server binds to 127.0.0.1. Data goes to %USERPROFILE%\.ai-workforce\workforce.sqlite3. Do not expose it to your network. Set AI_WORKFORCE_DATA or PORT before running python server.py to change data location or port.

## Providers and costs
Ollama, Gemini, OpenRouter, OpenAI, Anthropic and OpenAI-compatible providers appear in the registry. Paid providers require explicit confirmation but are excluded from automatic task routing. Only enable providers whose pricing and quota you understand. Gemini model availability and quotas depend on your account.

## Security and scope
API keys are encrypted with Windows DPAPI before storage in SQLite and can be decrypted only by the same Windows account on this computer. The browser never receives saved key values. Protect your Windows login and local data folder. This is a single-user local application without login. It does not execute generated code, modify project files, run arbitrary commands, install packages, deploy, or send communications. The approval list is empty because those actions are not implemented. The reviewer records completion but does not independently validate model output. Codex OAuth, Manus, and LangGraph are not implemented. The token meter records provider-reported per-call usage when returned, otherwise a text-length estimate. It shows the latest request’s context-window headroom and lifetime input/output totals per configured model. Set the context-window size in that provider’s configuration; defaults are a configurable estimate, not a model catalog guarantee. Provider account quotas or billing credits are not exposed by this meter.

Tasks/events persist in SQLite. Interrupted work can be retried after restart. Failover records provider errors and checkpoints. Only free providers are eligible; paid providers are never selected automatically.

## Troubleshooting
- Python not recognized: reinstall it with PATH enabled, then reopen the terminal.
- Browser does not connect: keep the server window open; check port 8765.
- Ollama fails: start Ollama and ensure the model is pulled.
- Task says waiting: connect an eligible free provider and retry.
- Rate limit or server error: check the activity feed. Only configured free models are tried.
