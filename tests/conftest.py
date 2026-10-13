import functools

import pytest

from guetteur import synth
from guetteur.evalrun import score_run
from guetteur.runner import run_scenario


@functools.lru_cache(maxsize=None)
def _run(name: str, seed: int, planner: str, gate: str):
    return run_scenario(name, seed, planner, gate)


@pytest.fixture(scope="session")
def run():
    return _run


@pytest.fixture(scope="session")
def scored():
    def f(name, seed=0, planner="rule", gate="approve"):
        res, meta = _run(name, seed, planner, gate)
        return res, meta, score_run(res, meta)
    return f


ALL = list(synth.scenarios())
