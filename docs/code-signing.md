# Code signing

Tycho's releases are to be signed through [SignPath Foundation](https://signpath.org), which gives open-source projects a free code signing certificate (#9). The certificate is issued to SignPath Foundation, not to Tycho, so Windows shows "SignPath Foundation" as the publisher. The public policy, with the team roles, is the README's [Code signing policy](../README.md#code-signing-policy); SignPath's conditions are its [terms](https://signpath.org/terms).

Until SignPath accepts Tycho, nothing here runs: every build is unsigned, as before.

## What is signed, and how

Only a release build is signed: the Build workflow started by hand on `main` with **sign** ticked. In that run (`.github/workflows/build.yml`):

1. PyInstaller builds `dist/tycho`, and the usual checks run.
2. `tycho.exe` and `tycho-app.exe` go to SignPath as a GitHub artifact (artifact configuration `exes`). SignPath checks they came from this repository's workflow on a GitHub-hosted runner, and that their product name is `Tycho` and their product version is this build's version. The maintainer approves the request in SignPath; the signed programs replace the unsigned ones.
3. Inno Setup builds `TychoSetup-<version>.exe` from the signed programs.
4. The installer goes to SignPath the same way (artifact configuration `installer`), and is approved again.
5. The signed installer is the run's `tycho-installer` artifact, which the release takes.

Third-party DLLs and Python modules inside the installer aren't signed by Tycho: SignPath Foundation signs a project's own files only. Inno Setup's uninstaller (`unins000.exe`) is also unsigned for now: Inno Setup signs it during compile, which needs a signing tool on the runner.

The two artifact configurations are in `.signpath/artifact-configurations/`; SignPath keeps its own copy, so a change here must be pasted into the SignPath project too. The product name and version on the programs come from `packaging/version_info.py`, and on the installer from `packaging/tycho.iss`.

## Settings the workflow needs

All in the repository's Settings, under Secrets and variables, Actions:

| Name | Kind | Value |
|---|---|---|
| `SIGNPATH_API_TOKEN` | Secret | The API token of a SignPath user with submitter rights on the project (a CI user, not the maintainer's own login) |
| `SIGNPATH_ORGANIZATION_ID` | Variable | The organization ID SignPath shows in its settings |
| `SIGNPATH_PROJECT_SLUG` | Variable, optional | The project's slug, if it isn't `tycho` |
| `SIGNPATH_POLICY_SLUG` | Variable, optional | The signing policy's slug, if it isn't `release-signing` |

Without `SIGNPATH_ORGANIZATION_ID`, a run with **sign** ticked stops at once and says why.

## Setting up SignPath, once accepted

1. In SignPath, add the predefined trusted build system **GitHub.com** to the organization and link it to the Tycho project. Install the SignPath GitHub App on this repository if SignPath asks.
2. Create the artifact configurations `exes` and `installer` from the two files in `.signpath/artifact-configurations/`.
3. Check the signing policy's slug (SignPath Foundation usually sets up `release-signing`) and that approval is required.
4. Create a CI user with submitter rights, and store its API token as the `SIGNPATH_API_TOKEN` secret. Add `SIGNPATH_ORGANIZATION_ID` as a variable.
5. Run Build by hand on `main` with **sign** ticked, approve both requests, and check the installer: its Properties, Digital Signatures tab, shows SignPath Foundation.

Every SignPath and GitHub account on the team must use multi-factor authentication (SignPath's terms).

## A signed release

The `release` skill covers it: after the version bump merges, start Build on `main` with **sign** ticked (`gh workflow run build.yml --ref main -f sign=true`), approve the two requests in SignPath, and take that run's installer instead of the one from the merge commit's own run.
