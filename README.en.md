<p align="center">
  <img src="docs/assets/readme/cover.svg" alt="PaperPilot: paper research and direction analysis" width="100%">
</p>

<p align="center">
  <a href="#download-and-start">Download and start</a> ·
  <a href="docs/INDEX.md">Documentation</a> ·
  <a href="docs/ARCHITECTURE.md">Architecture</a> ·
  <a href="README.md">简体中文</a>
</p>

PaperPilot is a desktop paper agent for investigating research directions. Given a question or implementation idea, it searches and reads papers, then chooses further lookup, comparison or delivery based on the evidence. It supports benchmark comparisons, literature reviews, fact checks and research-gap exploration. Papers, discussions and reports stay in a local workspace.

The current version is best suited to deep learning researchers. Fields share the same workflow and need no domain plugins; conclusion quality has not been evaluated separately across fields.

[![Start research, import papers and configure a model](docs/assets/readme/home.png)](docs/assets/readme/home.png)

Screenshots show the current local application with manually written example data. The process view replays saved events and does not demonstrate research quality. Click an image for the full-resolution view. The interface and technical guides are primarily in Chinese.

## Update draft

The `v0.2.0-preview.1` draft adds question-driven research actions, incremental library organization, local full-text hybrid retrieval and report-only revisions. It also improves session/PDF continuity and streaming in the conversation. See the [release draft](docs/releases/v0.2.0-preview.1.md). This draft version has not been published; Releases lists the available versions.

## Download and start

