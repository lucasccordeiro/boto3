# SPDX-License-Identifier: Apache-2.0
"""Read counterexample inputs out of ESBMC's generated pytest files.

`esbmc --generate-pytest-testcase` writes a file under `tests/generated/`
whose `@pytest.mark.parametrize` list holds the concrete inputs from the
counterexample. The tests in this directory drive the *real* boto3 code
with exactly those values, so the executable reproduction is tied to the
verifier's output rather than to a hand transcription of it.

The generated files are parsed, never imported. They begin with
`from <harness> import *`, and a harness is not importable under CPython
on purpose: `harness/stubs.py` declares `nondet_int` and friends only
under `TYPE_CHECKING`, because a runtime definition would shadow the
ESBMC intrinsic, collapse every symbolic value to a constant, and make
the whole suite pass vacuously (vLLM RETROSPECTIVE.md Finding 1). Making
the generated files runnable would mean breaking that rule, so they are
treated as data.
"""

from __future__ import annotations

import ast
import os

GENERATED_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "generated")


def _parametrize_call(tree: ast.Module) -> ast.Call:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "parametrize"
        ):
            return node
    raise AssertionError("no @pytest.mark.parametrize found")


def witness(harness_name: str) -> dict[str, int]:
    """Return the counterexample ESBMC found for `harness_name`.

    Maps the generated parameter names to their values. ESBMC numbers
    repeated nondet assignments in a loop as `v`, `v1`, `v2`, ... in
    source order, so the names are positional within a loop body.
    """
    path = os.path.join(GENERATED_DIR, f"test_{harness_name}.py")
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)

    call = _parametrize_call(tree)
    names = [n.strip() for n in ast.literal_eval(call.args[0]).split(",")]
    rows = ast.literal_eval(call.args[1])
    assert rows, f"{path}: no counterexample rows"
    assert len(rows) == 1, f"{path}: expected one counterexample, got {len(rows)}"
    values = rows[0]
    assert len(names) == len(values), f"{path}: {names} vs {values}"
    return dict(zip(names, values))


def witness_values(harness_name: str, *names: str) -> tuple[int, ...]:
    """`witness()` restricted to `names`, in the order given."""
    found = witness(harness_name)
    missing = [n for n in names if n not in found]
    assert not missing, f"{harness_name}: no such witness inputs {missing} in {found}"
    return tuple(found[n] for n in names)
