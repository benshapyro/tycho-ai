"""Brain traces: one JSON line per brain request, for Claude sessions to read (#282).

What the brain did and why, in a file an agent can ``grep`` and ``jq``: the request,
each call to the model (its tool calls, time to the first token, tokens and cost),
each look-up (its time and size, never its result), and how the request ended. Play
and the eval write the same lines, so ``tools/bench/traces.py`` can compare them.

Off for players. ``TYCHO_TRACE=1`` turns it on, into ``traces.jsonl`` in the logs
folder; the eval always writes its own. A line never holds a key (it passes through
``logs.redact``) or an absolute path (the home folder and Tycho's own folders become
``~``, ``<config>`` and ``<data>``), and the briefing appears only as a hash.

The adapters only report what their stream did; ``rounds`` records the tool calls and
look-ups and writes the line, so each rule is written once.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import logging.handlers
import os
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import cast

from tycho import __version__, logs, paths
from tycho.brain.decisions import Decision, Reply, RunActions, RunSequence

log = logging.getLogger(__name__)

ENV = "TYCHO_TRACE"
FILE = "traces.jsonl"
MAX_BYTES = 5_000_000
BACKUPS = 3
ARGUMENT_CHARS = 2000
"""A tool call's arguments are kept up to this long: a card can be long."""


def enabled() -> bool:
    """Whether ``TYCHO_TRACE`` asks for traces ("1", "true", "yes", "on")."""
    return os.environ.get(ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def briefing_hash(text: str) -> str:
    """Which briefing a request was sent with, without the briefing itself."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass
class Look:
    """One look-up the brain started."""

    name: str
    at_ms: float = 0.0
    """When it started, from the request's start."""
    ms: float | None = None
    """When its result came, from the request's start; None if it hadn't when the
    request ended (an act ended the reply first, and it was stopped)."""
    took_ms: float | None = None
    chars: int = 0
    """The size of its result, which went back to the model."""
    error: bool = False
    found: bool = False
    cancelled: bool = False
    """Stopped because the reply ended in an act first."""


@dataclass
class Call:
    """One call to the model."""

    at_ms: float
    """When it started, from the request's start."""
    first_ms: float | None = None
    """When the first piece of its stream arrived."""
    tools: list[dict[str, object]] = field(default_factory=list[dict[str, object]])
    """Each tool call, as it was complete: name, arguments and when."""
    stop: str = ""
    """Why the model stopped ("end_turn", "tool_use"), or "act" when Tycho left the
    stream at an act, so the rest never arrived."""
    tokens: dict[str, int] = field(default_factory=dict[str, int])
    """The call's tokens. A call left at an act has no "output": the count only comes
    at the end of the stream, which Tycho never reads (#387)."""
    output_chars: int | None = None
    """How much the model wrote before Tycho left the stream at an act: its text and
    each tool call's arguments, in characters. The act's output tokens never arrive,
    so this is how long that output was (#387)."""
    cost: float | None = None
    priced: str = ""
    """Where ``cost`` came from: "reported" by OpenRouter, worked out from its "price
    list", or Tycho's own "estimate" from Anthropic's prices; empty with no cost (#317)."""
    host: str = ""
    """Who served it, through OpenRouter."""
    generation: str = ""
    """OpenRouter's id for it, to look its cost up by when it ended at an act."""


@dataclass
class Trace:
    """What one brain request did, built up as it runs."""

    provider: str
    model: str
    briefing: str
    text: str
    recent: int = 0
    """Earlier exchanges sent with it, for follow-ups."""
    run: str = ""
    case: str = ""
    source: str = "play"
    time: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )
    calls: list[Call] = field(default_factory=list[Call])
    lookups: list[Look] = field(default_factory=list[Look])
    outcome: str = ""
    """"actions", "sequence", "reply", "card" or "error"."""
    decided: dict[str, object] = field(default_factory=dict[str, object])
    """What it decided: the actions or sequence, or the reply's words and card kind."""
    stopped: str = ""
    """Why the request ended."""
    ms: float = 0.0
    started: float = field(default_factory=perf_counter, repr=False)

    def elapsed(self) -> float:
        return round((perf_counter() - self.started) * 1000, 1)

    def call(self) -> None:
        """A call to the model starts."""
        self.calls.append(Call(self.elapsed()))

    def streaming(self) -> None:
        """A piece of the current call's stream arrived."""
        if self.calls and self.calls[-1].first_ms is None:
            self.calls[-1].first_ms = self.elapsed()

    def tool(self, name: str, arguments: object) -> None:
        """A tool call in the current call was complete."""
        if not self.calls:
            self.call()
        # Cleaned here, while it's plain text: once nested in the line its quotes are
        # escaped, so a key pattern could eat a backslash and a path is escaped twice (#304).
        shown = _clean(json.dumps(arguments, ensure_ascii=False, default=str))
        if len(shown) > ARGUMENT_CHARS:
            shown = shown[:ARGUMENT_CHARS] + "...(cut)"
        self.calls[-1].tools.append({"name": name, "arguments": shown, "ms": self.elapsed()})

    def used(
        self,
        tokens: Mapping[str, int],
        cost: float | None,
        stop: str = "",
        host: str = "",
        priced: str = "",
    ) -> None:
        """The current call's usage, when it finished with a whole reply."""
        if not self.calls:
            return
        last = self.calls[-1]
        last.tokens, last.cost, last.host = dict(tokens), cost, host or last.host
        last.priced = priced if cost is not None else ""
        last.stop = stop or last.stop

    def left(self, output_chars: int, tokens: Mapping[str, int] | None = None) -> None:
        """Tycho left the current call's stream at an act: how much the model had
        written by then, and the tokens known so far (Claude reports the input and
        cache tokens as the call starts)."""
        if self.calls:
            last = self.calls[-1]
            last.output_chars = output_chars
            if tokens:
                last.tokens = dict(tokens)

    def served(self, host: str, generation: str = "") -> None:
        """Who served the current call, and its id, when it ended at an act."""
        if self.calls:
            self.calls[-1].host = host or self.calls[-1].host
            self.calls[-1].generation = generation or self.calls[-1].generation

    def looked(self, look: Look) -> None:
        self.lookups.append(look)

    def ended(self, decided: Decision, stopped: str = "") -> None:
        """The request ended in ``decided``. ``stopped`` says why, when the decision
        alone doesn't (an act refused after a web search, a reply cut short)."""
        self.ms = self.elapsed()
        if self.calls and not self.calls[-1].stop:
            self.calls[-1].stop = "act"  # the stream was left at the act
        match decided:
            case RunActions(actions=actions, say=say):
                self.outcome = "actions"
                self.decided = {"actions": list(actions), "say": say}
            case RunSequence(name=name, say=say):
                self.outcome = "sequence"
                self.decided = {"sequence": name, "say": say}
            case Reply(text=text, card=card):
                self.outcome = "card" if card is not None else "reply"
                self.decided = {"say": text}
                if card is not None:
                    self.decided["card"] = card.kind
        if stopped:
            self.stopped = stopped
        elif self.outcome == "reply":
            self.stopped = f"answered ({self.calls[-1].stop})" if self.calls else "answered"
        else:
            self.stopped = "act"

    def failed(self, error: BaseException) -> None:
        self.ms = self.elapsed()
        self.outcome = "error"
        if isinstance(error, asyncio.CancelledError):
            self.stopped = "cancelled"
        else:
            self.stopped = f"{type(error).__name__}: {error}"[:300]

    def line(self) -> str:
        """The trace as one JSON line, with no key and no absolute path."""
        data = asdict(self)
        data.pop("started")
        data = {"version": __version__, **data}
        if not self.case:
            data.pop("case")
        return json.dumps(_cleaned(data), ensure_ascii=False, default=str)


