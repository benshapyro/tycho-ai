"""Command-line entry point: ``tycho run``, ``tycho check``, ``tycho bind``, ``tycho sounds``."""

import sys

from tycho.app import main

if __name__ == "__main__":
    sys.exit(main())
