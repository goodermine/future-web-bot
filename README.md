# future-web-bot

A modern take on Clif High's 1997 Web Bot: a predictive linguistics engine that
reads public online text, scores its emotional tension, and flags days where that
tension moves unusually far from its baseline. Where the original matched text
against a fixed 300,000-word lexicon, this version uses an LLM (Claude) and keeps
a small local scorer as a free pre-filter.

```
[sources] -> ingest.py -> processor.py -> database.py -> analytics.py -> alerts + chart
             spiders      quantifier      ledger          trend engine
```

## Modules

| File | Role | What it does |
|---|---|---|
| `ingest.py` | Module 1: spiders | Fetches URLs concurrently with `httpx` (RSS/Atom, Reddit JSON listings, HTML pages). Strips HTML noise and waits a minimum interval between requests to the same host. Retries 429/5xx responses with backoff. Outputs `[{timestamp, source_url, headline, raw_text}]`. |
| `processor.py` | Module 2: quantifier | Scores text on **intensity**, **immediacy** and **scale** (integers 1–100). The `lexical` backend (VADER plus cue lexicons) runs offline. The `claude` backend sends a JSON-schema-constrained request through the Anthropic SDK. |
| `database.py` | Module 3: ledger | SQLite `modelspace_ledger`. `calculated_tension` is a generated column (`intensity * immediacy`). Bulk inserts run in one transaction, and repeated content is skipped. |
| `analytics.py` | Module 4: trend engine | Uses pandas to build daily means with 7-day and 30-day rolling means and standard deviations. Flags a **tension spike** or **pressure valley** when a day is ≥2σ from its baseline. Draws a chart with matplotlib. |
| `pipeline.py` | Orchestrator | `run` does one full cycle, `analyze` runs analysis only, and `demo` builds a synthetic 90-day history. |

## Quick start

```bash
pip install -r requirements.txt

# See the whole thing work on synthetic data (no network, no API key)
python pipeline.py demo            # -> reports/demo_tension.png + alerts on stdout

# One real cycle against the sources in sources.txt
python pipeline.py run --sources sources.txt

# Use Claude for scoring (auto-selected when ANTHROPIC_API_KEY is set)
export ANTHROPIC_API_KEY=...
python pipeline.py run --sources sources.txt --backend claude --min-intensity 40
```

To build a history, run `pipeline.py run` on a schedule (for example hourly with cron).
The anomaly detector needs `MIN_BASELINE_DAYS` (7) days of data before it can flag anything.

Each module also runs on its own:

```bash
python ingest.py --sources sources.txt -o data/raw.json
python processor.py -i data/raw.json -o data/scored.json --backend lexical
python processor.py "Markets are collapsing right now worldwide"   # score one text
python database.py --load data/scored.json
python analytics.py --plot reports/tension.png --csv reports/daily.csv
```

## Windows: set up and run every hour

All of these are in the `windows` folder. Double-click them in File Explorer.

1. **Install Python 3.10 or newer** from python.org. On the first installer screen, tick
   **"Add python.exe to PATH"**.
2. **Double-click `setup.bat`** (once). It installs everything into a private `.venv` folder,
   runs the demo and opens the demo chart.
3. **Double-click `run_now.bat`** to do one real run and check that it works.
4. **Double-click `schedule_hourly.bat`** to have Windows run the bot every hour while you're
   logged in. A console window flashes briefly each time it runs.

After that:

- `show_results.bat` re-analyses everything collected so far and opens the chart.
- `remove_schedule.bat` stops the hourly runs. Your collected data is kept.
- Each run's output is added to `logs\webbot.log`.
- To use Claude for scoring, open Command Prompt and run
  `setx ANTHROPIC_API_KEY "your-key-here"` once. Scheduled runs started after that pick it up.

## Design notes

- **Token control (handoff §5.3).** Every packet gets the free lexical score first. Only packets whose
  lexical intensity is at least `--min-intensity` (default 40) go to Claude. If a Claude call fails
  (rate limit, network error, refusal), that packet keeps its lexical score and the batch continues.
  The `method` column records which scorer produced each row.
- **Context over matching (handoff §5.2).** The Claude system prompt defines each scale with anchors.
  It tells the model to discount sarcasm, hyperbole and trivial slang unless they signal a structural
  shift. It also tells the model to treat the scraped text as data and not follow instructions in it.
- **Model.** The default is `claude-opus-5-5` at `effort: low`. Override it with `WEBBOT_MODEL`.
  Requests opt into server-side refusal fallbacks (`fallbacks: "default"`).
- **Baseline without look-ahead.** Each day's z-score uses the mean and std of *earlier, non-flagged*
  days only. A day never counts toward its own baseline, and one spike can't raise the std enough to
  hide the next spike.
- **Scaling up.** The ledger schema is plain SQL, so moving to PostgreSQL/TimescaleDB mainly means
  changing `database.connect`.

## Tests

```bash
python -m pytest -q
```

The tests use mocked HTTP and a mocked Anthropic client, so they don't need network access or an API key.
