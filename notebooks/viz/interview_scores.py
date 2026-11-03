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
        # RISSK — single interview drill-down

        Pick one interview to see its **unit risk score**, the feature scores that
        drove it, and the riskiest individual items within it.
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
    item = viz.load_item_scores(qnr.value)
    return item, unit


@app.cell
def _(mo, unit):
    # interviews ordered riskiest-first for convenience
    _ids = unit.sort_values("unit_risk_score", ascending=False)["interview__id"].tolist()
    interview = mo.ui.dropdown(
        options=_ids,
        value=_ids[0] if _ids else None,
        label="Interview",
        searchable=True,
    )
    interview
    return (interview,)


@app.cell
def _(interview, mo, unit):
    mo.stop(interview.value is None, mo.md("_Select an interview._"))
    row = unit[unit["interview__id"] == interview.value].iloc[0]
    mo.hstack(
        [
            mo.stat(label="unit_risk_score", value=round(float(row["unit_risk_score"]), 1)),
            mo.stat(label="interviewer", value=str(row.get("responsible", "—"))),
            mo.stat(label="version", value=str(row.get("qnr_version", "—"))),
        ],
        justify="start",
        gap=2,
    )
    return (row,)


@app.cell
def _(alt, mo, pd, row, unit, viz):
    _scols = viz.score_columns(unit)
    _df = pd.DataFrame({"feature": _scols, "score": [float(row[c]) for c in _scols]})
    _df = _df[_df["score"].abs() > 0].sort_values("score", ascending=False)
    _chart = (
        alt.Chart(_df)
        .mark_bar()
        .encode(
            x=alt.X("score:Q"),
            y=alt.Y("feature:N", sort="-x", title=None),
            tooltip=["feature", "score"],
        )
        .properties(height=320, title="Feature scores contributing to this interview")
    )
    mo.vstack([mo.md("### Feature scores for this interview"), mo.ui.altair_chart(_chart)])
    return


@app.cell
def _(interview, item, mo, viz):
    _scols = viz.score_columns(item)
    _sub = item[item["interview__id"] == interview.value]
    _id_vars = [c for c in ["variable_name", "roster_level"] if c in _sub.columns]
    _long = _sub.melt(id_vars=_id_vars, value_vars=_scols, var_name="feature", value_name="score")
    _long = (
        _long[_long["score"].fillna(0).abs() > 0]
        .sort_values("score", ascending=False)
        .head(25)
    )
    mo.vstack([mo.md("### Riskiest items in this interview"), mo.ui.table(_long)])
    return


@app.cell
def _(alt, mo, pd, row, unit, viz):
    _hist = viz.histogram_df(unit["unit_risk_score"], bins=25)
    _base = alt.Chart(_hist).mark_bar(opacity=0.7).encode(
        x=alt.X("bin_start:Q", title="unit_risk_score"),
        x2="bin_end:Q",
        y=alt.Y("count:Q", title="interviews"),
    )
    _rule = (
        alt.Chart(pd.DataFrame({"x": [float(row["unit_risk_score"])]}))
        .mark_rule(color="red", size=2)
        .encode(x="x:Q")
    )
    _chart = (_base + _rule).properties(
        height=240, title="Where this interview sits in the URS distribution"
    )
    mo.ui.altair_chart(_chart)
    return


if __name__ == "__main__":
    app.run()
