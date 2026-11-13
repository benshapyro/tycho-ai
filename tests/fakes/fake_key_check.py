"""A key check that answers as scripted. Satisfies ``tycho.desktop.keys.KeyCheck``."""


class FakeKeyCheck:
    def __init__(self, problem: str = "", no_credit: str = "") -> None:
        self.problem = problem
        self.no_credit = no_credit
        self.checked: list[str] = []

    def check(self, name: str, key: str) -> str:
        self.checked.append(name)
        return self.problem

    def credit(self, name: str, key: str) -> str:
        return self.no_credit
