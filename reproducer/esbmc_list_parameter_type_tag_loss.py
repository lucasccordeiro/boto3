#!/usr/bin/env python3
"""
ESBMC-Python frontend defect — minimal reproducer.
  A list passed as a function *argument* loses its elements' type tags
  in the callee. Subscripting the parameter and then comparing or doing
  arithmetic on the result is modelled as an uncaught `TypeError`, so
  any such harness returns a spurious VERIFICATION FAILED.

Observed with: ESBMC version 8.4.0 64-bit x86_64 linux.

Not a boto3 finding — this is a verifier bug that constrains how the
harnesses in ../harness/ may be written. See ROADMAP.md
"Modelling constraints (ESBMC-Python)".

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
"""

# Executable form of the divergence: under CPython every assertion below
# holds. Under ESBMC 8.4.0, cases A and C report VERIFICATION FAILED.


def cmp_(xs):
    if xs[0] == 5:
        return 1
    return 0


def use(xs):
    return xs[0] + 0


def local_cmp():
    ys = [5, 3]
    if ys[0] == 5:
        return 1
    return 0


def read0(xs):
    return xs[0]


values = [5, 3]
assert cmp_(values) == 1, 'C: list parameter + comparison'
assert use(values) == 5, 'A: list parameter + arithmetic'
assert local_cmp() == 1, 'B: list local to callee'
assert read0(values) == 5, 'D: return element, compare in caller'
print('CPython: all four cases hold; ESBMC 8.4.0 reports FAILED for A and C')
