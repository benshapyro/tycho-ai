"""The Tile's cards (#2): a fixed kit that Claude fills, never markup it writes.

Claude picks a kind and supplies the words and numbers, about 50 tokens; the Tile
draws each kind its own way. Generating a layout per answer would add seconds.

What Claude sends is untrusted text. It is cleaned here (no control characters, no
markdown, cut to fit) and is only ever drawn as text, never as markup. Strict tool
schemas can't carry length limits, so the limits live here too.

Some kinds are Tycho's own, never Claude's: the confirm question (from the safety
gate's own words), notices about Tycho itself, and the long-answer reader.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast, get_args

type Kind = Literal[
    "list",  # places or items with a value each, best first
    "route",  # a trade route: buy, sell, and the profit
    "recipe",  # what to gather, with amounts
    "ship",  # a few specs side by side
    "fleet",  # the player's own ships, ranked for a job
    "steps",  # something to do in order
    "headlines",  # dated news with a source
    "warning",  # exact text the player needs, such as the keybinding to set
    "long",  # the long-answer reader: Tycho's last reply, to read
    "confirm",  # a question waiting on the player, with the time left
    "notice",  # something about Tycho that stays true for a while
]
BRAIN_KINDS: tuple[Kind, ...] = (
    "list",
    "route",
    "recipe",
    "ship",
    "fleet",
    "steps",
    "headlines",
    "warning",
)
"""The kinds Claude may show. The rest come from Tycho itself."""
ALL_KINDS: tuple[Kind, ...] = cast(tuple[Kind, ...], get_args(Kind.__value__))

type Tone = Literal["normal", "caution", "danger"]

MAX_ROWS: Mapping[Kind, int] = {"steps": 8, "route": 2}
"""Rows a card may have; others get ``DEFAULT_ROWS``. More is a list to read elsewhere."""
DEFAULT_ROWS = 6
TITLE, LABEL, SOURCE = 40, 28, 40
TEXT, DETAIL, VALUE = 60, 70, 20
BODY = 1200
"""Characters of a card's paragraphs, all told: a long answer is a few paragraphs."""


@dataclass(frozen=True, slots=True)
class Row:
    text: str
    """What the row is: a place, an item, a ship, a step, a headline."""
    detail: str = ""
    """A second line, smaller: where it is, why."""
    value: str = ""
    """The number to glance for, drawn in the number font: a price, an amount, a date."""
    dim: bool = False
    """Shown dimmed: in a fleet pick, a ship that can't do the job."""


@dataclass(frozen=True, slots=True)
class Card:
    kind: Kind
    title: str = ""
    label: str = ""
    """The small line above the title ("Sell · Laranite")."""
    source: str = ""
    """Where it's from ("UEX"), never a claim Tycho can't back."""
    rows: tuple[Row, ...] = ()
    hero: tuple[str, str] | None = None
    """One big figure with its label: a route's profit."""
    body: tuple[str, ...] = ()
    """Paragraphs: a long answer, a warning's or a notice's words."""
    seconds: float = 0.0
    """For a confirm card: how long the question waits."""

    @property
    def tone(self) -> Tone:
        if self.kind == "confirm":
            return "danger"
        if self.kind in ("warning", "notice"):
            return "caution"
        return "normal"

    @property
    def size(self) -> int:
        """Roughly how much there is to read, in characters: longer cards stay longer."""
        parts = [self.title, self.label, *self.body, *(self.hero or ())]
        parts += [f"{r.text} {r.detail} {r.value}" for r in self.rows]
        return sum(len(p) for p in parts)


_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u206f]")
_MARKUP = re.compile(r"[*`#<>]+")