class TraceFile:
    """Where traces go: a rotating JSON-lines file, written on a thread of its own so
    the event loop never waits for the disk, and in the order the requests ended."""

    def __init__(self, path: Path, *, source: str = "play", run: str = "") -> None:
        self.path = path
        self.source = source
        self.run = run or datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        """Which session or eval run a line came from, so one file can hold several."""
        self.case = ""
        """The eval case being run, written on each line (the eval sets it)."""
        self._writer = ThreadPoolExecutor(1, thread_name_prefix="tycho-traces")
        self._handler: logging.handlers.RotatingFileHandler | None = None

    @classmethod
    def from_env(cls) -> TraceFile | None:
        """``traces.jsonl`` in the logs folder when ``TYCHO_TRACE`` is on, else None."""
        if not enabled():
            return None
        return cls(paths.logs_dir() / FILE)

    def start(self, provider: str, model: str, briefing: str, text: str, recent: int) -> Trace:
        return Trace(
            provider,
            model,
            briefing,
            text,
            recent,
            run=self.run,
            case=self.case,
            source=self.source,
        )

    def write(self, trace: Trace) -> None:
        """Queue the trace's line. Never raises: a trace must never break a request."""
        try:
            line = trace.line()
            self._writer.submit(self._append, line)
        except Exception:
            log.exception("Couldn't write a brain trace")

    def _append(self, line: str) -> None:
        try:
            if self._handler is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._handler = logging.handlers.RotatingFileHandler(
                    self.path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
                )
                self._handler.setFormatter(logging.Formatter("%(message)s"))
            self._handler.emit(logging.makeLogRecord({"msg": line, "levelno": logging.INFO}))
        except Exception:
            log.exception("Couldn't write a brain trace")

    def close(self) -> None:
        """Write what's queued and close the file."""
        self._writer.shutdown(wait=True)
        if self._handler is not None:
            with contextlib.suppress(Exception):
                self._handler.close()


def _clean(text: str) -> str:
    """No key and no absolute path in ``text``."""
    return _relative(logs.redact(text))


def _cleaned(value: object) -> object:
    """Every text in ``value`` cleaned before it's written as JSON, so the cleaning never
    sees JSON's escapes and can't break the line (#304)."""
    match value:
        case str():
            return _clean(value)
        case dict():
            items = cast(dict[object, object], value).items()
            return {_cleaned(k): _cleaned(v) for k, v in items}
        case list() | tuple():
            return [_cleaned(v) for v in cast(list[object], value)]
        case _:
            return value


def _relative(text: str) -> str:
    """Tycho's folders and the home folder by name, never as absolute paths. The
    longest first, so the data folder isn't written as ``~/AppData/...``."""
    places = {
        "<config>": paths.config_dir(),
        "<data>": paths.data_dir(),
        "~": Path.home(),
    }
    found: list[tuple[str, str]] = []
    for name, place in places.items():
        for form in {str(place), place.as_posix()}:
            if len(form) > 3:
                found.append((form, name))
                found.append((json.dumps(form)[1:-1], name))  # as it's escaped in JSON
    for form, name in sorted(set(found), key=lambda f: len(f[0]), reverse=True):
        text = text.replace(form, name)
    return text
