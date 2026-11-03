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
        # RISSK — feature scores

        Distribution and coverage of each **item-level** feature score (`s__*`)
        across all scored items of the selected questionnaire.
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
    item_scores = viz.load_item_scores(qnr.value)
    score_cols = viz.score_columns(item_scores)
    return item_scores, score_cols


@app.cell
def _(mo, score_cols):
    feature = mo.ui.dropdown(
        options=score_cols,
        value=score_cols[0] if score_cols else None,
        label="Feature score",
        searchable=True,
    )
    feature
    return (feature,)


@app.cell
def _(alt, feature, item_scores, mo, viz):
    _hist = viz.histogram_df(item_scores[feature.value], bins=30)
    _chart = (
        alt.Chart(_hist)
        .mark_bar()
        .encode(
            x=alt.X("bin_start:Q", title=feature.value),
            x2="bin_end:Q",
            y=alt.Y("count:Q", title="items"),
            tooltip=["bin_start", "bin_end", "count"],
        )
        .properties(height=260, title=f"Distribution of {feature.value}")
    )
    mo.ui.altair_chart(_chart)
    return


@app.cell
def _(item_scores, pd, score_cols):
    coverage = pd.DataFrame(
        {
            "feature": score_cols,
            "items_scored": [int(item_scores[c].notna().sum()) for c in score_cols],
        }
    ).sort_values("items_scored", ascending=False)
    return (coverage,)


@app.cell
def _(alt, coverage, mo):
    _cov = (
        alt.Chart(coverage)
        .mark_bar()
        .encode(
            x=alt.X("items_scored:Q", title="items scored"),
            y=alt.Y("feature:N", sort="-x", title=None),
            tooltip=["feature", "items_scored"],
        )
        .properties(height=400, title="Feature coverage (non-null item scores)")
    )
    mo.ui.altair_chart(_cov)
    return


if __name__ == "__main__":
    app.run()
