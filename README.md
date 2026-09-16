# just-release

A native desktop application for tracking, downloading, and orchestrating software installations from GitHub Releases.

Built for Linux using GTK 4 and Libadwaita, `just-release` provides a dashboard to monitor upstream repositories, inspect release assets, match files with glob patterns, and automate deployment strategies.

## Features

- Native GTK 4 and Libadwaita user interface.
- Upstream GitHub Release inspection for tags, release notes, and assets.
- Asset pattern matching using glob patterns or regular expressions.
- Automated asset pattern generation based on selected release filenames.
- Three installation and update strategies:
  - Smart In-Place Sync (`smart-sync`): Updates binaries while preserving local configuration files and custom scripts.
  - Symlink Strategy (`symlink`): Installs each release into versioned directories (`versions/<tag>/`) and points a `current` symlink to the active version.
  - Clean Wipe (`wipe`): Removes destination contents prior to extracting a fresh release.
- Lifecycle hooks (`pre_update`, `post_update`) supporting custom shell scripts.
- Declarative YAML manifest for managing tracked repositories and settings.
- Optional GitHub Personal Access Token (PAT) support for private repositories and elevated API rate limits.
- Background check interval and desktop notification settings.
- Direct folder navigation via `xdg-open`.

## System Requirements

- Linux desktop environment
- Python 3.10 or newer
- GTK 4 and Libadwaita 1.0+ runtime and development libraries
- PyGObject dependencies (Cairo and GObject introspection)
- `xdg-utils` (for opening directories in the file manager)

### System Packages

PyGObject and GTK 4 require native system libraries and GObject Introspection bindings. Install them using your distribution's package manager:

#### Debian / Ubuntu

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

#### Fedora

```bash
sudo dnf install -y \
    python3-devel \
    gobject-introspection-devel \
    cairo-gobject-devel \
    gtk4 \
    libadwaita \
    xdg-utils
```

#### Arch Linux

```bash
sudo pacman -S --needed \
    python \
    gobject-introspection \
    cairo \
    gtk4 \
    libadwaita \
    xdg-utils
```

## Installation

Activate the virtual environment and install the required Python packages from `requirements.txt`:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Note: PyGObject interfaces with system-level GTK 4 and Libadwaita introspection libraries. If using an existing virtual environment, ensure `include-system-site-packages = true` is set in your `.venv/pyvenv.cfg`.

## Running the Application

With the virtual environment active:

```bash
source .venv/bin/activate
python main.py
```

### Specifying a Custom Manifest

By default, configurations are saved to `~/.config/just-release/releases.yaml`. To load a custom manifest:

```bash
python main.py --manifest /path/to/releases.yaml
```

or with the short flag:

```bash
python main.py -m /path/to/releases.yaml
```

### Command-Line Usage

```
usage: main.py [-h] [--manifest MANIFEST]

just-release: Autonomous GitHub Release Orchestrator

options:
  -h, --help            show this help message and exit
  --manifest MANIFEST, -m MANIFEST
                        Path to releases manifest file (YAML)
```

## Manifest Configuration

The configuration file is written in YAML format. If missing at startup, a default manifest is generated automatically.

Default path: `~/.config/just-release/releases.yaml`

### Example Manifest

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
    last_checked: "2026-09-15"
    hooks:
      pre_update: "pkill -f ghidraRun || true"
      post_update: "echo 'Ghidra updated to' $JUST_RELEASE_NEW_VERSION"

  ripgrep:
    name: "ripgrep"
    repo: "BurntSushi/ripgrep"
    install_to: "/home/user/.local/bin"
    asset_pattern: "*x86_64*linux*musl*.tar.gz"
    strategy: "wipe"
    current_version: "14.1.0"
    latest_version: "14.1.0"
    last_checked: "2026-09-15"
    hooks:
      pre_update: ""
      post_update: ""
```

### Configuration Reference

| Key | Type | Description |
|---|---|---|
| `settings.poll_interval_hours` | Integer | Interval in hours between automated release checks (1 to 72). |
| `settings.desktop_notifications` | Boolean | Enable or disable desktop notifications when updates are found. |
| `settings.github_token` | String | GitHub Personal Access Token (optional). Raises API rate limit from 60 to 5,000 requests/hour and enables private repository tracking. |
| `tools.<id>.name` | String | Human-readable name for the tool. |
| `tools.<id>.repo` | String | GitHub repository in `owner/repo` format. |
| `tools.<id>.install_to` | String | Target directory where the tool is unpacked or linked. |
| `tools.<id>.asset_pattern` | String | Glob pattern or regular expression matching release archive names. |
| `tools.<id>.strategy` | String | Update mechanism: `smart-sync`, `symlink`, or `wipe`. |
| `tools.<id>.current_version` | String | Currently installed version tag. |
| `tools.<id>.latest_version` | String | Most recent upstream tag detected. |
| `tools.<id>.hooks.pre_update` | String | Shell command to execute before the update process. |
| `tools.<id>.hooks.post_update` | String | Shell command to execute after the update process finishes. |

## Update Strategies

| Strategy | Value | Behavior | Recommended For |
|---|---|---|---|
| Smart In-Place Sync | `smart-sync` | Unpacks release contents over the destination directory, replacing updated files while preserving existing custom files and plugins. | IDEs, decompilers (such as Ghidra), and tools with user plugins or local configs. |
| Symlink Strategy | `symlink` | Installs versions into isolated subdirectories (`versions/<tag>/`) and points a `current` symlink to the active version. | Daemons, runtimes, and tools needing atomic updates or rollback capability. |
| Clean Wipe | `wipe` | Deletes the destination directory contents prior to unpacking the fresh release. | Standalone CLI tools and single binary archives (such as ripgrep). |

## Lifecycle Hooks

Hooks execute custom shell commands during tool updates. The following environment variables are exposed to hook processes:

- `JUST_RELEASE_TOOL`: Identifier or name of the tool.
- `JUST_RELEASE_NEW_VERSION`: Tag name of the new release.

Example pre-update hook to stop running processes:
```bash
pkill -f myapp || true
```

Example post-update hook to refresh symlinks or create desktop entries:
```bash
ln -sf /home/user/apps/myapp/current/myapp /home/user/.local/bin/myapp
```

## Project Structure

```
.
├── main.py                     # CLI entrypoint and application launcher
├── requirements.txt            # Python package dependencies
├── just_release/
│   ├── __init__.py
│   ├── config.py               # YAML manifest manager and defaults
│   ├── github.py               # GitHub API client and asset pattern matching
│   └── ui/
│       ├── __init__.py
│       ├── app.py              # Adw.Application lifecycle setup
│       ├── main_window.py      # Primary application window and tool listing
│       ├── settings_dialog.py  # Preferences and manifest configuration dialog
│       └── track_dialog.py     # Repository tracking and asset selection dialog
└── README.md
```

## License

This project is licensed under the MIT License.
