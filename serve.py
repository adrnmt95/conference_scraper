"""Run from the project directory: .venv/bin/python serve.py, then use the page that opens."""

import sys

try:
    from conference_agent.web import main
except ModuleNotFoundError as exc:
    sys.exit(
        f"Missing package '{exc.name}' in this Python ({sys.executable}).\n"
        "Start the site with the project's environment instead:\n"
        "    .venv/bin/python serve.py\n"
        "or install the requirements here with:  python -m pip install '.[web]'"
    )

if __name__ == "__main__":
    raise SystemExit(main())
