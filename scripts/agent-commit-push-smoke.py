from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


PROMPT = "Заккомить все изменения, предвратительно определив их, и запушь в репозиторий в этой же папке"


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return completed.stdout.strip()


def current_branch(cwd: Path) -> str:
    return git(cwd, "branch", "--show-current")


def ahead_count(cwd: Path, branch: str) -> int:
    try:
        upstream = git(cwd, "rev-parse", "--abbrev-ref", f"{branch}@{{upstream}}")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"Branch {branch!r} has no upstream configured:\n{exc.stdout}") from exc
    counts = git(cwd, "rev-list", "--left-right", "--count", f"{upstream}...HEAD").split()
    if len(counts) != 2:
        raise RuntimeError(f"Unexpected rev-list output: {counts!r}")
    return int(counts[1])


def wait_for_git_gate(repo: Path, before_head: str, branch: str, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            head = git(repo, "rev-parse", "HEAD")
            status = git(repo, "status", "--short")
            ahead = ahead_count(repo, branch)
            if head != before_head and not status and ahead == 0:
                return
            last_error = f"head_changed={head != before_head}, dirty={bool(status)}, ahead={ahead}"
        except Exception as exc:  # noqa: BLE001 - keep polling while the agent works.
            last_error = str(exc)
        time.sleep(3)
    raise TimeoutError(f"Agent did not commit and push successfully before timeout. Last state: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Browser smoke test that asks the local model to commit and push the current repository."
    )
    parser.add_argument("--repo", default=".", help="Repository path to verify after the model acts.")
    parser.add_argument("--url", default="http://127.0.0.1:7860/", help="Running Gemma UI URL.")
    parser.add_argument("--timeout", type=int, default=600, help="Seconds to wait for commit+push.")
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    before_head = git(repo, "rev-parse", "HEAD")
    branch = current_branch(repo)
    if not branch:
        raise RuntimeError("Repository is in detached HEAD state; smoke test needs a branch with upstream.")

    project_name = f"Agent gate {int(time.time())}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        page = browser.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        page.locator("#projectName").fill(project_name)
        page.locator("#projectForm button[type='submit']").click()
        page.locator("#messageInput").wait_for(state="visible", timeout=30_000)
        page.locator("#messageInput").fill(PROMPT)
        page.locator("#chatForm").evaluate("form => form.requestSubmit()")
        try:
            page.locator("#runState", has_text="idle").wait_for(timeout=30_000)
        except PlaywrightTimeoutError:
            pass
        wait_for_git_gate(repo, before_head, branch, args.timeout)
        browser.close()

    print("Agent commit+push smoke test passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - CLI should print a readable failure.
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
