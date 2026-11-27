"""The installer script (packaging/tycho.iss), as far as it can be checked without
Inno Setup: CI builds it."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from tycho.runtime import instance

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "tycho.iss"


def _setup_values() -> dict[str, str]:
    """The [Setup] section's ``Name=value`` lines."""
    values: dict[str, str] = {}
    section = ""
    for raw in SCRIPT.read_text("utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
        elif section == "[Setup]" and "=" in line and not line.startswith((";", "#")):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def test_setup_waits_for_a_running_tycho_to_quit() -> None:
    # A running Tycho locks its files, and the tray app has no window for Windows to
    # close: without this, an upgrade fails one locked file at a time (#333).
    assert _setup_values()["AppMutex"] == instance.DEFAULT_NAME


# Getting ready for SignPath Foundation's signing (#9). Its terms ask that the privacy
# policy is shown during installation, that changes to the system are announced, and
# that every signed file carries the product's name and version.

REPO = SCRIPT.parent.parent
SERVICES = (
    "Anthropic",
    "OpenRouter",
    "ElevenLabs",
    "Perplexity",
    "UEX",
    "Star Citizen Wiki",
    "GitHub",
    "Hugging Face",
    "Sentry",
)


def _version_info() -> ModuleType:
    """packaging/version_info.py, loaded by path as the spec loads it."""

    spec = importlib.util.spec_from_file_location(
        "tycho_version_info", REPO / "packaging" / "version_info.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section(name: str) -> list[str]:
    lines: list[str] = []
    inside = False
    for raw in SCRIPT.read_text("utf-8").splitlines():
        line = raw.strip()
        if line.startswith("["):
            inside = line == name
        elif inside and line and not line.startswith(";"):
            lines.append(line)
    return lines


def test_setup_shows_what_leaves_the_pc() -> None:
    shown = SCRIPT.parent / _setup_values()["InfoAfterFile"]
    text = shown.read_text("utf-8")
    assert text.isascii()  # Inno Setup reads a file with no byte-order mark as ANSI
    assert "PRIVACY.md" in text
    for service in SERVICES:
        assert service in text, service


def test_the_privacy_policy_names_every_service_in_its_table() -> None:
    policy = (REPO / "PRIVACY.md").read_text("utf-8")
    rows = [
        line.split("|")[1]
        for line in policy.splitlines()
        if line.startswith("| ") and not line.startswith(("| Service", "|---"))
    ]
    assert rows
    for row in rows:
        # A service added to the table must be added to SERVICES, and so to the
        # summary setup shows.
        assert any(service in row for service in SERVICES), row


def test_setup_asks_before_it_changes_the_path() -> None:
    tasks = _section("[Tasks]")
    assert any(line.startswith('Name: "addtopath"') for line in tasks)
    path = [line for line in _section("[Registry]") if 'ValueName: "Path"' in line]
    assert path
    assert all("Tasks: addtopath" in line for line in path)


def test_the_installer_and_programs_carry_tychos_name_and_version() -> None:
    values = _setup_values()
    info = _version_info()
    assert values["VersionInfoProductName"] == info.PRODUCT == "Tycho"
    assert values["VersionInfoProductVersion"] == "{#AppNumericVersion}"
    assert values["VersionInfoProductTextVersion"] == "{#AppVersion}"
    strings = info.strings("0.11.0", "tycho.exe", "Tycho")
    assert strings["ProductName"] == "Tycho"
    assert strings["ProductVersion"] == "0.11.0"
    assert strings["OriginalFilename"] == "tycho.exe"
    assert info.numbers("0.11.0") == (0, 11, 0, 0)
    assert info.numbers("0.12.0rc1") == (0, 12, 0, 0)
    assert info.numbers("1.2.3.4.5") == (1, 2, 3, 4)


def test_a_pre_release_gives_inno_setup_only_numbers() -> None:
    """#724: Inno Setup's binary version takes only numbers, so ``0.12.0rc1`` would fail
    the build; its text version stays the whole version, which SignPath checks."""
    values = _setup_values()
    assert values["VersionInfoVersion"] == "{#AppNumericVersion}"
    assert values["VersionInfoProductVersion"] == "{#AppNumericVersion}"
    assert values["VersionInfoProductTextVersion"] == "{#AppVersion}"
    script = SCRIPT.read_text("utf-8")
    assert "#ifndef AppNumericVersion\n  #define AppNumericVersion AppVersion\n#endif" in script
    build = (REPO / ".github" / "workflows" / "build.yml").read_text("utf-8")
    assert '"/DAppNumericVersion=$numeric"' in build
    assert "python packaging/version_info.py $version" in build
    done = subprocess.run(  # noqa: S603 - this repo's own script
        [sys.executable, str(REPO / "packaging" / "version_info.py"), "0.12.0rc1"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == "0.12.0.0"


def test_ci_checks_the_name_and_version_signpath_checks() -> None:
    """#724: a wrong product name or version fails every build, not a release's signing."""
    build = (REPO / ".github" / "workflows" / "build.yml").read_text("utf-8")
    check = build.split("- name: The programs and installer carry Tycho's name and version")[1]
    check = check.split("- name:")[0]
    for name in ("tycho.exe", "tycho-app.exe", "TychoSetup-$version.exe"):
        assert name in check
    assert '$info.ProductName -ne "Tycho"' in check
    assert "$info.ProductVersion -ne $version" in check


def test_a_merge_to_main_never_cancels_a_signing_run() -> None:
    """#724: a sign run waits for approvals; it gets its own group, never cancelled."""
    build = (REPO / ".github" / "workflows" / "build.yml").read_text("utf-8")
    assert "group: build-${{ github.ref }}${{ inputs.sign && '-sign' || '' }}" in build
    assert "cancel-in-progress: ${{ !inputs.sign }}" in build


def test_signpath_signs_the_installer_by_its_exact_name() -> None:
    """#724: the installer's configuration names the one file, by the version."""
    config = (REPO / ".signpath" / "artifact-configurations" / "installer.xml").read_text("utf-8")
    assert '<pe-file path="TychoSetup-${version}.exe"' in config
    assert "*" not in config.split("<zip-file>")[1]


def test_signpath_checks_the_same_name_the_build_sets() -> None:
    folder = REPO / ".signpath" / "artifact-configurations"
    for name in ("exes.xml", "installer.xml"):
        config = (folder / name).read_text("utf-8")
        assert 'product-name="Tycho"' in config, name
        assert 'product-version="${version}"' in config, name


def test_the_readme_has_the_code_signing_policy() -> None:
    readme = (REPO / "README.md").read_text("utf-8")
    assert "## Code signing policy" in readme
    assert (
        "Free code signing provided by [SignPath.io](https://about.signpath.io), "
        "certificate by [SignPath Foundation](https://signpath.org)."
    ) in readme
    assert "(PRIVACY.md)" in readme
