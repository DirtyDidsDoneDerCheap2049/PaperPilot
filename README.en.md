<p align="center">
  <img src="docs/assets/readme/cover.svg" alt="PaperPilot: an AI agent for investigating research gaps" width="100%">
</p>

<p align="center">
  <a href="#download-and-start">Download and start</a> ·
  <a href="docs/WORKSPACE_GUIDE.md">Workspace and backup</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="README.md">简体中文</a>
</p>

PaperPilot is a desktop agent for investigating research directions through papers. Start with a research question or implementation idea. It searches for relevant papers, attempts to retrieve their full text, and compares methods and evidence across papers to help you assess prior work and differences worth testing. Research records stay in a local workspace.

Given the author's research background and current validation scope, PaperPilot is currently better suited to deep learning researchers.

Investigating a direction often means reading dozens of papers. You can start with a question or import existing PDFs, then continue the same session as you add sources and hypotheses. The workspace keeps papers, evidence, reports and discussions. See the [project goals and unfinished work](docs/PRODUCT_PURPOSE.md).

Research directions use the same workflow, guided by your question and the papers, without domain plugins. This Windows preview has not established research accuracy across fields.

[![The home page: start research, import papers and configure a model](docs/assets/readme/home.png)](docs/assets/readme/home.png)

These screenshots were captured from the real application frontend on September 23, 2026, using an isolated workspace with manually written examples. Some labels and report layouts predate recent updates; they do not demonstrate model quality. Click a screenshot to open its original resolution. The interface and linked technical guides are primarily in Chinese.

## Download and start

The current Windows package is `AIReader-windows-preview.zip`. The executable and existing workspace paths still use AIReader. A public GitHub Release has not been published yet, so there is no public download URL in this document.

1. Extract the complete `AIReader` folder and run `AIReader.exe`.
2. Open **连接与设置** and provide an OpenAI Chat Completions-compatible endpoint, API key and model names.
3. Describe the mechanism, target module and question to investigate, then use **研究 Agent** to check existing implementations and possible gaps. Existing PDFs are optional seed material.

The desktop package does not require Python, MySQL or a remote server. Windows WebView2 Runtime is required. Without a model key you can still inspect the interface and import PDFs.

Model usage is billed by your provider. Start with a small paper set and configure a spending limit with that provider.

Other OpenAI Chat Completions-compatible providers can use a manually entered model ID, configurable output-token parameter, JSON output mode and provider options. Automatic mode sends DeepSeek-specific thinking parameters only to its official endpoint. A missing model-list endpoint does not prevent manual configuration. Responses and native Anthropic protocols are not supported, and not every vendor has been tested.

For the official DeepSeek endpoint, the application prefills `deepseek-flash` for both task roles with `max` thinking effort; available model IDs are determined by the provider. The effort, output budget and request timeout are configurable. A new DeepSeek workspace allows 65,536 output tokens including reasoning per call, with a 600-second request timeout. These are application settings. Connection testing checks the configured names against the service's model list without generating a response.

Paper access is conditional. The application attempts to retrieve full text, but access permissions and source restrictions may prevent a download. When full text is unavailable, add the PDF yourself or continue with an explicitly limited evidence set. An abstract is not a substitute for checking the full method section.

## What you can do

- Start from a concrete implementation idea and check what has already been done.
- Keep retrieved papers and manually imported PDFs in one local library.
- Compare source-backed coverage, counterevidence and candidate differences; retain missing evidence and proposed discriminating experiments.
- Search conversation history, copy or expand messages, and reopen an older report version.
- Inspect task status, cancel a running task, or retry an interrupted task after checking its existing output.
- Research runs inside the conversation. Reading findings stream as prose, completed steps can be collapsed, and reasoning stays in a single-line preview outside the report. Saved events restore after a refresh; missing events from older runs cannot be reconstructed. The agent performs bounded follow-up searches before writing and checking the report.
- New reports present conclusions, candidate research gaps, related work and limitations, in that order. Each candidate states the gap and its difference from prior work; weaker candidates are marked as unverified. Detailed processing and audit records are stored separately. Existing reports keep their original format.
- Filter missing full texts by the latest research turn or workspace, and export a review ZIP with Markdown, structured JSON and checksums. PDFs are not included automatically; review private content before sharing.

**研究 Agent** searches prior work and checks candidate gaps. **讨论结果** discusses existing results without running that pipeline and accepts excerpts from at most eight selected papers. Continuing research carries forward the direction, paper collection and candidate context. Reports retain processing failures and unresolved evidence. Reaching a paper count does not stop the remaining queries within the configured query budget.

