# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "httpx>=0.27,<1",
#   "openai>=2,<3",
#   "pillow>=10,<13",
#   "tomli>=2,<3; python_version < '3.11'",
# ]
# ///

"""Image generation CLI for user-configured OpenAI-compatible gateways."""

from __future__ import annotations

from imagegen_cli import main

if __name__ == "__main__":
    raise SystemExit(main())
