import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo
    import altair as alt
    import pandas as pd

    from rissk import viz

    return alt, mo, pd, viz


@app.cell
def _(mo):
    mo.md(
        """
        # RISSK — unit (interview) risk scores

        The aggregated **0–100 unit risk score** per interview, and how it breaks
        down by interviewer and questionnaire version.
        """
    )
    return


@app.cell
def _(mo, viz):
    _qnrs = viz.list_questionnaires()
    qnr = mo.ui.dropdown(
        options=_qnrs,
        value=_qnrs[0] if _qnrs else None,
        label="Questionnaire",
    )
    qnr
    return (qnr,)


@app.cell
def _(mo, qnr, viz):
    mo.stop(qnr.value is None, mo.md("**No scored questionnaire found under the data root.**"))
    unit = viz.load_unit_scores(qnr.value)
    return (unit,)


@app.cell
def _(mo, unit):
    mo.hstack(
        [
            mo.stat(label="Interviews", value=int(len(unit))),
            mo.stat(label="Mean URS", value=round(float(unit["unit_risk_score"].mean()), 1)),
            mo.stat(label="Max URS", value=round(float(unit["unit_risk_score"].max()), 1)),
            mo.stat(label="Interviewers", value=int(unit["responsible"].nunique())),
        ],
        justify="start",
        gap=2,
    )
    return


@app.cell
def _(alt, mo, unit, viz):
    _hist = viz.histogram_df(unit["unit_risk_score"], bins=25)
    _chart = (
        alt.Chart(_hist)
        .mark_bar()
        .encode(
            x=alt.X("bin_start:Q", title="unit_risk_score"),
            x2="bin_end:Q",
            y=alt.Y("count:Q", title="interviews"),
            tooltip=["bin_start", "bin_end", "count"],
        )
        .properties(height=260, title="Distribution of the 0–100 unit risk score")
    )
    mo.ui.altair_chart(_chart)
    return


@app.cell
def _(mo):
    top_n = mo.ui.slider(5, 50, value=15, label="Top N riskiest")
    top_n
    return (top_n,)


@app.cell
def _(mo, top_n, unit):
    _cols = [
        c
        for c in ["interview__id", "responsible", "qnr_version", "unit_risk_score"]
        if c in unit.columns
    ]
    _top = unit.sort_values("unit_risk_score", ascending=False).head(top_n.value)[_cols]
    mo.vstack([mo.md("### Riskiest interviews"), mo.ui.table(_top)])
    return


@app.cell
def _(alt, mo, unit):
    _by_resp = (
        alt.Chart(unit)
        .mark_boxplot()
        .encode(
            x=alt.X("unit_risk_score:Q", title="unit_risk_score"),
            y=alt.Y("responsible:N", title="interviewer", sort="-x"),
        )
        .properties(height=320, title="Unit risk score by interviewer")
    )
    mo.ui.altair_chart(_by_resp)
    return


@app.cell
def _(mo, unit, viz):
    _scols = viz.score_columns(unit)
    feat = mo.ui.dropdown(
        options=_scols,
        value=_scols[0] if _scols else None,
        label="Feature score (x-axis)",
        searchable=True,
    )
    feat
    return (feat,)


@app.cell
def _(alt, feat, mo, unit):
    mo.stop(feat.value is None, mo.md("_This output has no `s__` feature columns._"))
    _sc = (
        alt.Chart(unit)
        .mark_circle(opacity=0.5)
        .encode(
            x=alt.X(f"{feat.value}:Q"),
            y=alt.Y("unit_risk_score:Q"),
            color=alt.Color("responsible:N", legend=None),
            tooltip=["interview__id", "responsible", "unit_risk_score", feat.value],
        )
        .properties(height=320, title=f"unit_risk_score vs {feat.value}")
    )
    mo.ui.altair_chart(_sc)
    return


if __name__ == "__main__":
    app.run()
