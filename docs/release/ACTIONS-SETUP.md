# Setting up the release workflow (once)

`.github/workflows/release.yml` signs and publishes a release from GitHub
Actions, but only after the owner approves it, and only with secrets kept
in a GitHub **environment** named `release`. This is the one-time setup.
How a release then runs: [CONTRIBUTING.md, Releasing](../CONTRIBUTING.md#releasing).

You need: admin on `coders-farm-official/domovoi`, your release key's
private file and passphrase (the Windows update key, [WINDOWS_HOST.md, The
release key](../WINDOWS_HOST.md#the-release-key)), and your Android
keystore with its alias and passwords. None of these ever goes into the
repository, a chat, or a file other than the ones you already keep.

## 1. The environment and its approval

On github.com, in the repository:

1. **Settings → Environments → New environment**, name `release` (exactly),
   **Configure environment**.
2. **Required reviewers**: tick it, add yourself. Leave **Prevent
   self-review** off (you start the run and approve it). **Save protection
   rules**.
3. **Deployment branches and tags**: choose **Selected branches and tags →
   Add deployment branch or tag rule**, ref type *Branch*, pattern `main`,
   **Add rule**. Runs on any other branch can't reach the environment.
4. Leave **Wait timer** at 0. Don't add any of these secrets as repository
   secrets (Settings → Secrets and variables → Actions → *Repository
   secrets*): only as environment secrets of `release`, below.

## 2. The secrets

Six environment secrets of `release`. Add them with the GitHub CLI from
**Git Bash** (each reads the value from the file or from a hidden prompt,
so it is never shown or written anywhere), or on the web (**Settings →
Environments → release → Environment secrets → Add environment secret**,
pasting the value; clear the clipboard afterwards, and mind Windows'
clipboard history).

| secret | what |
|---|---|
| `RELEASE_SIGNING_KEY` | the release key's private file, all of its text (an OpenSSH ed25519 key, passphrase-protected) |
| `RELEASE_SIGNING_KEY_PASSPHRASE` | that key's passphrase |
| `ANDROID_KEYSTORE_B64` | the Android keystore file, base64 |
| `ANDROID_KEYSTORE_PASSWORD` | the keystore's password |
| `ANDROID_KEY_ALIAS` | the key's alias (`domovoi` unless you chose another) |
| `ANDROID_KEY_PASSWORD` | the key's own password; set it to the keystore's password when they are the same |

From Git Bash, in any folder (adjust the two paths if yours differ; these
are the defaults the docs and the scripts use):

```bash
R="--env release --repo coders-farm-official/domovoi"
gh secret set RELEASE_SIGNING_KEY $R < ~/.domovoi-release/domovoi-release
gh secret set RELEASE_SIGNING_KEY_PASSPHRASE $R      # prompts, hidden
base64 -w0 ~/domovoi-release.jks | gh secret set ANDROID_KEYSTORE_B64 $R
gh secret set ANDROID_KEYSTORE_PASSWORD $R           # prompts, hidden
gh secret set ANDROID_KEY_ALIAS $R                   # prompts
gh secret set ANDROID_KEY_PASSWORD $R                # prompts, hidden
gh secret list $R                                    # names and dates only
```

The same values from PowerShell, for the web form (each goes to the
clipboard, never the screen):

```powershell
Get-Content -Raw $HOME\.domovoi-release\domovoi-release | Set-Clipboard
[Convert]::ToBase64String([IO.File]::ReadAllBytes("$HOME\domovoi-release.jks")) | Set-Clipboard
Set-Clipboard -Value ' '   # afterwards
```

The key file must have plain LF line endings, as `ssh-keygen` writes it;
a copy that went through an editor with CRLF may not load.

There is no dry run for the secrets; the first release checks them, and a
wrong one stops it before anything is pushed: right after your approval
the job loads the release key into a throwaway ssh-agent and compares it
with `windows/installer/release-signing.pub`, and a wrong keystore
password or alias fails `apksigner`, which signs before `release.ps1`
pushes. Fix the secret and run the workflow again.

## 3. What else the workflow needs from the repository

- **Settings → Actions → General → Workflow permissions**: nothing to
  change. The default *Read repository contents* is fine: the workflow asks
  for `contents: write` itself, in the one job that tags, publishes and
  pushes, and nothing else.
- **Actions permissions**: if you ever restrict which actions may run,
  allow `actions/checkout` (the only action the workflow uses, pinned by
  commit SHA).
- **Pushes by the workflow.** It pushes with the run's own `GITHUB_TOKEN`
  (the `github-actions[bot]` account): the tag `v<version>`, `stable`
  (fast-forward only, never forced) and, when the Android app changed, the
  `android: release <v> [skip ci]` commit on `main` with its tag. Today
  nothing stops that: `main` and `stable` have no branch protection, and
  the ruleset *Main* (no deletion, no force-push, pull requests required
  on the default branch) is **disabled**. Enabling it as it is would
  refuse the app path's push to `main` (a pull request is required); that
  push comes before anything is published, so a run would stop cleanly,
  but app releases would then need the local scripts. The deletion and
  force-push rules alone are harmless: the workflow never does either. A
  tag ruleset or a rule on `stable`, if you add one, must let
  `github-actions[bot]` create tags and fast-forward `stable`.
- A push made with `GITHUB_TOKEN` starts no workflow, so the release commit
  can't trigger CI or another release.

## 4. Rotating or removing

Replace a secret by setting it again (same command). To stop releases from
Actions, delete the `release` environment: the job can't start without it,
and the local scripts keep working. If a secret may have leaked, rotate it
where it lives (a new release key needs a Setup run by hand on each Windows
PC; the Android key can't change without phones refusing the update) and
remove the environment until then.
