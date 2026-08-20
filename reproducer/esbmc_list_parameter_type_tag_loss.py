#!/usr/bin/env python3
"""
ESBMC-Python frontend defect — minimal reproducer.
  A parameter annotated with a bare `list` loses its elements' type in
  the callee. Arithmetic on the subscript is modelled as an uncaught
  `TypeError`; an equality on it silently evaluates false with no
  exception at all. Annotating the parameter `list[int]` fixes both.

Filed upstream as esbmc/esbmc#7187.

Observed with: ESBMC 8.4.0, build v8.4-985-g40472d4a1f, whose
`src/python-frontend/` is identical to master 9676e09665.

Not a boto3 finding — this is a verifier bug that constrains how the
harnesses in ../harness/ may be written: annotate every list parameter
with its element type. See ROADMAP.md constraint C1.

Cases D and E below were added after the first write-up; they are what
show the annotation is the fix, and they replaced the original, much
more restrictive workaround of never letting a list cross a call.

--------------------------------------------------------------------
Case C — list parameter, element compared to an int literal: FAILS
--------------------------------------------------------------------

    def cmp_(xs: list) -> int:
        if xs[0] == 5:
            return 1
        return 0

    def main() -> None:
        xs = [5, 3]
        assert cmp_(xs) == 1        # <-- FAILED

    main()

  $ esbmc --multi-property c.py
    FAILED  [main.assertion.1]  line 9  assertion return_value$_cmp_$1 == 1
    VERIFICATION FAILED

--------------------------------------------------------------------
Case A — list parameter, element used in arithmetic: FAILS
--------------------------------------------------------------------

    def use(xs: list) -> int:
        return xs[0] + 0

    def main() -> None:
        xs = [5, 3]
        assert use(xs) == 5         # <-- FAILED

    main()

  $ esbmc --multi-property a.py
    FAILED  [global.assertion.3]  line 0  uncaught exception: TypeError
    VERIFICATION FAILED

  The `TypeError` claim is the tell: the element's type tag is gone, so
  the frontend cannot prove `int + int` and admits the exception path.

--------------------------------------------------------------------
Case B — same comparison, list created inside the callee: SUCCEEDS
--------------------------------------------------------------------

    def local_cmp() -> int:
        ys = [5, 3]
        if ys[0] == 5:
            return 1
        return 0

    def main() -> None:
        assert local_cmp() == 1     # <-- passes

    main()

  $ esbmc --multi-property b.py
    VERIFICATION SUCCESSFUL

--------------------------------------------------------------------
Also SUCCEEDS — return the element, compare in the caller
--------------------------------------------------------------------

    def read0(xs: list) -> int:
        return xs[0]

    def main() -> None:
        xs = [5, 3]
        assert read0(xs) == 5       # <-- passes

    main()

  So the loss is on *use* of the subscripted parameter inside the
  callee, not on the subscript itself or on the argument passing.

--------------------------------------------------------------------
Workaround adopted by this PoC
--------------------------------------------------------------------

Keep every list in the function that creates it. Harnesses that need a
list either inline the loop into `main` (see harness/all_not_none.py)
or build and consume the list inside one helper (see
harness/collection_limit_nonpositive.py, where `page_sizes` is local).

Dependencies: none (pure Python stdlib) — but note these snippets are
verifier inputs, not CPython programs: under CPython all four cases
pass trivially, which is itself the point. The divergence is between
CPython semantics and ESBMC's model, so CPython is the oracle here.

--------------------------------------------------------------------
Case D — arithmetic, parameter annotated `list[int]`: SUCCEEDS
--------------------------------------------------------------------

    def use_typed(xs: list[int]) -> int:
        return xs[0] + 0

    def main() -> None:
        xs = [5, 3]
        assert use_typed(xs) == 5

    main()

  $ esbmc --multi-property d.py
    ** 0 of 47 properties failed, 47 passed
    VERIFICATION SUCCESSFUL

--------------------------------------------------------------------
Case E — equality, parameter annotated `list[int]`: SUCCEEDS
--------------------------------------------------------------------

    def cmp_typed(xs: list[int]) -> int:
        if xs[0] == 5:
            return 1
        return 0

    def main() -> None:
        xs = [5, 3]
        assert cmp_typed(xs) == 1

    main()

  $ esbmc --multi-property e.py
    ** 0 of 47 properties failed, 47 passed
    VERIFICATION SUCCESSFUL

  The property count rises from 36 (bare `list`) to 47 (`list[int]`):
  the element type is what enables the extra checks.

  All five programs exit 0 under CPython 3.12, so every FAILED verdict
  above is spurious.

"""

# Executable form of the divergence: under CPython every assertion below
# holds. Under ESBMC 8.4.0, only the bare-`list` cases (A and C) report
# VERIFICATION FAILED; annotating the parameter `list[int]` (D2, E2)
# makes them pass. The annotations are what the verifier reads, so they
# are written out here even though CPython ignores them.


def cmp_(xs: list):
    if xs[0] == 5:
        return 1
    return 0


def use(xs: list):
    return xs[0] + 0


def local_cmp():
    ys = [5, 3]
    if ys[0] == 5:
        return 1
    return 0


def read0(xs: list):
    return xs[0]


def use_typed(xs: list[int]):
    return xs[0] + 0


def cmp_typed(xs: list[int]):
    if xs[0] == 5:
        return 1
    return 0


values = [5, 3]
assert cmp_(values) == 1, 'C: bare list parameter + comparison'
assert use(values) == 5, 'A: bare list parameter + arithmetic'
assert local_cmp() == 1, 'B: list local to callee'
assert read0(values) == 5, 'return element, compare in caller'
assert use_typed(values) == 5, 'D: list[int] parameter + arithmetic'
assert cmp_typed(values) == 1, 'E: list[int] parameter + comparison'
print(
    'CPython: all six assertions hold. ESBMC 8.4.0 reports FAILED for A '
    'and C (bare `list`) and SUCCESSFUL for D and E (`list[int]`).'
)
