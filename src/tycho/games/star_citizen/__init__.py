"""Star Citizen's code (ADR 19): its install, ``Data.p4k`` and keybind readers, the
keybind store, ``Game.log``, and its look-ups (the Wiki, UEX, ships, the fleet, items,
mining, refining and trade). Its data is the pack in ``packs/star-citizen/``.

Only ``tycho.games`` imports this package (the layout test checks it). Each source is
a boundary (``WikiSource``) with a fake in tests, and each look-up satisfies
``tycho.brain.lookups.Lookup``. Data from outside is untrusted: it only ever shapes
what Tycho says, never what it presses.

Nothing is imported here: import a module (``star_citizen.ship_lookup``), so loading
one part never loads the rest (#411).
"""
