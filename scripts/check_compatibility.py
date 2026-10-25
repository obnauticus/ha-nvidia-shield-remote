"""Check integration imports against the installed Home Assistant runtime.

Run this script with a real Home Assistant installation, for example using
the Docker command in README.md. It does not connect to a Shield or send keys.
"""

import importlib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for suffix in ("", ".config_flow", ".remote"):
    importlib.import_module("custom_components.nvidia_shield_remote" + suffix)

print("Integration, config flow, and remote imports passed")
