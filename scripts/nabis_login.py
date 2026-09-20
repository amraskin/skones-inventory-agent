"""Run this once (and again whenever the session expires) from a machine
with normal internet access - NOT from a locked-down remote sandbox.

Opens a real, visible browser window pointed at Nabis. Log in yourself,
including any 2FA/verification step, then come back to this terminal and
press Enter. Your session cookies get saved to data/session_state/nabis.json
so src/vendors/nabis.py can reuse them without re-logging-in every run.
"""
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
SESSION_STATE_PATH = REPO_ROOT / "data" / "session_state" / "nabis.json"
LOGIN_URL = "https://ny.nabis.com/login"


def main():
    SESSION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(LOGIN_URL)

        input(
            "\nA browser window opened to Nabis. Log in there (including any "
            "2FA), then come back here and press Enter to save the session... "
        )

        context.storage_state(path=str(SESSION_STATE_PATH))
        print(f"Session saved to {SESSION_STATE_PATH}")
        browser.close()


if __name__ == "__main__":
    main()
