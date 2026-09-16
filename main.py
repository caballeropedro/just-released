#!/usr/bin/env python3
import sys
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="just-release: Autonomous GitHub Release Orchestrator")
    parser.add_argument("--manifest", "-m", help="Path to releases manifest file (YAML)", default=None)
    args = parser.parse_args()

    # Launch Native GTK4/Adwaita Desktop Application
    from just_release.ui.app import JustReleaseApp
    app = JustReleaseApp(manifest_path=args.manifest)
    sys.exit(app.run(sys.argv[:1]))

if __name__ == "__main__":
    main()
