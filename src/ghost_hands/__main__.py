"""Allow `python -m ghost_hands ...` as an entry point.

Some MCP directories and checkers launch a Python server straight from
the source tree with `python -m <package>`; without this module that
route fails before the server can answer. It simply delegates to the
same main() as the `ghost-hands` console script.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
