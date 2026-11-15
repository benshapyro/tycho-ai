"""``tycho check``'s findings live in ``doctor`` (#398), apart from the app."""

import subprocess
import sys


def test_the_checks_load_without_the_app() -> None:
    # #398: the findings were in app.py, so anything that checked loaded the command line.
    script = (
        "import sys, tycho.doctor, tycho.stt.choice, tycho.voice.choice\n"
        "print('tycho.cli' in sys.modules)"
    )
    done = subprocess.run(  # noqa: S603 - this interpreter, running the script above
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "False", done.stderr