Packages are listed in [GitHub Releases](https://github.com/DirtyDidsDoneDerCheap2049/PaperPilot/releases). You can also [run from source](#from-source). The Windows package retains the filename `AIReader-windows-preview.zip` and launcher `AIReader.exe`; the application is named PaperPilot.

1. Extract the complete `AIReader` folder and run `AIReader.exe`.
2. Open **连接与设置** and enter an OpenAI Chat Completions-compatible endpoint, API key and model ID.
3. Enter a question and select **研究 Agent**. Import PDFs or select library papers to supply existing sources.

The desktop package needs Windows WebView2 Runtime. It does not require Python, MySQL, a backend server or a PaperPilot account. Without a model key, the interface and PDF import remain available.

Full-text access depends on permissions and source availability. Upload a PDF when retrieval fails, or continue with available material; abstract-only evidence is identified in the report. Your configured provider bills model usage. Start with a small paper set and set a provider-side spending limit.

## Research workflow

- The Agent plans for the question, then selects search, source lookup, local retrieval, reading, analysis or delivery. It can investigate a gap, compare values, review literature or check specified papers. A check confined to supplied papers does not expand into an open literature search. See [research scheduling](docs/ADAPTIVE_RESEARCH.md).
- Library organization generates categories, tags and summaries, with separate filters for temporal input, priors and training. Later runs reuse the taxonomy and unchanged results. Verified duplicates are folded without deleting files or historical references; organization can be undone. See [library organization](docs/LIBRARY_ORGANIZATION.md).
- Local lexical and vector retrieval returns full-text sections, tables and source locations. The Agent uses this tool too; selected papers remain in the reading scope even when absent from the top-ranked hits. See [full-text retrieval](docs/FULLTEXT_RETRIEVAL.md).
- Reading findings and report drafts stream directly into the conversation. Reasoning stays in a single-line preview outside the report. Saved events can be replayed after a refresh.
- Reports lead with the answer, followed by evidence and limitations. Gap reports identify candidate differences; numeric reports show verified values and comparison conditions. Limited search cannot establish global records or novelty.
- **讨论结果** answers from existing context, with excerpts from at most eight papers, without searching. Switching back to **研究 Agent** retains recent discussion and available attachments in the same session.
- A request to shorten, rewrite or restructure a report uses that report and its saved analysis to create a revision, without rereading papers. Originals are retained. Requests for new evidence still use the research workflow. Report writing follows the bundled Naturawrite rules.

<p align="center"><a href="docs/assets/readme/library.png"><img src="docs/assets/readme/library.png" alt="Library organization, category filters, full-text search and paper selection" width="100%"></a></p>

[![In-conversation research process and per-paper findings from saved events](docs/assets/readme/conversation.png)](docs/assets/readme/conversation.png)

[![Report contents and evidence records](docs/assets/readme/report.png)](docs/assets/readme/report.png)

Direct discussion replies are expanded by default. Older research reports open their saved versions. Draft messages are kept per session in the current window and discarded when the application closes.

Task records support inspection, cancellation and retry. Closing the application interrupts active work; a manual retry restarts the workflow and may incur additional charges. Queued tasks can continue on reopening.

Missing-text records offer original-source links and title searches. Adding a PDF does not rewrite an old report; run research again in the same session. Review exports contain Markdown, JSON and checksums for the selected session, report or papers. PDFs are not included automatically. Review private content before sharing.

## Model configuration

PaperPilot supports OpenAI Chat Completions-compatible services. Model IDs, output-length parameters, JSON mode and provider options are configurable. Automatic mode sends DeepSeek-specific thinking parameters only to its official endpoint. Responses and native Anthropic protocols are not supported; provider compatibility needs practical verification.

The official DeepSeek preset uses `deepseek-flash` and `max` thinking effort. The output budget is 65,536 tokens, including reasoning, with a 600-second timeout. Available IDs depend on the provider. Connection testing checks the model list without generating a response.

New workspaces allow up to 60 full texts, 200 model requests and 120 minutes per task. Existing settings are retained. Concurrency, input capacity and database settings are configurable; database settings are under advanced options. See [usage and caching](docs/MODEL_COST.md).

<p align="center"><a href="docs/assets/readme/settings.png"><img src="docs/assets/readme/settings.png" alt="Model connection and advanced settings" width="560"></a></p>

## Data and privacy

The default workspace is `%LOCALAPPDATA%/AIReader/workspace`. SQLite is the default; MySQL is optional and does not provide automatic cloud synchronization.

Local PDF extraction and full-text embeddings run on your computer without embedding API charges. Model analysis, organization, discussion and report revisions send relevant text to the configured provider. Optional MinerU parsing and external vector services send data to their configured endpoints.

Windows encrypts API keys with the current user's DPAPI. Reconfigure keys after moving to another account or computer. Close PaperPilot before backing up the complete workspace; MySQL also needs a database snapshot. See the [workspace guide](docs/WORKSPACE_GUIDE.md) and [MySQL notes](docs/MYSQL.md).

## Architecture

The desktop uses pywebview, local FastAPI, WebSocket and SSE interfaces. A C++17 kernel handles persistent tasks, idempotency, state transitions, attempt tokens and workspace locking. A Python worker runs the paper, model and evidence tools. JSON Lines connects TaskService to the kernel.

Full-text retrieval combines an ONNX multilingual embedding model, Chroma and SQLite FTS. One workspace allows one application instance and one research task at a time; independent paper operations within that task may run concurrently. See the [architecture](docs/ARCHITECTURE.md).

## From source

On Windows PowerShell with Python 3.12, run in the project directory:

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-lock-windows-py312.txt
.venv/Scripts/python.exe scripts/build_native.py
.venv/Scripts/python.exe -m src.app.desktop
```

The native build uses Zig and verifies pinned dependencies. `scripts/prepare_embedding_model.py` prepares the local model; the desktop build bundles it.

```powershell
.venv/Scripts/python.exe -m src.app.desktop --workspace D:/ReaderData/research
.venv/Scripts/python.exe -m pytest tests -q
node --test tests/test_conversation_ui.cjs tests/test_live_ui.cjs
.venv/Scripts/python.exe scripts/build_desktop.py
.venv/Scripts/python.exe scripts/prepare_release.py --check-only
```

The build outputs `dist/public-desktop/AIReader/`. See [directory conventions](docs/WORKSPACES.md) and [publishing](docs/PUBLISHING.md).

## Limitations

- Local extraction has no OCR. Scanned PDFs and complex tables may need another parser or manual verification.
- Independent downloads, model reading and audit batches default to concurrency 3, configurable from 1 to 6. Local PDF parsing remains sequential. Planning, aggregate analysis and reporting follow their dependencies; there is no parallel subagent scheduler.
- A full paper is read when it fits the input budget. Otherwise, relevant complete sections and tables are retrieved, with partial-reading and source-range metadata. Budgets are conservative estimates; receiving the text does not guarantee correct interpretation.
- Initial indexing takes time and memory. Cold retrieval loading can briefly block status endpoints. See the [resource measurements](docs/LOCAL_RETRIEVAL_BENCHMARK.md).
- Research records, report files and task state do not share a transaction. Inspect existing output after an interruption before retrying.
- There is no multi-user collaboration, automatic cross-device sync, cloud task execution or automatic updater. Clean-system Windows installation and code signing remain unverified.

## License

Source uses [AGPL-3.0-only](LICENSE). Dependency and model licenses are listed in [THIRD_PARTY.md](THIRD_PARTY.md). Binary distributions must include corresponding source and build instructions. Imported papers retain their original ownership and are not covered by the code license.