def clean(text: object, limit: int) -> str:
    """One line of plain text from untrusted input, cut to ``limit`` with an ellipsis."""
    if not isinstance(text, str):
        return ""
    line = " ".join(_MARKUP.sub("", _CONTROL.sub(" ", text)).split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def from_tool(arguments: Mapping[str, object]) -> Card:
    """The card in a ``show_card`` call. Raises ``ValueError`` if it can't be shown."""
    kind = arguments.get("kind")
    if kind not in BRAIN_KINDS:
        raise ValueError(f"{kind!r} isn't a card Claude can show")
    rows = tuple(_rows(arguments.get("rows")))
    hero_value = clean(arguments.get("hero_value"), VALUE)
    hero = (clean(arguments.get("hero_label"), LABEL), hero_value) if hero_value else None
    body = tuple(p for p in [clean(arguments.get("text"), BODY)] if p)
    if kind == "route" and len(rows) != 2:
        kind = "list"  # a route is a buy and a sell; anything else reads as a list
    rows = rows[: MAX_ROWS.get(kind, DEFAULT_ROWS)]
    if not rows and not body:
        raise ValueError(f"the {kind} card has nothing on it")
    return Card(
        kind=kind,
        title=clean(arguments.get("title"), TITLE),
        label=clean(arguments.get("label"), LABEL),
        source=clean(arguments.get("source"), SOURCE),
        rows=rows,
        hero=hero,
        body=body,
    )


def _rows(raw: object) -> list[Row]:
    if not isinstance(raw, list):
        return []
    rows: list[Row] = []
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        fields = cast(dict[str, object], item)
        text = clean(fields.get("text"), TEXT)
        if text:
            rows.append(
                Row(
                    text,
                    clean(fields.get("detail"), DETAIL),
                    clean(fields.get("value"), VALUE),
                    fields.get("dim") is True,
                )
            )
    return rows


def long_answer(text: str, title: str = "Last answer") -> Card:
    """Tycho's last reply, to read: "show me that"."""
    return Card("long", title=title, label="Last answer", body=(clean(text, BODY),))


def confirm(question: str, seconds: float) -> Card:
    """The question the safety gate asked, in its own words, with the time to answer."""
    return Card(
        "confirm", label="Waiting on you", body=(clean(question, TEXT * 2),), seconds=seconds
    )


def notice(text: str) -> Card:
    return Card("notice", label="Notice", body=(clean(text, BODY),))


TOOL_SCHEMA: Mapping[str, object] = {
    "type": "object",
    "properties": {
        "say": {
            "type": "string",
            "description": "The spoken line: the answer in a sentence, then where the "
            'rest is ("Two more on the Tile").',
        },
        "kind": {"type": "string", "enum": list(BRAIN_KINDS)},
        "label": {
            "type": "string",
            "description": 'A few words above the title: "Sell · Laranite".',
        },
        "title": {"type": "string", "description": "A few words."},
        "source": {
            "type": "string",
            "description": 'Where the facts are from, if a look-up gave them ("UEX"); '
            "otherwise empty.",
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "detail": {"type": "string"},
                    "value": {"type": "string"},
                    "dim": {"type": "boolean"},
                },
                "required": ["text", "detail", "value", "dim"],
                "additionalProperties": False,
            },
        },
        "hero_label": {"type": "string"},
        "hero_value": {"type": "string"},
        "text": {"type": "string", "description": "For a warning: the exact words to follow."},
    },
    "required": [
        "say",
        "kind",
        "label",
        "title",
        "source",
        "rows",
        "hero_label",
        "hero_value",
        "text",
    ],
    "additionalProperties": False,
}

TOOL_DESCRIPTION = """\
Answer out loud and put the details on the Tile, a small panel over the game. Use it \
only when the eye beats the ear: more than about three items or numbers, exact text \
the player must find or type, or steps to follow. Not for one-line answers, small talk \
or anything you press. Fill the card from look-up results when you have them.
Kinds and their rows (text | detail | value):
- list: places or items, best first (place | where | price). Mark nothing dim.
- route: exactly two rows, buy then sell (place | "Buy" or "Sell" | price per SCU); \
hero_label and hero_value for the profit.
- recipe: what to gather (item | | amount).
- ship: specs (spec name | | value); title the ship, label its role.
- fleet: the player's own ships, best first (ship | why | verdict); dim ones that \
can't do the job.
- steps: one row per step (step | | ), in order.
- headlines: news (headline | | date).
- warning: no rows; text holds the exact words ("Keybindings, search lock all doors, \
give it a key").
Empty strings for what a kind doesn't use. Keep every field short."""
