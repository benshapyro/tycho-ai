"""Starting Tycho lives in ``tycho.start`` and the tray and window in ``desktop.shell``
(#398), apart from the command line."""

import subprocess
import sys


def test_starting_loads_without_the_command_line() -> None:
    # #398: all of it was in app.py, the command line, so starting loaded every command.
    script = (
        "import sys, tycho.start.run, tycho.desktop.shell\n"
        "print('tycho.app' in sys.modules, 'tycho.cli' in sys.modules)"
    )
    done = subprocess.run(  # noqa: S603 - this interpreter, running the script above
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "False False", done.stderr
