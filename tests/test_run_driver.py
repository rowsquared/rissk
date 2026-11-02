from pathlib import Path

from rissk.run import load_questionnaire_configs


def test_returns_empty_when_no_questionnaires_dir(tmp_path):
    (tmp_path / "conf" / "solo").mkdir(parents=True)
    assert load_questionnaire_configs("solo", tmp_path) == []


def test_parses_and_sorts_questionnaire_yamls(tmp_path):
    qdir = tmp_path / "conf" / "pmpmd" / "questionnaires"
    qdir.mkdir(parents=True)
    (qdir / "household.yml").write_text("name: pmpmd_household\nVERSION: []\nfilter_var: null\n")
    (qdir / "community.yml").write_text("name: pmpmd_community\nVERSION: [2, 3]\nfilter_var: null\n")
    got = load_questionnaire_configs("pmpmd", tmp_path)
    assert [q["name"] for q in got] == ["pmpmd_community", "pmpmd_household"]  # sorted by filename
    assert got[0]["VERSION"] == [2, 3]
