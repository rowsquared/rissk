"""The wheel must carry conf/base so an installed rissk can start Kedro without a checkout."""
import pathlib
import shutil
import subprocess
import tomllib
import zipfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_pyproject_ships_conf_base_in_the_wheel():
    py = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert py["build-system"]["build-backend"] == "hatchling.build"
    wheel = py["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["src/rissk"]
    assert wheel["force-include"] == {"conf/base": "rissk/_conf/base"}
    assert py["project"]["version"] == "0.2.0"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_wheel_contains_conf_base_only(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
                   cwd=REPO, check=True, capture_output=True)
    names = set(zipfile.ZipFile(next(tmp_path.glob("rissk-*.whl"))).namelist())
    assert {"rissk/_conf/base/catalog.yml", "rissk/_conf/base/globals.yml",
            "rissk/_conf/base/parameters.yml"} <= names
    assert not [n for n in names if n.startswith("rissk/_conf/") and "/base/" not in n]


def test_package_version_matches_pyproject():
    import rissk

    py = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert rissk.__version__ == py["project"]["version"]


def test_telemetry_hooks_are_disabled():
    from rissk import settings

    assert "kedro-telemetry" in settings.DISABLE_HOOKS_FOR_PLUGINS
