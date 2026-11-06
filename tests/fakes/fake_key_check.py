"""A key check that answers as scripted. Satisfies ``tycho.desktop.keys.KeyCheck``."""


class FakeKeyCheck:
    def __init__(self, problem: str = "") -> None:
        self.problem = problem
        self.checked: list[str] = []

    def check(self, name: str, key: str) -> str:
        self.checked.append(name)
        return self.problem
