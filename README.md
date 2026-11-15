# just-release

Keep the tools you install from GitHub Releases up to date, without doing it by hand.

Plenty of good software never reaches your distribution's package manager. You download a zip from a project's Releases page, unpack it somewhere, and then forget about it until it is a year out of date. just-release is a small Linux desktop app that takes over that chore. You tell it which repositories you care about and where each tool lives on disk. It watches for new releases and installs them for you.

It is written in Python with GTK 4 and Libadwaita, so it looks at home on a GNOME desktop.

## What it does

- **Tracks repositories.** Add any GitHub repository and see its latest release, tag and downloadable files.
- **Picks the right file.** Most releases ship a dozen files for different platforms. Click the one you want and just-release works out a pattern (such as `ghidra_*_PUBLIC_*.zip`) that will match the same file in future releases. You can also write the pattern yourself, as a glob or a regular expression.
- **Installs and updates.** One click downloads the file, checks it against the SHA-256 digest when GitHub publishes one, unpacks it and puts it in place.
- **Leaves your files alone.** You choose how each tool is updated, so plugins and configuration you added survive an update. See [Update strategies](#update-strategies).
- **Runs your scripts.** Shell hooks can run before and after each update, for example to stop a running program or refresh a symlink.
- **Checks on its own.** While the app is open it looks for new releases every few hours and can send a desktop notification when it finds one.
- **Works with private repositories.** Add a GitHub token in Settings to track private repositories. A token also raises the API limit from 60 to 5,000 requests an hour.

Everything is stored in one YAML file that you can read, edit and keep in your dotfiles.

## Getting started

### 1. Install the system libraries

GTK and Libadwaita come from your distribution, not from `pip`. You need Python 3.10 or newer and Libadwaita 1.5 or newer.

Debian or Ubuntu:

```bash
sudo apt update
sudo apt install -y \
    python3-dev \
    libgirepository1.0-dev \
    libcairo2-dev \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    xdg-utils
```

Fedora:

```bash
sudo dnf install -y \
    python3-devel \
    gobject-introspection-devel \
    cairo-gobject-devel \
    gtk4 \
    libadwaita \
    xdg-utils
```

Arch Linux:

```bash
sudo pacman -S --needed \
    python \
    gobject-introspection \
    cairo \
    gtk4 \
    libadwaita \
    xdg-utils
```

### 2. Install the Python packages

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If `pip` fails while building PyGObject on Debian or Ubuntu, install `libgirepository-2.0-dev` and try again. Recent PyGObject versions need it.

You can also skip `pip` and use the packages your distribution already provides (`python3-gi` and `python3-yaml` on Debian or Ubuntu). In that case, create the environment with `python3 -m venv --system-site-packages .venv`.

### 3. Run it

```bash
python main.py
```

The first run creates `~/.config/just-release/releases.yaml` with two example tools, Ghidra and ripgrep, so you can see how things fit together. Nothing is installed until you click **Install**. Edit or remove the examples as you like.

To keep your manifest somewhere else, pass its path:

```bash
python main.py --manifest /path/to/releases.yaml
```

`-m` is the short form. You can also change the path later in Settings.

## Tracking a tool

1. Click **+ Track Repo**.
2. Enter the repository as `owner/repo`. Pasting a GitHub URL works too.
3. Click **Fetch Assets** to list the files in the latest release, then **Select** the one for your machine.
4. Choose the directory to install into and an update strategy.
5. Click **Track Tool**.

The tool now appears in the main window. When a release is available that you do not have, an **Install** or **Update** button appears next to it.

## Update strategies

Each tool has a strategy that decides what happens to the files already in its install directory.

| Strategy | Value | What it does | Good for |
|---|---|---|---|
| Smart In-Place Sync | `smart-sync` | Unpacks the release over the install directory. Files from the release replace the old ones. Anything else you put there is left untouched. | Tools you extend with plugins or local configuration, such as Ghidra or an IDE. |
| Symlink Strategy | `symlink` | Puts each release in its own folder, `versions/<tag>/`, and points a `current` symlink at the newest one. Older versions stay on disk. | Tools where you want a clean switch between versions, or the option to go back to an older one. |
| Clean Wipe | `wipe` | Deletes everything in the install directory, then unpacks the release. | Self-contained tools that should match the release exactly, such as ripgrep. |

A few details apply to every strategy:

- `.zip` and `.tar.*` archives are unpacked. Any other file is treated as a single program and made executable.
- Many archives wrap everything in one versioned folder, such as `ripgrep-15.2.0-x86_64-unknown-linux-musl/`. just-release installs the contents of that folder, not the folder itself, so every release lands on the same paths.
- Each install directory gets a small `.just-release.json` file recording what was installed and when.

### A word of caution about Clean Wipe

Clean Wipe deletes **everything** in the install directory, so always give those tools a directory of their own. Never point one at a shared location such as `~/.local/bin`.

There are two safeguards. just-release asks you to confirm before it clears a directory it did not install into, and it refuses outright to wipe your home directory or anything above it.

## Hooks

A hook is a shell command that runs as part of an update. Each tool can have two:

- `pre_update` runs after the download, just before any installed file is touched. If it fails, the update is cancelled and nothing is changed.
- `post_update` runs once the new files are in place. If it fails, the update still counts as done and you are shown the error as a warning.

Hooks run through `/bin/sh` from the install directory. These variables are available to them:

| Variable | Contents |
|---|---|
| `JUST_RELEASE_TOOL` | The tool's key in the manifest, such as `ripgrep`. |
| `JUST_RELEASE_NEW_VERSION` | The tag being installed. |
| `JUST_RELEASE_OLD_VERSION` | The tag being replaced. Empty on a first install. |
| `JUST_RELEASE_INSTALL_DIR` | The full path of the install directory. |

For example, to stop a program before it is updated:

```bash
pkill -f myapp || true
```

And to put the new version on your `PATH` afterwards:

```bash
ln -sf "$JUST_RELEASE_INSTALL_DIR/current/myapp" "$HOME/.local/bin/myapp"
```

## The manifest

The app writes the manifest for you, but it is plain YAML and you are welcome to edit it by hand. It lives at `~/.config/just-release/releases.yaml` unless you chose another path.

```yaml
version: 1
settings:
  poll_interval_hours: 4
  desktop_notifications: true
  github_token: ""

tools:
  ghidra:
    name: "Ghidra"
    repo: "NationalSecurityAgency/ghidra"
    install_to: "/home/user/security/ghidra"
    asset_pattern: "ghidra_*_PUBLIC_*.zip"
    strategy: "smart-sync"
    current_version: "Ghidra_11.2.1_build"
    latest_version: "Ghidra_11.3.1_build"
    last_checked: "2026-09-15 19:12:16"
    hooks:
      pre_update: "pkill -f ghidraRun || true"
      post_update: "echo \"Ghidra updated to $JUST_RELEASE_NEW_VERSION\""

  ripgrep:
    name: "ripgrep"
    repo: "BurntSushi/ripgrep"
    install_to: "/home/user/.local/opt/ripgrep"
    asset_pattern: "*x86_64*linux*musl*.tar.gz"
    strategy: "wipe"
    current_version: "14.1.0"
    latest_version: "14.1.0"
    last_checked: "2026-09-15 19:12:17"
    hooks:
      pre_update: ""
      post_update: "ln -sf \"$JUST_RELEASE_INSTALL_DIR/rg\" \"$HOME/.local/bin/rg\""
```

### Settings

| Key | Meaning |
|---|---|
| `poll_interval_hours` | How often to check for new releases while the app is open, from 1 to 72 hours. |
| `desktop_notifications` | Whether to send a desktop notification when a release is found or installed. |
| `github_token` | An optional GitHub personal access token. |

### Tools

Each entry under `tools` has a short key of your choosing (`ghidra` and `ripgrep` above) and these fields:

| Key | Meaning |
|---|---|
| `name` | The name shown in the app. |
| `repo` | The GitHub repository, as `owner/repo`. |
| `install_to` | The directory the tool is installed into. |
| `asset_pattern` | A glob or regular expression that picks the file to download from each release. |
| `strategy` | `smart-sync`, `symlink` or `wipe`. |
| `current_version` | The tag you have installed. The app fills this in. |
| `latest_version` | The newest tag seen on GitHub. The app fills this in. |
| `last_checked` | When the app last checked the repository. The app fills this in. |
| `hooks.pre_update` | Shell command to run before an update. |
| `hooks.post_update` | Shell command to run after an update. |

Two things to know before you edit by hand:

- The app rewrites the whole file when it saves, so comments you add will be lost.
- The file can contain your GitHub token, so the app keeps it readable only by you. Remove the token before sharing the file or committing it to a public repository.

If the manifest cannot be read, for instance after a typo, the app does not overwrite it. It sets the file aside as `releases.yaml.broken-<date>` and tells you.

## Good to know

- Release checks only happen while the app is open. There is no background service yet.
- Only the latest full release of a repository is considered. Pre-releases are ignored.
- Closing the window is blocked while an install is running, so an update is never cut off halfway.

## Development

The code is split so that everything apart from the interface can be tested without GTK:

```
.
├── main.py                     # Command-line entry point
├── requirements.txt
├── just_release/
│   ├── config.py               # Reads and writes the manifest
│   ├── github.py               # GitHub API, downloads and asset matching
│   ├── installer.py            # Unpacking, update strategies and hooks
│   └── ui/
│       ├── app.py              # Application setup
│       ├── main_window.py      # Main window and the list of tools
│       ├── settings_dialog.py  # Settings
│       └── track_dialog.py     # Adding and editing a tool
└── tests/                      # Unit tests
```

Run the tests with:

```bash
python -m unittest discover -s tests -t .
```

## License

This project is licensed under the MIT License.
