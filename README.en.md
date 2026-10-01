<p align="center">
  <img src="docs/assets/readme/cover.svg" alt="PaperPilot: an AI agent for investigating research gaps" width="100%">
</p>

<p align="center">
  <a href="#download-and-start">Download and start</a> ·
  <a href="docs/WORKSPACE_GUIDE.md">Workspace and backup</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="README.md">简体中文</a>
</p>

PaperPilot is a desktop agent for exploring research directions through paper search and analysis. Given a research question or implementation idea, it searches for relevant papers, attempts to retrieve their full text, and compares methods and evidence to identify prior work and differences worth testing. Research records stay in a local workspace.

The current version is best suited to deep learning researchers.

Start with a question or import existing PDFs, then continue the same session as you add sources and ideas. The workspace keeps papers, evidence, reports and discussions. See the [project goals](docs/PRODUCT_PURPOSE.md).

Research directions share the same workflow and require no domain plugins.

[![The home page: start research, import papers and configure a model](docs/assets/readme/home.png)](docs/assets/readme/home.png)

Screenshots use manually written examples. The interface and linked technical guides are primarily in Chinese.

## Download and start

You can [run PaperPilot from source](#from-source). The Windows preview package is named `AIReader-windows-preview.zip`, with `AIReader.exe` as its launcher; a public download is not yet available.

1. Extract the complete `AIReader` folder and run `AIReader.exe`.
2. Open **连接与设置** and provide an OpenAI Chat Completions-compatible endpoint, API key and model names.
3. Enter a research question or implementation idea, then use **研究 Agent** to search prior work and investigate possible gaps. You can select or import papers as additional material.

The desktop package does not require Python, MySQL or a remote server. Windows WebView2 Runtime is required. Without a model key you can still inspect the interface and import PDFs.

Model usage is billed by your provider. Start with a small paper set and configure a spending limit with that provider.

Full-text downloads depend on source access and permissions. If a paper is unavailable, add the PDF yourself or continue with the available material; abstract-only analysis has a narrower scope.

## Research workflow

- Start from a concrete implementation idea and check what has already been done.
- Keep retrieved papers and manually imported PDFs in one local library.
- Compare methods, evidence and candidate differences, with references to the source text.
- Search conversation history, copy or expand messages, and reopen an older report version.
- Inspect task status, cancel a running task, or retry an interrupted task after checking its existing output.
- Follow research progress in the conversation. Reading findings appear as they are produced, completed steps can be collapsed, and reasoning stays in a single-line preview outside the report. Saved progress returns after a refresh.
- Read reports organized by conclusions, candidate research gaps, related work and the scope of the analysis. Each candidate states its difference from prior work; candidates with insufficient evidence are marked as unverified. Processing records are stored separately.
- Filter missing full texts by the latest research turn or workspace, and export a review ZIP with Markdown, structured JSON and checksums. PDFs are not included automatically; review private content before sharing.

**研究 Agent** adds sources and investigates candidate gaps or related directions. **讨论结果** answers from existing context, with excerpts from at most eight selected papers, without searching again or updating research conclusions. The same session retains its direction, papers and candidate context; start a new session for an independent direction.

An evidence index connects research claims, method relationships and source references. Before generating a report, the agent searches and reads additional material relevant to candidate differences. When a query, paper or model-request limit is reached, the report lists the remaining evidence gaps. You can continue the research in the same session.

<p align="center"><a href="docs/assets/readme/library.png"><img src="docs/assets/readme/library.png" alt="Example paper records and full-text status in the library" width="820"></a></p>

Reports help identify material to read and ideas to test. Novelty still needs to be assessed against the source papers and experiments.

[![Conversation history and research progress](docs/assets/readme/conversation.png)](docs/assets/readme/conversation.png)

[![Report contents and evidence records](docs/assets/readme/report.png)](docs/assets/readme/report.png)

Closing the application interrupts an active task. You can retry it after reopening, which may incur additional model charges; queued tasks continue to run. Draft messages stay in the current window when switching sessions and are discarded when the application closes.

## Model configuration

Enter the endpoint, API key and model ID in settings. Routine and analysis tasks can use the same model, with separate thinking effort, output budgets and request timeouts.

PaperPilot supports OpenAI Chat Completions-compatible services. You can configure the output-token parameter, JSON output mode and provider options manually. Automatic mode sends DeepSeek-specific thinking parameters only to its official endpoint. Manual configuration remains available when a service has no model-list endpoint. Responses and native Anthropic protocols are not supported.

For the official DeepSeek endpoint, the application prefills `deepseek-flash` and `max` thinking effort. Available model IDs depend on the provider. The default output budget is 65,536 tokens, including reasoning, with a 600-second request timeout. Connection testing checks the configured model against the service's model list without generating a response.

New workspaces allow up to 50 full texts, 200 model requests and 120 minutes per task. Existing workspaces retain their paper-limit setting. PDF parsing and vector search are optional; database settings are under the advanced options.

## Data and privacy

The default workspace is `%LOCALAPPDATA%/AIReader/workspace` and uses SQLite. The database, PDFs, reports and indexes stay in the workspace. Model requests send relevant paper text and conversation content to the provider you configure. Optional MinerU parsing and external vector search send data to their configured services.

Windows stores API keys encrypted with the current user's DPAPI. Reconfigure keys after moving to another Windows account or computer. Close PaperPilot before copying a complete workspace for backup. See the [workspace guide](docs/WORKSPACE_GUIDE.md) and [MySQL notes](docs/MYSQL.md).

<p align="center"><a href="docs/assets/readme/settings.png"><img src="docs/assets/readme/settings.png" alt="Model connection settings and advanced options" width="560"></a></p>

## Architecture

PaperPilot uses pywebview with a local FastAPI and WebSocket layer. A C++17 task kernel owns durable task state, idempotency checks, state transitions and workspace locking. A Python worker keeps the paper parsing, retrieval, model and evidence toolchain. JSON Lines connects the Python task service to the native kernel. SQLite is the default store; MySQL is optional for existing or explicitly configured workspaces.

Each workspace allows one PaperPilot instance and one research task at a time. Interrupted tasks require a manual retry, which restarts the workflow and can reuse existing paper profiles.

See the [architecture](docs/ARCHITECTURE.md) for request handling, evidence checks and failure recovery.

## From source

On Windows PowerShell with Python 3.12:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock-windows-py312.txt
.venv/Scripts/python.exe scripts/build_native.py
.venv/Scripts/python.exe -m src.app.desktop
```

Run the offline checks with `pytest` and `node --test tests/test_conversation_ui.cjs`. Build the desktop folder with `scripts/build_desktop.py`. Source export and release instructions are in [PUBLISHING.md](docs/PUBLISHING.md).

## Scope and limitations

- Research directions share the [claim-and-evidence workflow](docs/DOMAIN_PROFILES.md). Conclusion quality has not been validated separately across fields.
- Local PDF extraction has no OCR; scanned or complex PDFs may need an external parser.
- Full-text retrieval depends on source access and permissions.
- Full-text processing has a configurable limit of 0–100 papers. Papers beyond the limit remain pending. External parsing and vector services may charge separately.
- Papers are processed sequentially, with evidence checks in batches of up to eight. Parallel subagent scheduling is not supported.
- Extraction reads at most the first 60,000 characters of each paper. Long-document segmentation and on-demand rereading are not implemented. Follow-up research has round and request limits.
- The desktop package does not include Chroma. External vector search requires optional dependencies installed from source; changing models requires rebuilding the corresponding index.
- Database records and report files are not committed in a shared transaction. An interruption can leave partial results; check existing reports and messages before retrying.
- There is no multi-user collaboration, automatic cross-device sync, cloud task execution or automatic updater.
- The Windows preview package has not completed clean-system installation testing or code signing.

## License

Source code is licensed under [AGPL-3.0-only](LICENSE). Third-party dependencies are listed in [THIRD_PARTY.md](THIRD_PARTY.md). Binary distributions must include corresponding source and build instructions. Imported papers and other third-party materials retain their original ownership and are not covered by the project's code license. See the [image notes](docs/assets/readme/README.md) for image sources.
