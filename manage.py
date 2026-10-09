#!/usr/bin/env python
import os
import sys
from pathlib import Path

# Make sure the project folder is importable (fixes "No module named 'config'")
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
