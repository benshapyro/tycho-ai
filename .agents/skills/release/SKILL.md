---
name: release
description: Publish a new Tycho version. Bumps the version in a PR, takes the installer CI built from main, checks it, drafts the GitHub release with notes and a SHA-256, and publishes once the maintainer agrees. Use when working in the Tycho repository and asked to release, ship, tag or publish a version, or to make an installer available to players.
---

# Release Tycho

Players get Tycho from GitHub Releases. The `tycho-setup` skill installs the latest one, and Tycho's update check compares against it. Nothing is published without the maintainer's explicit OK: a published release reaches every player who runs the update check.

## 1. Choose the version

- **Which number:** while Tycho is 0.x, raise the second number for new features (0.3.0 to 0.4.0) and the third for fixes only (0.3.0 to 0.3.1). Ask the maintainer if it's unclear.
- **The only place it's written** is `pyproject.toml`; `tycho --version` reads it from the package metadata.
- **Check what's already out:** `gh release list` and `git tag --list`.

## 2. Bump it in a PR

```
git checkout main && git pull
git checkout -b release/<version>
uv version <version> --no-sync      # updates pyproject.toml and uv.lock
```

In the same PR, move everything under **Unreleased** in `CHANGELOG.md` to a new `## [<version>] - <YYYY-MM-DD>` section, leave **Unreleased** empty, and update the compare links at the bottom.

Commit (`chore(release): <version>`), push, open a PR, wait for CI and the Claude review, and merge when the maintainer says so.

## 3. Take the installer CI built from main

The **Build** workflow (`.github/workflows/build.yml`) builds `TychoSetup-<version>.exe` on every push to `main`. Before it builds the installer, it smoke-tests the frozen app and checks the third-party notices.

```
gh run list --branch main --workflow build.yml --limit 1      # wait for success on the merge commit
gh run download <run id> -n tycho-installer -D <empty folder>
sha256sum <folder>/TychoSetup-<version>.exe
```

The file name must carry the new version. Use the installer from the merge commit's own run: its commit is the one the tag will point to.

## 4. Draft the release

Start from the version's section in `CHANGELOG.md`: it lists what changed, in players' words, with links. If something merged since the last release is missing from it, add it there too. The notes then expand it for a reader who has never heard of Tycho.

Write the notes for players, not developers. Assume the reader has never heard of Tycho and isn't technical: plain words, short sections, and a concrete example for each change (what you say, and what Tycho does). Explain any term (the Tile, push-to-talk) the first time it appears.

- what's new since the last release, by what a player notices, most noticeable first; fixes and work behind the scenes go last, in a line or two;
- how to install (link the README's Install section; SmartScreen's **More info**, then **Run anyway**, while the installer is unsigned);
- known limits;
- the SHA-256 line, `SHA-256 of \`TychoSetup-<version>.exe\`: \`<hash>\``, which the `tycho-setup` skill checks against;
- the unofficial-fan-project notice from `NOTICE`.

Also, in the release PR, move anything this release ships from the README's **Coming** list to **Works today**, and add new plans to **Coming** with their issue links.

Before the release PR, check the open issues labelled `from-review` (`gh issue list --label from-review`): review findings that weren't blocking when their PR merged. Fix them, or ask the maintainer which may wait for the next release.

`git log --oneline <last tag>..main` lists what changed, to check the changelog against. Then create the release as a draft:

```
gh release create v<version> <folder>/TychoSetup-<version>.exe --draft --target <merge commit sha> --title "Tycho <version>" --notes-file notes.md
```

Give the maintainer the draft's link, and ask whether to publish.

## 5. Publish, and check

Only when the maintainer agrees:

```
gh release edit <draft tag from the link> --draft=false --latest
git fetch --tags && git rev-parse v<version>     # the tag is on the merge commit
uv run python -c "from tycho import updates; print(updates.latest_release())"
```

The last line must print the new version: that's what installed copies of Tycho will see.
