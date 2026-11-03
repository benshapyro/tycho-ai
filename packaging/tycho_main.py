"""The installed app's entry point: the same as `uv run tycho`."""

import multiprocessing
import sys

from tycho.app import main

if __name__ == "__main__":
    multiprocessing.freeze_support()  # harmless if unused; required if anything spawns
    sys.exit(main())
