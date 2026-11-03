# RISSK — Getting Started

RISSK uses machine learning to score interviews from Survey Solutions export files,
flagging individual interviews most likely to contain unwanted interviewer behaviour.

---

## Prerequisites

- **Python 3.13** installed on your machine
- An internet connection for the initial install
- Survey Solutions export files (Main Survey Data + Paradata ZIPs)

Verify your Python version:

```bash
python --version
```

---

## Option A — uv (recommended for new users)

[uv](https://docs.astral.sh/uv/) is a fast, self-contained Python package manager.
You do **not** need to manage virtual environments manually.

### 1. Install uv

**macOS / Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Get the RISSK code

Clone with Git:
```bash
git clone https://github.com/rowsquared/rissk.git
cd rissk
```

Or download the ZIP from GitHub, unzip it, and navigate to the `rissk/` folder.

### 3. Install dependencies

```bash
uv sync                 # core install  (add --extra gui for the GUI, --extra viz for the score notebooks)
```

That's it — RISSK is installed. Continue to **[Run RISSK](#run-rissk)**.

---

## Option B — conda (for experienced users)

### 1. Get the code

```bash
git clone https://github.com/rowsquared/rissk.git
cd rissk
```

### 2. Create and activate the conda environment

```bash
conda env create -f environment.yml
conda activate rissk_kedro
```

This installs Python 3.13, all pipeline dependencies, and the RISSK package in one step.

Continue to **[Run RISSK](#run-rissk)**.

---

## Run RISSK

A run is described by a **Kedro config environment** — a single file `conf/<config>/globals.yml`
(survey, questionnaire name/versions, and the three storage roots). Copy an example, edit it, and run:

```bash
cp -r conf/grdslchbs_test conf/my_survey     # then edit conf/my_survey/globals.yml
kedro run --env my_survey                    # full pipeline
# …or a single stage:
kedro run --env my_survey --pipeline data_ingestion
kedro run --env my_survey --pipeline feature_creation
kedro run --env my_survey --pipeline rissk_scoring
```

Results land in `<output_root>/<survey>/latest/35_SCORES/unit_rissk_scores.csv`. For the full
walkthrough — every config field, the **local / s3in / s3out / s3** storage modes, and handling
several surveys — see **[README → Configure a run](README.md#3-configure-a-run)**.

---

## Optional: GUI

A local [NiceGUI](https://nicegui.io) app provides a point-and-click alternative for a single local run.

> **Note:** the GUI predates the config-env model (it writes the older `data_root` / `conf/local` schema) and has **not** yet been migrated, so it may not run against the current pipeline. `kedro run --env <config>` (above) is the supported path; the steps below describe the intended GUI workflow and will return when the GUI is reworked.

Install + launch it:

```bash
uv sync --extra gui
bash run_gui.sh        # macOS / Linux   (run_gui.bat on Windows)  →  http://localhost:8080
```

### Step 1 — Data folder

Choose where RISSK will read and write survey data.

- **Default (`data`):** keeps everything in the project folder (the repo root).
- **Absolute path:** point to any folder on your machine, e.g. `/Users/jane/surveys`.

The GUI shows you the exact subfolder where ZIP files must be placed, e.g.:

```
/Users/jane/surveys/pmpmd_household/latest/10_RAW/
```

Click **Create folder & Open** to create that folder and open it in your file manager.

### Step 2 — Prepare your Survey Solutions exports

Export from Survey Solutions and place the **unmodified ZIP files** in the folder shown:

1. **Main Survey Data** — choose *Tab separated* or *Stata 14*, tick *Include meta information about questionnaire*.
2. **Paradata** — under *Data Type* select *Paradata*.

> Export both files from the **same questionnaire version** consecutively.
> For multiple compatible versions, export each separately and place all ZIPs in the same folder.

Do **not** rename, modify, or unzip the files.

### Step 3 — Questionnaire configuration

- **Questionnaire name:** the template name exactly as it appears in Survey Solutions (e.g. `pmpmd_household`). This is also used as the data folder name.
- **Versions:** comma-separated list of version numbers to process, e.g. `4, 5, 6`.
- **Consent filter (optional):** score only interviews where a specific paradata variable equals a required value (useful for surveys with a consent question).

To switch to a different questionnaire, update the name in the Setup tab and save — or edit `questionnaire.name` directly in your `conf/<config>/globals.yml`.

### Step 4 — Save & Run

1. Click **Save configuration** on the Setup tab.
2. Switch to the **Run** tab.
3. Choose a pipeline stage (leave as *All* for a full run).
4. Click **Run RISSK** and monitor the live log.

Results are written to:
```
<data_root>/<questionnaire_name>/latest/35_SCORES/unit_rissk_scores.csv
```

### Advanced settings

Access the **Advanced** tab to:
- Set a ZIP password (if your exports are password-protected)
- Toggle automatic contamination estimation
- Enable/disable individual features and adjust contamination thresholds

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `ModuleNotFoundError: nicegui` | Run `pip install "nicegui>=1.4"` in your active environment |
| Browser does not open | Open http://localhost:8080 manually |
| Pipeline fails with "No data found" | Check that ZIP files are in the correct subfolder (see Setup tab) |
| `kedro: command not found` | Activate your environment first (`conda activate rissk_kedro` or `source .venv/bin/activate`) |
| ZIPs not extracted | Make sure filenames are not modified; check the ZIP password setting if exports are protected |
