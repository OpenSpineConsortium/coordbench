"""An agent definition every agent of a run runs as: a Claude Code subagent file.

`run.py run --agent-file FILE` names one: a Markdown file whose front
matter gives its name, a one-line description and the tools it may use,
and whose body is its instructions. It is the format Symphony keeps its
agents in (OpenSpineConsortium/symphony-template, .claude/agents/ and
docs/agents/AGENTS.md), so an agent a repository uses for its work is
the agent coordbench measures, brought in as the file it is. Every agent
of the pair then runs as it (`claude --agents FILE --agent NAME`) on the
claude-cli driver, the arm's prompt still the task; the run's model and
its disallowed tools hold over the file's, so the agent's instructions
and its narrower tool list are what differs, and result.json names the
agent and the sha256 of its file.
"""

import hashlib
import re
from pathlib import Path

NAME = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9])){0,38}$")
MAX_BYTES = 64 * 1024


class AgentFileError(Exception):
    """Why a file is not an agent a stage can run as."""


def read(path):
    """The agent at PATH as {name, description, tools, prompt}; tools is
    None when the file lists none, which leaves the session's own."""
    path = Path(path)
    if not path.is_file():
        raise AgentFileError(f"{path}: no agent file there")
    text = path.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise AgentFileError(f"{path}: over {MAX_BYTES // 1024} KB")
    lines = text.replace("\r\n", "\n").split("\n")
    if not lines or lines[0].strip() != "---":
        raise AgentFileError(f"{path}: an agent file opens with a front matter between two lines of ---")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise AgentFileError(f"{path}: the front matter has no closing ---")
    fields, key = {}, None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            item = line.strip()
            if key and item.startswith("- ") and isinstance(fields.get(key), list):
                fields[key].append(item[2:].strip().strip("'\""))
            continue
        if ":" in line:
            key, value = (part.strip() for part in line.split(":", 1))
            fields[key] = value.strip("'\"") if value else []
    name, description = fields.get("name"), fields.get("description")
    if not isinstance(name, str) or not NAME.match(name):
        raise AgentFileError(f"{path}: name must be lower case letters, digits and inner dashes")
    if not isinstance(description, str) or not description:
        raise AgentFileError(f"{path}: description is missing")
    tools = fields.get("tools")
    if isinstance(tools, str):
        tools = [tool.strip() for tool in tools.split(",") if tool.strip()]
    prompt = "\n".join(lines[end + 1:]).strip()
    if not prompt:
        raise AgentFileError(f"{path}: the body, the agent's instructions, is empty")
    return {"name": name, "description": description, "tools": tools or None, "prompt": prompt + "\n"}


def definition(path):
    """The agent at PATH as `claude --agents` reads it, {name: {description,
    prompt, tools}}, with no model, so the run's model holds; and the
    sha256 of the file, for the record."""
    agent = read(path)
    found = {"description": agent["description"], "prompt": agent["prompt"]}
    if agent["tools"]:
        found["tools"] = agent["tools"]
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return {"name": agent["name"], "sha256": digest, "agents": {agent["name"]: found}}
