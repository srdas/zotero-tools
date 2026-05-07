# Zotero Collection Analyzer

A terminal tool that reads your local Zotero library, lets you pick a collection, uses a local LLM (via [Ollama](https://ollama.com)) to cluster the papers into themes, and then streams a mini literature survey for whichever theme you choose.

No cloud API keys required. All inference runs locally.

---

## How it works

1. **Pick a collection** — the tool lists every collection in your Zotero library in a tree.
2. **Get categories** — the configured model reads each paper's title and abstract and groups them into 5–8 thematic clusters.
3. **Pick a category** — choose one of the clusters.
4. **Read the survey** — the model streams a multi paragraph academic mini-survey citing the papers by author and year, followed by the full paper list.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.8+ | No third-party packages needed |
| [Zotero](https://www.zotero.org) | Desktop app installed; database at `~/Zotero/zotero.sqlite` |
| [Ollama](https://ollama.com) | Running locally (`ollama serve`) with at least one model pulled |

---

## Setup

**1. Install Ollama and pull a model**

```bash
# Install from https://ollama.com, then:
ollama pull llama3.2
```

Any model available in Ollama works. Larger models (e.g. `llama3.1:70b`, `mistral`, `llama3.2`, `deepseek-r1`) produce better surveys but are slower. I find `gpt-oss` is slow but gives good results. 

**2. Make the script executable**

```bash
chmod a+x ~/zotero_survey.py
```

**3. Run it**

```bash
python3 ~/zotero_survey.py
```

On first run the config file is created automatically at `config.json` in the same directory as `zotero_survey.py`.

---

## Configuration

`config.json` (same directory as `zotero_survey.py`)

```json
{
  "ollama": {
    "host": "http://localhost:11434",
    "categorization_model": "llama3.2",
    "survey_model": "llama3.2"
  },
  "max_papers": 300
}
```

| Key | Description |
|---|---|
| `ollama.host` | Ollama server URL. Change if running on another machine or port. |
| `ollama.categorization_model` | Model used to cluster papers into themes. A fast, small model is fine here. |
| `ollama.survey_model` | Model used to write the survey. Use a stronger model for better prose. |
| `max_papers` | Maximum number of papers sent to the model per categorization call. Increase if your context window allows it; reduce if the model is slow or runs out of memory. |

You can use **different models for each task**, e.g. a small model for fast categorization and a larger one for the survey:

```json
{
  "ollama": {
    "host": "http://localhost:11434",
    "categorization_model": "llama3.2",
    "survey_model": "llama3.1:70b"
  },
  "max_papers": 300
}
```

To list models you have available locally:

```bash
ollama list
```

---

## Database access

The tool opens `~/Zotero/zotero.sqlite` in **read-only mode** — it never writes to or modifies your Zotero data. If the main database is locked (Zotero is open and has an exclusive write lock), it automatically falls back to `zotero.sqlite.bak`.

---

## Troubleshooting

**`Error: cannot reach Ollama at http://localhost:11434`**
Ollama is not running. Start it with:
```bash
ollama serve
```

**`No JSON found in model response`**
The model returned prose instead of the expected JSON. This can happen with very small models. Try a larger or instruction-tuned model in the config.

**`Zotero database not found at default location`**
If your Zotero data folder is not at the default `~/Zotero/`, the tool will prompt you to enter the correct path at startup. You can find the path in *Zotero → Settings → Advanced → Files and Folders*.

**Survey quality is poor**
Switch `survey_model` to a larger model. Models with at least 8B parameters tend to produce coherent academic prose; 70B models are noticeably better.
