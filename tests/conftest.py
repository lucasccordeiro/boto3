# SPDX-License-Identifier: Apache-2.0
"""ESBMC's generated files are parsed by `esbmc_witness`, not collected.

They open with `from <harness> import *`, and harnesses are deliberately
not importable under CPython -- see the module docstring in
`esbmc_witness.py`.

`harness/` goes on the path so the tests can take the value-domain
sentinels from `harness/stubs.py` rather than restating them. That
module is import-safe: its intrinsic declarations sit under
`TYPE_CHECKING`, so nothing executable is defined at runtime.
"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness")
)

collect_ignore_glob = ["generated/*"]
