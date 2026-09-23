"""Just enough git to tell added lines from pre-existing ones."""

import subprocess


def _run(args, cwd=None):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, check=False, cwd=cwd
        )
    except (OSError, ValueError):
        return None
    return result.stdout if result.returncode == 0 else None


def run_in(cwd, args):
    return _run(args, cwd=cwd)


def staged_files():
    output = _run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"])
    return [line for line in (output or "").splitlines() if line.strip()]


def head_content(path):
    """The committed version of a path, or None if it is new."""
    return _run(["git", "show", "HEAD:%s" % path])


def commit_message_file(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def in_repo():
    return _run(["git", "rev-parse", "--is-inside-work-tree"]) is not None
