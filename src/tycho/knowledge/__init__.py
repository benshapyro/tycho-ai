"""Knowledge (ADR 15): what the brain can look up before it answers, for any game.

Web search (``web``) and answers kept for a while (``fresh``). A game's own look-ups
live with its code: Star Citizen's are in ``tycho.games.star_citizen``. Data from
outside is untrusted: it only ever shapes what Tycho says, never what it presses.
"""
