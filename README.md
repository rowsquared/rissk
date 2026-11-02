![RISSK Logo](images/rissk.png)

# What is RISSK?

RISSK utilizes machine learning algorithms to generate a **Unit Risk Score (URS)** from your **[Survey Solutions](https://mysurvey.solutions/en/)** export files. This score indicates the likelihood of unwanted interviewer behaviour in individual interviews. It is a valuable tool to prioritize suspicious interviews for verification exercises such as back-checking or audio audits. Designed to be generic, RISSK can be easily integrated into the monitoring systems of most CAPI or CATI surveys conducted using Survey Solutions. Setting up and running RISSK on your local machine is straightforward and platform-independent. Running locally, it ensures the privacy and security of your survey data. Explore further details in the chapters below.

- [Getting started](#getting-started)
- [Advanced use](#advanced-use)
- [Interpretation](#interpretation)
- [Survey integration](#survey-integration)
- [Limitations](#limitations)
- [Confirmation of results](#confirmation-of-results)
- [Process description](#process-description)
- [Roadmap](#roadmap)

# Getting started

RISSK runs as a **Kedro pipeline driven by a small config file** — you describe your survey in `conf/<config>/globals.yml` and run `kedro run --env <config>`. A point-and-click **GUI is optional** (see [Optional: GUI](#optional-gui)). For full installation details see [SETUP.md](SETUP.md).

## Prerequisites

- **Python 3.13** installed on your machine
- An internet connection for the initial install
- Survey Solutions export files (Main Survey Data + Paradata ZIPs)

Verify your Python version:

```bash
python --version
```

## 1. Install

[uv](https://docs.astral.sh/uv/) is recommended (a fast, self-contained package manager — no manual virtualenvs):

```bash
git clone https://github.com/rowsquared/rissk.git
cd rissk
uv sync                 # core install  (add --extra viz for the score notebooks, --extra gui for the GUI)
```

<details><summary>conda alternative</summary>

```bash
git clone https://github.com/rowsquared/rissk.git
cd rissk
conda env create -f environment.yml
conda activate rissk_kedro
```
</details>

## 2. Export your data from Survey Solutions

Export **both** files for each questionnaire version:

- **Main Survey Data** — choose *Tab separated* or *Stata 14*, tick *Include meta information about questionnaire*. <details><summary>Screenshot of the export options for Main Survey Data.</summary>![Export options Main Survey Data](images/export_main.png)</details>
- **Paradata** — under *Data Type* select *Paradata*. <details><summary>Screenshot of the export options for Paradata.</summary>![Export options Paradata](images/export_para.png)</details>

**Do not rename, modify, or unzip the files** — RISSK reads Survey Solutions' original filename (`<questionnaire>_<version>_<format>_<status>.zip`) to find each export.

Put all the ZIPs for one **survey** together in a single `10_RAW` folder:

```
<input_root>/<survey>/latest/10_RAW/
    slchbs_grenada_2627_10_STATA_All.zip       # Main Survey Data (Stata or Tab-separated)
    slchbs_grenada_2627_10_Paradata_All.zip    # Paradata
    slchbs_grenada_2627_11_STATA_All.zip       # another version of the same questionnaire
    ...
```

(All questionnaires of a survey share this one folder; they are told apart by the filename prefix.)

## 3. Configure a run

A **run is a committed Kedro config environment** — a single file `conf/<config>/globals.yml`. Copy one of the bundled examples and edit it for your survey:

```bash
cp -r conf/grdslchbs_test conf/my_survey      # local example  (or copy conf/s3in / conf/s3out / conf/s3)
```

```yaml
# conf/my_survey/globals.yml
input_root:  "data"            # where the export ZIPs live  (local path, or s3://<bucket>)
work_root:   "data"            # ALWAYS-local staging — zips are fetched + unzipped here
output_root: "data"            # where results are written   (local path, or s3://<bucket>)

survey: my_survey              # folder under the roots →  <root>/my_survey/latest/...
questionnaire:
  name: slchbs_grenada_2627    # questionnaire template name = the <name>_*.zip filename prefix
  VERSION: [10, 11, 12, 13]    # versions to process;  [] = all versions found in 10_RAW
  filter_var: null             # optional consent filter, e.g. {consent_q: "1"} (score only consenting interviews)
```

**The storage mode is just the root *values*** (a local path vs `s3://<bucket>`). Four ready-made envs are committed:

| Env | `input_root` | `work_root` | `output_root` | Use when |
|---|---|---|---|---|
| `grdslchbs_test` | local | local | local | everything on disk |
| `s3in`  | `s3://…` | local | local | ZIPs live in S3, write results locally |
| `s3out` | local | local | `s3://…` | ZIPs are local, publish results to S3 |
| `s3`    | `s3://…` | local | `s3://…` | read **and** write S3 |

- `work_root` is **always local** — the unzip step can't run on S3.
- Any `s3://` root needs `s3fs` (installed by default) **and** AWS credentials in the environment (standard chain: env vars or `~/.aws`).

**One survey per config.** A survey folder holds a single questionnaire's results, so to process several questionnaires (or surveys) you make several configs — `conf/survey_a/`, `conf/survey_b/`, … — each with its own `survey` value and ZIP folder, and run each with its own `--env`.

## 4. Run RISSK

From the repo root:

```bash
kedro run --env my_survey                              # full pipeline
kedro run --env my_survey --pipeline data_ingestion    # …or a single stage:
kedro run --env my_survey --pipeline feature_creation  #   data_ingestion → feature_creation → rissk_scoring
kedro run --env my_survey --pipeline rissk_scoring
```

Results land under `output_root`:

```
<output_root>/<survey>/latest/35_SCORES/
    unit_rissk_scores.csv     ← the Unit Risk Score (0–100) per interview  (the main output)
    item_scores.parquet       responsible_scores.csv
```

**Scheduling (Windmill).** Scheduled runs are Windmill jobs: see [windmill/README.md](windmill/README.md). Survey configuration and credentials live in Windmill resources, not in committed envs. [rissk_readme.ipynb](rissk_readme.ipynb) remains a local, in-process runner: set `ENV` (a config name, or a list of them) and `PIPELINE`, and it runs each via `KedroSession`.

## Visualising the scores

Three interactive [marimo](https://marimo.io) notebooks in [notebooks/viz/](notebooks/viz/) explore a scored run — `feature_scores.py` (per-feature distributions), `unit_scores.py` (the 0–100 unit risk score across interviews) and `interview_scores.py` (single-interview drill-down). Install the extra and launch one in the browser:

```bash
uv sync --extra viz
uv run marimo edit notebooks/viz/unit_scores.py
```

Each notebook starts with a questionnaire dropdown that scans the data root, so it picks up whichever runs you have locally. See [notebooks/viz/README.md](notebooks/viz/README.md) for details.

## Optional: GUI

A local [NiceGUI](https://nicegui.io) app offers a point-and-click alternative for a single local run:

```bash
uv sync --extra gui
bash run_gui.sh        # macOS / Linux   (run_gui.bat on Windows)  →  http://localhost:8080
```

> **Note:** the GUI predates the config-env model (it writes the older `data_root` schema) and has **not** yet been migrated, so it may not run against the current pipeline. The `kedro run --env <config>` flow above is the supported path; the GUI will be reworked.

# Advanced use

## Feature scores

Every run writes both the per-interview `unit_rissk_scores.csv` (the `unit_risk_score`) **and** `item_scores.parquet` — the detailed per-feature scores for each interview — to `35_SCORES/`. For guidance on how to interpret each feature score, refer to [FEATURES_SCORES.md](FEATURES_SCORES.md).

## Excluding features

By default, RISSK includes all available features when calculating the Unit Risk Score (URS). To exclude a feature, set its `use: false` in `conf/base/parameters.yml` (the shared `features:` map, applied to every env):

```yaml
features:
  answer_changed:
    use: false
```

(The GUI **Advanced** tab offers the same toggles for a local run.)

## Adjusting contamination level

Default contamination values have been set based on our testing data. To override them, set a per-feature `contamination` in `conf/base/parameters.yml`:

```yaml
features:
  answer_changed:
    use: true
    parameters:
      contamination: 0.12
```

(Or adjust the thresholds in the GUI **Advanced** tab for a local run.)

## Automatically determining contamination level

The `medfilt` thresholding method can automatically determine contamination levels for each algorithm. This increases memory use and runtime but improved RISSK's effectiveness in our [experiment](#confirmation-of-results).

Set it in `conf/base/parameters.yml` (or via the GUI **Advanced** tab, *Automatic contamination*):

```yaml
processing:
  automatic_contamination: true
```

# Interpretation

RISSK generates `unit_rissk_scores.csv`, which contains the following variables for each interview: `interview__id`, `responsible` and `unit_risk_score`.

The `unit_risk_score` ranges from 0 to 100. A higher score suggests that the interview exhibits more anomalies and is therefore at a greater risk of containing problematic interviewer behavior. Interviews with elevated URS should be prioritized for verification and review.

To identify these anomalies, RISSK analyzes the following features:
- Interview timing (hour of the day)
- Duration of the interview and individual questions
- Geographical location (if GPS questions are set)
- Sequence of questions asked
- Modifications to question answers
- Pauses during the interview
- Statistical properties of answers (variance, entropy, etc.)

For more information on how URS is calculated, refer to chapter [Process description](#process-description). For a detailed breakdown of all features and scores, consult [FEATURES_SCORES.md](FEATURES_SCORES.md).

> [!WARNING]
> The URS is **not** definitive proof of interviewer misconduct. It may include **false positives**, where legitimate interviews receive high scores due to unusual circumstances, and **false negatives**, where problematic interviews receive low scores because they contain few or no detectable anomalies. To conclusively identify interviewer misconduct, further verification and review is required. See [Survey integration](#survey-integration) for more details.

The URS is a _relative_ measure, influenced by the data patterns in the set of interviews within the Survey Solutions export files. Therefore, scores will change when RISSK is run again with different data. When comparing URS between interviews, ensure that the scores were generated using the same set of export files. Direct comparison of URS values between different surveys is not advised.

By design, RISSK considers only the interview data up to the first interaction by a Supervisor or HQ role to eliminate the influence of confounding post-interview actions. This means that if substantial parts of an interview are completed after this point, the URS may not accurately reflect the interview's risk level. Consequently, modifying an interview after rejection will not improve its URS.

Note that RISSK does not currently take into account outstanding error messages or interviewer comments. These elements are easily accessible in the Survey Solutions interface and should ideally be reviewed systematically.

# Survey integration

RISSK serves as a useful tool to prioritize at-risk interviews in your quality assurance processes, such as in-depth reviews, back-checks, or audio auditing. Additionally, the Unit Risk Score (URS) can be monitored by interviewer and over time to identify trends. This chapter outlines general guidance on how to integrate RISSK into your survey. For advice specific to your context, please [list an issue](https://github.com/rowsquared/rissk/issues/new/choose) (use label 'questions') or reach out to the authors.

> [!WARNING]
> While RISSK enhances the data quality assurance system of a survey, it should not replace other essential components such as back-checks or audio audits.

**Frequency**

Ideally, RISSK should be executed—and its results reviewed and acted upon—regularly during fieldwork to detect and address issues promptly. For most surveys, a frequency ranging from **daily** to **weekly** is advisable. This usually means that RISSK's output will need to be processed and reviewed in batches.

**System integration**

If possible, integrate RISSK into your survey's data management and monitoring system. This allows for automated execution and the consolidation of various monitoring indicators. For example:
- Run RISSK as part of the scripts that export from Survey Solutions.
- Use the output to identify interviews for review/verification and add them to the backlog for supervisors or data monitors.
- Incorporate URS values into your monitoring dashboard alongside other indicators.

## Interview prioritization

The URS is specifically designed to guide the **initial** review or verification of an interview. It only takes into account data collected before any interactions by a Supervisor or HQ role. Any actions taken by the interviewer after the first review or rejection should be monitored through other means.

For each batch of interviews, it's most efficient to prioritize those with the **highest URS values** for review or verification, as they are most likely to contain issues. In our [experiment](#confirmation-of-results), 82% of interviews that fell within the top 5% of URS values were fabricated. However, because the URS can also include false negatives it may be beneficial to include some interviews with lower URS values in your review process. For instance, one approach could be to review or verify 10% of interviews with the highest URS in each batch, along with an additional random 5% drawn from the remaining pool. This could also be tailored to focus on specific interviewers or other criteria.

## Review/Verification

The process of reviewing or verifying interviews can involve various activities:

- External verification through back-check interviews or audio audits.
- In-depth examination of the interview and its paradata.
- Direct queries or confrontations with interviewers.
- Direct observation of future interviews.

It is advisable to keep a structured record of the outcome of the review/verification. Specifically, document for each interview whether it was found to contain problematic behaviour and, if so, describe the nature of the identified issues. This information can help you to finetune the composition of the URS (see chapter [Advanced use](#advanced-use) for details). The authors would also appreciate receiving these outcomes, together with the output file, to continue improving RISSK.

If problematic interviewer misbehaviour is confirmed, timely and appropriate consequences should ensue. These can range from a stern warning (akin to a "yellow card") to the loss of a bonus or even dismissal in cases of intentional misconduct such as data fabrication. For unintentional mistakes or if an interviewer is struggling, tailored feedback, explanatory phone calls, or in-person retraining may be necessary. Failure to address issues promptly can lead to persistent problems during fieldwork, negatively impacting data quality.

## Feedback to interviewers

Informing interviewers that their activities are closely monitored and that an algorithm is used to flag suspicious cases typically offers two benefits:

1. It encourages better performance, as people generally perform better when they understand the significance of their work.
2. It acts as a deterrent against misconduct, as there is a real risk of detection and subsequent consequences.

However, it's crucial to exercise caution in your communication with interviewers. Specifically, **do NOT** reveal details about how RISSK operates, such as the specific features analyzed or the scores influencing the Unit Risk Score (URS). Doing so could allow interviewers to adjust their behavior to evade detection.

For instance, providing feedback like, _"Your interview was flagged because it took place at night"_, could lead interviewers to falsify data during regular working hours or manipulate device time settings. Instead, opt for a generic initial inquiry, asking for details about the flagged interview and then cross-referencing this information with paradata and additional input from respondents or supervisors. For example, you might say, _"Your interview has been flagged and is currently under investigation. Could you please provide all the details about when it was conducted, with whom, how many visits were needed, any challenging aspects, pauses, and so forth?"_

Additionally, aim to provide feedback that is both **useful and actionable**. Generalized statements like, _"Your interview scored high; stop doing whatever you're doing wrong,"_ are not helpful. Instead, try to identify the underlying issues through verification or review and tailor your feedback accordingly. For instance, you could say, _"We've noticed that your interviews are unusually short and involve fewer households engaged in agriculture. If the respondent says 'No' in Q1, make sure to probe for XYZ. If the respondent mentions ABC, it should also be considered as a 'Yes' in Q1."_

## Monitoring

To use the URS as a monitoring indicator, average `unit_risk_score` by interviewer (and/or team) and over time (week/month), and visualize it e.g. as part of a survey monitoring dashboard. While individual interviews by one interviewer may not score high enough to be reviewed/verified, a repeated high average score over time for one interviewer may signal potential issues and the need to take action. Monitoring the average URS by interviewer and time also helps to check if interviewers have adjusted to feedback or warnings (lower URS post-intervention) or continue to produce problematic interviews (equal or higher URS).

> [!IMPORTANT]
> If your survey deploys multiple questionnaire templates, run RISSK separately for each one.

# Limitations

- **Majority behavior assumption**: RISSK assumes that the majority of interviews are conducted as desired, using this as a baseline for normal behavior. If a survey has an extreme level of problematic interviewer behavior, the scores may become unreliable.

- **Low number of interviews**: Some scores require a minimum number of observations to be calculated. Anomaly detection functions better with more observations. While there are only few interviews, e.g., during the first few days of fieldwork, the scores are less effective and reliable.

- **Interviewing events only**: RISSK considers only the interview data up to the first interaction by a Supervisor or HQ role. This means that if substantial parts of an interview are completed after this point, the URS may not accurately reflect the interview's risk level. If you're using [partial synchronization](https://docs.mysurvey.solutions/headquarters/config/admin-settings/) it's recommended that Supervisor and HQ roles refrain from opening interview files before they are completed to maintain reliability.

- **System Requirements**: RISSK has been tested on a laptop with 16GB of RAM, processing paradata files up to 1GB in size. Larger datasets may require more advanced hardware.

- **Data Format**: As of now, RISSK doesn't support SPSS format for microdata exports from Survey Solutions. Use STATA or TAB formats instead.

- **Version Compatibility**: RISSK is designed to support export formats from Survey Solutions version 23.06 and later. While it may be possible to use RISSK with earlier versions, such compatibility has not been officially tested.

- **Non-Contact and non-response**: Interviews containing non-contact or non-response cases can distort the URS, as these often follow an atypical path through the questionnaire.

- **Interviews in Progress**: When using the online Interviewer, incomplete interviews that are still in progress can be included in the analysis, potentially distorting the URS. To minimize this issue, it's advisable to run RISSK during periods when there is minimal interviewing activity, such as during nighttime hours.

- **Question Types**: No microdata based features have been developed for barcode, picture, audio, and geography questions. These question types are only considered through their related events in the paradata.

- **Survey Modes**: RISSK is designed for CAPI or CATI modes. It has not been tested for CAWI mode in Survey Solutions.

# Confirmation of results

To rigorously test the RISSK package's effectiveness in identifying high-risk interviews, we conducted an experiment using both real and artificially created "fake" interviews. These fake interviews were designed to mimic various types of problematic interviewer behavior.

## Methodology

We utilized a real CATI survey dataset (specific details are confidential). To this, we added 77 artificial fake interviews created by 11 interviewers just after completion of fieldwork, each following one of seven scenarios designed to induce different types of interviewer behavior. Here are the scenarios:

1. **Non-incentivized, uninformed**. Pretend you are interviewing and fill in the questionnaire.
2. **Incentivized, uninformed**. Fake as good as you can, try not to get caught.
3. Same as Scenario 2 (to generate more cases).
4. **Incentivized, real timing**. Fake as good as you can, try to be realistic in timings.
5. **Incentivized, real answers**. Fake as good as you can, try to set as real answers as possible.
6. **Non-incentivized, low effort**. Fake without putting effort.
7. **Incentivized, speed**. Fake as fast as possible.

These artificial fake interviews were then mixed with 268 real interviews, creating a test set of 345 interviews. Real interviews for this survey are believed to be of general low-risk, as they were conducted by a small team of interviewers with a trusted, long-term relationship, incentives to perform well and deterrents to do badly, as well as a good data monitoring structure in place. Furthermore, interviewers were aware that the data they collected would be used to validate secondary data and that discrepancies would be investigated. Nevertheless, it could not be ruled out that some real interviews contained problematic interviewer behaviour.

## Metrics

To measure RISSK's utility in a practical survey setting, we sorted all interviews by `unit_risk_score`, select the top _N%_ - as would be done if using the URS to prioritize interviews for review/verification - and calculate the share of artificial fake interviews `share_urs`. We compare this to `share_rand`, the share of artificial fakes one would obtain if selecting _N%_ at random, which is equal to the prevalence of artificial fakes in the data. We also calculate the ratio of `share_urs/share_rand` measuring how many more artificial fake interviews are contained in the selection guided by URS vs a random selection.

## Results

The table below summarizes the results for the top 5, 10, 15 and 20 percent:

|    N | Share selecting top URS<br/>(share_urs) |    Share selecting at random <br/>(share_rand) | Ratio<br/>(share_urs/share_rand) |
|-----:|----------------------------------------:|-----------------------------------------------:|---------------------------------:|
|   5% |                                     82% |                                          22.4% |                              3.7 |
|  10% |                                     56% |                                          22.4% |                              2.5 |
|  15% |                                     43% |                                          22.4% |                              1.9 |
|  20% |                                     41% |                                          22.4% |                              1.9 |

In our test, selecting the top 5% of interviews based on their URS yielded 3.7 times more artificial fakes than if selected randomly. This ratio decreases as we select a larger percentage of interviews, but at 1.9 for 20% continues to be significantly higher within the range of review/verification ratios common in surveys.

In the chart below, the blue line (with the y-axis on the left) illustrates how `share_urs` varies as we increase the number of interviews selected, ranging from 1 to 100 of all interviews. The orange horizontal line, set at 22.4%, represents `share_rand`. The green line (with the y-axis on the right) indicates the percentage of all artificially created fake interviews contained within the top N% of interviews, sorted by their URS. As the chart shows, over two-thirds of all artificial interviews are found within the top 40% of interviews when sorted by URS.

![experiment](images/final_output_no_automatic_contamintation.png)

The results presented above were obtained by running RISSK with its default settings. The chart below shows results obtained using the automatic contamination option. This option enables the system to automatically determine the contamination levels employed by the relevant algorithms during score calculations. In our tests, using the automatic contamination level showed slightly weaker performance in the 0-10% range but surpassed the default settings in the 10-20% range.

![experiment](images/final_output_automatic_contamination.png)

Please note that our results are based on the classification of interviews as either real or artificially created, according to the experiment's design. While none of the artificially created interviews can be devoid of issues, some of the real interviews with relatively high `unit_risk_score` may also contain problematic behavior. This could potentially increase the `share_urs` value, further demonstrating the utility of the tool in identifying at-risk interviews.

> [!NOTE]
> The effectiveness is likely to differ between surveys as it depends on the nature of problematic interviews.

# Process description

This chapter outlines the key steps that the RISSK pipeline follows to generate the Unit Risk Score (URS).

## Data preparation

1. **Unzipping files**. The pipeline scans the configured data folder for Survey Solutions export ZIP files (Main Survey Data in Tab or Stata format, and Paradata). Each version's files are extracted to a subfolder, and `Questionnaire/content.zip` is further unzipped.

2. **Constructing questionnaire data**. For each version, a dataframe `df_questionnaire` is constructed from `Questionnaire/content/document.json` and the Excel files in `Questionnaire/content/Categories`. This dataframe has one row per questionnaire item (questions, variables, subsections) with columns for each item's properties (question type, variable name, etc.).

3. **Constructing microdata**. All microdata export files are identified (excluding `interview__*` and `assignment__*` files). For each file:
   - The data is loaded into a dataframe.
   - If loaded from Stata, non-response values are adjusted to match the tabular export format.
   - Columns related to multi-variable questions are consolidated into single columns.
   - System-generated variables are removed.
   - The dataframe is reshaped to long format and all version-specific dataframes are appended together.
   - All rows relating to disabled questions or Survey Solutions variables are dropped.
   - Question properties from `df_questionnaire` are merged.

4. **Constructing paradata**. Each version's paradata file is loaded into a dataframe. The `parameters` column is split and question properties are merged in from `df_questionnaire`.

5. **Appending versions**. The questionnaire, microdata, and paradata dataframes from all versions are appended to create comprehensive dataframes for each.

## Indicator generation

6. **Isolating interviewing events**. The paradata and microdata are filtered to focus solely on what we term _interviewing events_ — the initial interview process, prior to any corrections or updates after the first intervention by Supervisor or HQ roles.
    - In the paradata of every interview, the first event of type `['RejectedBySupervisor', 'OpenedBySupervisor', 'RejectedByHQ', 'OpenedByHQ']` is identified and all subsequent events are removed.
    - The remaining paradata is merged into the microdata, retaining only data points that correspond to interviewing events.

7. **Constructing features**. Various features are derived from the refined paradata and microdata. These features can either be _unit-level_ (referring to the entire interview) or _item-level_ (pertaining to the answer of an individual question on a roster instance/row). Features are absolute values, such as the time spent on a particular question measured in seconds. For a detailed explanation of how each feature is calculated, refer to [FEATURES_SCORES.md](FEATURES_SCORES.md).

## Generating scores

8. **Evaluation and score calculation**. Individual features are evaluated and corresponding scores are calculated. For an in-depth understanding of all scores, please consult [FEATURES_SCORES.md](FEATURES_SCORES.md). Generally, scores are categorized into three types:
   - **Type 1 Score — Item-level features aggregated to unit**: Anomalies are initially detected at the item level, such as identifying atypical hours of the day for a question to have been answered. Subsequently, the proportion of anomalies within each interview is calculated.
   - **Type 2 Score — Unit-level features to unit-level**: Features are directly transformed at the unit level without any aggregation. The specific transformation depends on the nature of the feature.
   - **Type 3 Score — Item-level features aggregated to interviewer**: Anomalies are first identified at the item level, grouped by both `variable_name` and `interviewer`. Then, the proportion of anomalies is calculated at the interviewer level. All interviews conducted by the same interviewer share the same Type 3 scores.

9. **Score aggregation and normalization**. Individual scores are synthesized through the following steps:
    - Type 3 Scores are aggregated using [Principal Component Analysis](https://en.wikipedia.org/wiki/Principal_component_analysis) (PCA), which is well-suited for the distribution of Type 3 Scores.
    - Type 1 and 2 Scores are aggregated using [Isolation Forest](https://en.wikipedia.org/wiki/Isolation_forest). Due to the multiple distinct types of distributions present in Type 1 and 2 Scores, Isolation Forest was preferred over PCA.
    - Results from the PCA and Isolation Forest are combined by a normalized product.
    - This product is then [winsorized](https://en.wikipedia.org/wiki/Winsorizing) to mitigate the impact of extreme outliers.
    - Finally, the winsorized product is [rescaled](https://en.wikipedia.org/wiki/Feature_scaling#Rescaling_(min-max_normalization)) to a range of 0–100, rendering the resulting `unit_risk_score` easy to interpret.

# Roadmap
We have planned a series of enhancements and additions to RISSK, which are maintained as [issues](https://github.com/rowsquared/rissk/issues). They can be categorized into the following broad areas:

- **Deploy RISSK:**
  - Expand its application across diverse survey types and contexts.
  - Actively seek feedback to refine the tool, address bugs, and gather more evidence on its efficacy.
- **Improve usability**:
  - Provide additional outputs, including standardized summary reports and dashboards.
- **Refine methodology**:
  - Minimize both false positives and negatives to bolster the reliability of the tool.
  - Experiment with feature engineering, score design, and aggregation methods with more testing data.
- **Make RISSK learn**:
  - Develop standardized framework for users to record review/verification outcomes.
  - Produce an anonymized output format, allowing users to share data that can help refine RISSK's algorithms.
  - For individual surveys: Establish a feedback loop enabling RISSK to adapt based on previous verification outcomes.
  - Across different surveys: With standard verification outcome and access to more testing data, alternative methodologies can be explored, such as training neural networks, to enhance RISSK's prediction accuracy.
