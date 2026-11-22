"""The session, wired the way ``tycho run`` wires it for the chosen game: the pack, its
keybinds, the speech model, the commands. Out of ``app.py`` (#398)."""

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from tycho import paths
from tycho.commands import CommandFileError, CommandMatcher
from tycho.games import find_install, load_keys
from tycho.keybinds import Keybinds
from tycho.packs import GameKeys, GamePack, as_run, built_in
from tycho.ptt.binding import Binding
from tycho.ptt.conflicts import conflict_warnings
from tycho.ptt.devices import input_sources
from tycho.settings import Settings
from tycho.stt import PARAKEET_V2, PARAKEET_V2_INT8, ModelError, ModelStore, speech_models
from tycho.stt.choice import device_choice, device_name, gpu_hint

if TYPE_CHECKING:
    from tycho.address import Address
    from tycho.audio.source import AudioSource
    from tycho.input import FocusCheck, KeySender
    from tycho.runtime import (
        ActionLog,
        Brain,
        Camera,
        Display,
        Feedback,
        GameState,
        Result,
        Session,
        Speaker,
        Transcriber,
    )
    from tycho.stt.parakeet import ParakeetTranscriber

log = logging.getLogger("tycho")


def game_pack(current: Settings) -> GamePack:
    """The game the settings pick (ADR 18), as the player's ``key_actions`` runs it (#191),
    named in the log with its version, its mode and the hash of its briefing, so a bug
    report or a score says which pack it came from."""
    from tycho.brain.briefing import instructions_hash

    pack = as_run(built_in(current.game), key_actions=current.key_actions)
    log.info(
        "Game: %s, pack %s, key actions %s (briefing %s)",
        pack.name,
        pack.version,
        "off" if pack.safety.keys_off else "on",
        instructions_hash(pack),
    )
    return pack


def load_keybinds(current: Settings, binding: Binding) -> GameKeys:
    install = find_install(current)
    store = load_keys(install)
    log.info("Keybinds: %d actions from %s", len(store.keybinds), install.folder)
    for warning in conflict_warnings(binding, store):
        log.warning("%s", warning)
    return store


def load_transcriber(
    current: Settings, doing: Callable[[str], None] | None = None
) -> ParakeetTranscriber:
    """``doing`` hears what's happening, for the star of a Tycho with no terminal."""
    from tycho.stt.parakeet import ParakeetTranscriber

    choice = device_choice(current)
    store = ModelStore(paths.models_dir())
    for spec in speech_models(choice.device):
        try:
            store.ensure(spec, download_progress(doing=doing))
        except ModelError as e:
            # Upgraded offline (#480): the full model is here, so start on it, on the
            # processor, and fetch the 8-bit copy at a later start.
            if spec != PARAKEET_V2_INT8 or not store.ready(PARAKEET_V2):
                raise
            log.warning("%s Using the full speech model until then.", e)
    if doing is not None:
        doing("Loading the speech model")
    log.info("Speech recognition on the %s: %s", device_name(choice.device), choice.reason)
    started = time.monotonic()
    transcriber = ParakeetTranscriber.load(store.folder(PARAKEET_V2), choice.device)
    log.info("Speech model ready in %.1f s", time.monotonic() - started)
    wanted = current.transcription_device == "gpu"
    if transcriber.device == "cpu" and wanted and (hint := gpu_hint()):
        log.info("%s", hint)
    return transcriber


def load_commands(pack: GamePack) -> CommandMatcher:
    """The game's built-in commands plus the player's own file for that game. A broken
    file is reported and skipped, so a typo never stops Tycho starting."""
    try:
        matcher = CommandMatcher.default(paths.commands_file(pack.id), pack=pack)
    except CommandFileError as e:
        log.warning("Your commands file can't be used, so only the built-in ones are: %s", e)
        return CommandMatcher.default(pack=pack)
    for warning in matcher.warnings:
        log.warning("Your commands file: %s", warning)
    return matcher


