"""Does every native dependency actually load in this environment?

WHY THIS EXISTS

`python:3.12-slim` ships no OpenMP runtime, and LightGBM links against it. `pip install
lightgbm` succeeds, `import lightgbm` succeeds, and the failure arrives only when a model
is loaded:

    OSError: libgomp.so.1: cannot open shared object file

That surfaced on the deployed host, on the one screen that loads a model, an hour after the
image first built green. Nothing local could have caught it -- the developer machine has
libgomp, and so does every base image that is not slim.

The lesson generalises past libgomp: **a wheel installing is not the same as its shared
libraries being present**, and the gap between the two is invisible until something calls
into the native layer. scikit-learn, numpy, polars and matplotlib all have native
components, and each one is silent until a code path touches it.

TWO PLACES THIS RUNS

*   **At image build time**, as a `RUN` step. A missing shared library then fails the
    build rather than a screen, which is the whole point -- the same principle as every
    other guard in this project: make it structurally impossible rather than promised.
*   **From `/health`**, so a running container can be interrogated without shelling in.

It deliberately does NOT run at startup and abort. Taking the whole service down for a
dependency only one screen needs would be a worse failure than that screen failing, and
the seeded run needs none of them.
"""

from __future__ import annotations

import importlib

# Every dependency with a compiled component. The *exercise* matters as much as the
# import: `import lightgbm` succeeds without libgomp; loading a Booster does not.
NATIVE_CHECKS: tuple[tuple[str, str], ...] = (
    (
        "lightgbm",
        # Trains a two-row model, which is what actually enters the OpenMP runtime.
        "import numpy as np, lightgbm as lgb;"
        "lgb.train({'objective':'binary','verbose':-1,'num_leaves':2},"
        "lgb.Dataset(np.array([[0.0],[1.0]]), label=np.array([0,1])), num_boost_round=1)",
    ),
    (
        "sklearn",
        "from sklearn.isotonic import IsotonicRegression;"
        "IsotonicRegression().fit([0.0,1.0],[0.0,1.0])",
    ),
    ("numpy", "import numpy as np; np.linalg.norm(np.array([3.0,4.0]))"),
    ("scipy", "import scipy.stats as s; s.norm.cdf(0.0)"),
    ("polars", "import polars as pl; pl.DataFrame({'a':[1]}).height"),
    ("pandas", "import pandas as pd; pd.DataFrame({'a':[1]}).sum()"),
    ("rapidfuzz", "from rapidfuzz import fuzz; fuzz.ratio('a','a')"),
    ("psycopg", "import psycopg"),
    (
        "matplotlib",
        "import matplotlib; matplotlib.use('Agg');"
        "import matplotlib.pyplot as plt; plt.figure()",
    ),
    (
        # pydantic v2's validation core is Rust. Defining a model compiles a validator;
        # only calling it enters that core, which is what needs exercising.
        "pydantic",
        "from pydantic import BaseModel;"
        "M = type('M', (BaseModel,), {'__annotations__': {'x': int}});"
        "assert M(x='3').x == 3",
    ),
)


def check_native() -> dict[str, str]:
    """Exercise every native dependency. Returns {name: 'ok'} or {name: the error}."""
    results: dict[str, str] = {}
    for name, snippet in NATIVE_CHECKS:
        try:
            importlib.import_module(name.split(".")[0])
            exec(compile(snippet, f"<selfcheck:{name}>", "exec"), {})  # noqa: S102
            results[name] = "ok"
        except Exception as exc:
            # The message matters more than the type: "libgomp.so.1: cannot open shared
            # object file" names the apt package to install, and a bare exception type
            # does not.
            results[name] = f"{type(exc).__name__}: {exc}"[:220]
    return results


def main() -> int:
    """Build-time entry point. Non-zero exit fails the image build."""
    results = check_native()
    broken = {k: v for k, v in results.items() if v != "ok"}

    width = max(len(k) for k in results)
    for name, status in results.items():
        mark = "ok " if status == "ok" else "FAIL"
        print(f"  [{mark}] {name:<{width}}  {'' if status == 'ok' else status}")

    if broken:
        print(f"\n{len(broken)} native dependency check(s) failed.")
        print("A wheel installing is not the same as its shared libraries being present.")
        return 1

    print(f"\nall {len(results)} native dependencies load and execute")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