Every direction uses an evidence index linking research claims to method relations and verified source IDs. Transfer questions distinguish source and target tasks. Distillation requires evidence for the teacher signal, student, imitation target and matching loss without assuming a specific application. Missing cells or unavailable papers do not establish novelty. The workflow performs bounded follow-up searches and reading before delivery, then states unresolved gaps when its limits are reached. Further research can continue in the same session.

<p align="center"><a href="docs/assets/readme/library.png"><img src="docs/assets/readme/library.png" alt="The actual library displaying three synthetic paper records" width="820"></a></p>

Reports help you locate material to read. They do not replace the source paper or prove novelty automatically.

[![The actual conversation interface with a synthetic example](docs/assets/readme/conversation.png)](docs/assets/readme/conversation.png)

[![The actual report reader with a manually written example](docs/assets/readme/report.png)](docs/assets/readme/report.png)

## Data and privacy

The default workspace is `%LOCALAPPDATA%/AIReader/workspace` and uses SQLite. The database, PDFs, reports and indexes stay in the workspace. Model requests send relevant paper text and conversation content to the provider you configure. Optional MinerU parsing and external vector search send data to their configured services.

Windows stores API keys encrypted with the current user's DPAPI. Reconfigure keys after moving to another Windows account or computer. Close PaperPilot before copying a complete workspace for backup. See the [workspace guide](docs/WORKSPACE_GUIDE.md) and [MySQL notes](docs/MYSQL.md).

<p align="center"><a href="docs/assets/readme/settings.png"><img src="docs/assets/readme/settings.png" alt="The actual settings dialog with an empty API key field" width="560"></a></p>

## Engineering

PaperPilot uses pywebview with a local FastAPI and WebSocket layer. A C++17 task kernel owns durable task state, idempotency checks, state transitions and workspace locking. A Python worker keeps the paper parsing, retrieval, model and evidence toolchain. JSON Lines connects the Python task service to the native kernel. SQLite is the default store; MySQL is optional for existing or explicitly configured workspaces.

See the [architecture](docs/ARCHITECTURE.md), [publishing guide](docs/PUBLISHING.md) and [workspace guide](docs/WORKSPACE_GUIDE.md) for code entry points, release checks and recovery boundaries.

## From source

On Windows PowerShell with Python 3.12:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock-windows-py312.txt
.venv/Scripts/python.exe scripts/build_native.py
.venv/Scripts/python.exe -m src.app.desktop
```

Run the offline checks with `pytest` and `node --test tests/test_conversation_ui.cjs`. Build the desktop folder with `scripts/build_desktop.py`. Exporting public source and scanning for credentials is documented in [PUBLISHING.md](docs/PUBLISHING.md).

## Scope and limitations

- All directions share the claim-and-evidence workflow, with no built-in domain-specific search or matrix branches. See [the general workflow](docs/DOMAIN_PROFILES.md). Workflow compatibility does not establish scientific accuracy or novelty guarantees.
- Local PDF extraction has no OCR; scanned or complex PDFs may need an external parser.
- Full-text retrieval depends on source access and permissions.
- Full-text processing supports a configurable limit of 0–100 papers. New workspaces default to 50 papers, 200 model requests and 120 minutes. Existing explicit settings are preserved. These are execution limits, not measured capacity or cost guarantees.
- Paper processing is sequential, followed by evidence-audit batches of up to eight papers. This is a staged agent workflow, not parallel autonomous subagent scheduling.
- Extraction currently reads at most 60,000 characters per paper. Follow-up research has round and request limits; hierarchical synthesis and on-demand source rereading are not complete.
- Offline regression covers direction-specific evidence, invalid citations, search budgets and historical candidate recovery, alongside a 50-PDF synthetic workflow. Research quality on real papers still requires online evaluation and human checking.
- A successful task does not prove that a research conclusion is correct. Citation matching confirms a source link, not the model's interpretation or novelty judgment.
- Shared writing guidance asks the model to answer directly, explain its evidence and preserve uncertainty. This may reduce boilerplate; it does not guarantee compliance or research accuracy. See the [validation notes](docs/MODEL_OUTPUT.md).
- There is no multi-user collaboration, automatic cross-device sync, cloud task execution or automatic updater.
- Clean Windows installation, code signing and long-running production operation have not been completed.

## License

Source code is licensed under [AGPL-3.0-only](LICENSE). Third-party dependencies are listed in [THIRD_PARTY.md](THIRD_PARTY.md). Screenshots use synthetic data and contain no private workspace records or credentials. See the [image notes](docs/assets/readme/README.md) for capture details. Papers and third-party materials retain their original ownership.