def build_session(
    current: Settings,
    binding: Binding,
    store: GameKeys | None,
    transcriber: Transcriber,
    *,
    audio: AudioSource,
    sender: KeySender,
    focus: FocusCheck,
    feedback: Feedback,
    game_state: GameState | None = None,
    action_log: ActionLog | None = None,
    make_brain: Callable[[CommandMatcher], Brain | None] | None = None,
    speaker: Speaker | None = None,
    on_result: Callable[[Result], None] | None = None,
    address: Address | None = None,
    display: Display | None = None,
    camera: Camera | None = None,
    pack: GamePack,
) -> Session:
    """The talking loop, wired the way ``tycho run`` wires it, for ``pack``'s game: its
    commands, words and safety lists, which say whether key actions are off (#191).
    ``store`` is None for a pack with no keybinds part. ``camera``: the game window's,
    for a pack that can look (its ``look`` phrases, #31)."""
    from tycho.games import BUILT_IN
    from tycho.input import Presser
    from tycho.lexicon import Lexicon
    from tycho.ptt.monitor import PushToTalk
    from tycho.runtime import Session
    from tycho.runtime.look import Eyes

    if game_state is None and store is not None and pack.parts.game_state is not None:
        game_state = BUILT_IN.game_state[pack.parts.game_state](store.install.folder)
    matcher = load_commands(pack)
    return Session(
        audio=audio,
        push_to_talk=PushToTalk(binding, input_sources(binding)),
        transcriber=transcriber,
        lexicon=Lexicon.default(pack),
        matcher=matcher,
        keybinds=(lambda: store.keybinds) if store is not None else lambda: NO_KEYBINDS,
        presser=Presser(sender, focus, game=pack.name),
        feedback=feedback,
        game_state=game_state,
        action_log=action_log,
        brain=make_brain(matcher) if make_brain is not None else None,
        speaker=speaker,
        brain_seconds=current.brain_seconds,
        search_seconds=_search_seconds(current.web_search_open)[0],
        search_limit=_search_seconds(current.web_search_open)[1],
        confirm_seconds=current.confirm_seconds,
        log_transcripts=current.keep_transcripts,
        on_result=on_result,
        address=(lambda: address.name) if address is not None else (lambda: ""),
        display=display,
        safety_lists=pack.safety,
        eyes=Eyes(pack.look, camera) if camera is not None and pack.look else None,
        looking=current.look_at_screen is True,
    )


def _search_seconds(open_web: bool) -> tuple[float, float]:
    """How long the brain has once a search starts, and the request's limit. The
    open-web fallback (#354) searches twice and reads pages: it gets longer."""
    from tycho.runtime import asking

    if open_web:
        return asking.OPEN_SEARCH_SECONDS, asking.OPEN_SEARCH_LIMIT
    return asking.SEARCH_SECONDS, asking.SEARCH_LIMIT


NO_KEYBINDS = Keybinds([])
"""A pack with no keybinds part's keys (an advisor, #191): none."""


def download_progress(
    what: str = "the speech model", doing: Callable[[str], None] | None = None
) -> Callable[[str, int, int], None]:
    """One line per file, updated in place, ending when the file is complete; and, with
    ``doing``, the same for the star's tooltip."""
    shown: dict[str, int] = {}

    def show(name: str, done: int, total: int) -> None:
        percent = 100 * done // total if total else 0
        if shown.get(name) == percent:
            return
        shown[name] = percent
        size = f"{done / 1e9:.2f} of {total / 1e9:.2f} GB" if total > 1e8 else ""
        end = "\n" if done >= total else ""
        line = f"  Downloading {what}: {name} {percent:3d}% {size}"
        print("\r" + line, end=end, flush=True)  # \r: overwrite the line in place
        if doing is not None and total > 1e8:  # the big file is the wait
            doing(f"Getting {what} ({total / 1e9:.1f} GB, once): {percent}%")

    return show
