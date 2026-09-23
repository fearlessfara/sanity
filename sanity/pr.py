"""Detect PR/MR create commands in agent shell / MCP payloads.

Template structure checks belong in your PR bot / CODEOWNERS flow.
Sanity only needs to know *that* a PR is being opened so it can grade
natural-language rules against the body.
"""

import os
import re
import shlex

HEREDOC = re.compile(r"<<-?\s*[\"']?(\w+)[\"']?[^\n]*\n(.*?)\n\s*\1\b", re.S)
SEPARATORS = {"&&", "||", ";", "|", "&"}
CLI_COMMANDS = [
    (("gh", "pr", "create"), "github"),
    (("gh", "pr", "edit"), "github"),
    (("glab", "mr", "create"), "gitlab"),
    (("glab", "mr", "update"), "gitlab"),
]
TITLE_FLAGS = {"--title", "-t"}
BODY_FLAGS = {"--body", "-b", "--description", "-d"}
BODY_FILE_FLAGS = {"--body-file", "-F"}


def unwrap_heredoc(value):
    match = HEREDOC.search(value or "")
    return match.group(2) if match else value


def unescape(value):
    if value and "\\n" in value and "\n" not in value:
        return value.replace("\\n", "\n").replace('\\"', '"')
    return value


def parse_command(command):
    """(kind, title, body, flags) for a PR-creating command, else None."""
    try:
        parts = shlex.split(command or "", comments=False)
    except ValueError:
        parts = (command or "").split()

    start = kind = None
    for index in range(len(parts)):
        for words, flavour in CLI_COMMANDS:
            if tuple(parts[index:index + len(words)]) == words:
                start, kind = index + len(words), flavour
                break
        if start is not None:
            break
    if start is None:
        return None

    title = body = body_file = None
    flags = set()
    index = start
    while index < len(parts):
        token = parts[index]
        if token in SEPARATORS:
            break
        name, _, inline = token.partition("=")
        value = inline or (parts[index + 1] if index + 1 < len(parts) else None)
        step = 1 if inline else 2

        if name in TITLE_FLAGS:
            title, index = value, index + step
            continue
        if name in BODY_FLAGS:
            body, index = value, index + step
            continue
        if name in BODY_FILE_FLAGS:
            body_file, index = value, index + step
            continue
        if token.startswith("-"):
            flags.add(name.lstrip("-"))
        index += 1

    if body:
        body = unwrap_heredoc(body)
    elif body_file and body_file != "-":
        try:
            with open(body_file, encoding="utf-8") as handle:
                body = handle.read()
        except OSError:
            body = None
    if body is None:
        match = HEREDOC.search(command or "")
        if match:
            body = match.group(2)

    return kind, title, unescape(body), flags
