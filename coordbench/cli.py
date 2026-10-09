"""The shell surface: an arm's tools as commands an agent runs in its shell.

Not every agent speaks MCP, and CooperBench's own prompts name shell
commands (coop-send, coop-recv and the rest), so every tool the hub serves
is also a command. The hub's /rpc endpoint for this agent (COORD_RPC_URL,
or --url) lists the tools with their aliases and runs them:

    python3 cli.py tools                       each tool, its command and what it does
    python3 cli.py prompt                      the agent's prompt
    python3 cli.py call <tool> '{"to": "agent2", "text": "hi"}'
    python3 cli.py call <tool> to=agent2 text=hi
    python3 cli.py alias-run <tool> agent2 hi there
    python3 cli.py install /usr/local/bin [--url URL] [--python PATH]

install writes one small sh wrapper per alias (coop-send) and one per tool
(coord-send_message), each running `alias-run <tool>`, so the agent types
`coop-send agent2 hi there` as in the authors' runs. Given --url, the
wrappers carry the URL themselves, which lets agents that share one host
each have a bin directory of their own.

alias-run reads the words as the tool's command line. A word naming one of
the tool's flags sets that flag's argument wherever it stands, before,
among or after the other words: a flag whose argument the tool's schema
types as a boolean is a switch (--mine), any other takes the next word
(-n "a note") or the value after "=" (--wait=10). The words left go onto
the tool's argv in order. A word that is not one of the tool's flags stays
a word even when it starts with a dash, since a message may.

What a command prints: the tool's text on stdout and exit 0; a tool that
refused its call, its text on stderr and the exit code the tool gave its
command, 1 when it gave none, so a ported command keeps the codes its
callers branch on (coop-task-claim exits 2 for a task another agent
holds); a hub that cannot be reached or answers with an HTTP error, or a
command used wrongly, one line on stderr and exit 2.

The file imports nothing from coordbench, so it can be copied alone into
an agent's container and run by whatever Python 3 is there.
"""

import argparse
import json
import os
import re
import shlex
import sys
import urllib.error
import urllib.request
from http.client import HTTPException
from pathlib import Path

OK, TOOL_ERROR, FAILURE = 0, 1, 2

# Generous, because a tool may block by design (wait_for_message waits up
# to 30 seconds); the hub decides how long a call lasts.
TIMEOUT_SECONDS = 600

# The hub is local to the agent (localhost or the Docker host), so a proxy
# set for the container's outbound traffic must not carry these requests.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Command names become file names in a bin directory; anything else is
# refused rather than written somewhere unexpected.
SAFE_COMMAND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

TYPES_PARSED_AS_JSON = {"integer", "number", "boolean", "array", "object", "null"}


class Failure(Exception):
    """A hub failure or a misused command: one line for stderr, exit 2."""


def one_line(text, limit=300):
    flat = " ".join(str(text).split())
    return flat if len(flat) <= limit else flat[:limit] + "..."


def hub_error_text(problem):
    """The hub's own reason for an HTTP error: its {"error": ...} body when
    it sent one, else the body as text, else the status line."""
    try:
        body = problem.read().decode("utf-8", "replace")
    except (OSError, HTTPException):
        body = ""
    finally:
        problem.close()
    try:
        reason = json.loads(body).get("error") or body
    except (ValueError, AttributeError):
        reason = body
    return one_line(reason) if str(reason).strip() else problem.reason


def explain(url, problem):
    """One line naming what went wrong between this command and the hub."""
    hub = f"the coordbench hub at {url}"
    if isinstance(problem, urllib.error.HTTPError):
        return f"{hub} answered HTTP {problem.code}: {hub_error_text(problem)}"
    if isinstance(problem, urllib.error.URLError):
        return f"cannot reach {hub}: {one_line(problem.reason)}"
    return f"lost {hub}: {one_line(problem) or type(problem).__name__}"


def exchange(url, body=None):
    """GET the url, or POST body to it as JSON; the answer as JSON."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                     headers={"Content-Type": "application/json",
                                              "Accept": "application/json"})
    try:
        with OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except (OSError, HTTPException) as problem:
        raise Failure(explain(url, problem))
    try:
        return json.loads(raw)
    except ValueError:
        raise Failure(f"the coordbench hub at {url} answered something that is not JSON")


def describe_agent(url):
    answer = exchange(url)
    if not isinstance(answer, dict):
        raise Failure(f"the coordbench hub at {url} did not describe this agent")
    return answer


def find_tool(description, name):
    for tool in description.get("tools") or []:
        if tool.get("name") == name:
            return tool
    names = ", ".join(t.get("name", "?") for t in description.get("tools") or []) or "none"
    raise Failure(f"this agent has no tool named {name!r}; its tools are: {names}")


def command_name(tool):
    return tool.get("alias") or f"coord-{tool['name']}"


def flags_of(tool):
    """The tool's flags, {shell flag: argument name}; none from a hub that
    lists none."""
    flags = (tool or {}).get("flags")
    return flags if isinstance(flags, dict) else {}


def flag_usage(tool):
    """[--mine] for a switch and [-n|--note <note>] for a flag that takes a
    word, the flags that set the same argument shown together."""
    spellings = {}
    for flag, name in flags_of(tool).items():
        spellings.setdefault(name, []).append(flag)
    return [f"[{'|'.join(flags)}]" if is_switch(tool, name) else f"[{'|'.join(flags)} <{name}>]"
            for name, flags in spellings.items()]


def usage(tool):
    """The command as it is typed: the flags first, as argparse shows them,
    so none reads as part of a trailing text..., then the argv words. A
    tool with no argv takes key=value words, shown when its schema has an
    argument no flag sets."""
    argv = list(tool.get("argv") or [])
    words = flag_usage(tool) + [f"<{name}>" for name in argv]
    properties = (tool.get("inputSchema") or {}).get("properties") or {}
    if not argv and set(properties) - set(flags_of(tool).values()):
        words.append("[key=value ...]")
    return " ".join([command_name(tool)] + words)


def property_types(tool, name):
    schema = ((tool or {}).get("inputSchema") or {}).get("properties", {}).get(name) or {}
    kind = schema.get("type", "string")
    return set(kind) if isinstance(kind, list) else {kind}


def is_switch(tool, name):
    """A flag whose argument the schema types as a boolean takes no word."""
    return "boolean" in property_types(tool, name)


def coerce(tool, name, value):
    """A word from the shell is a string; a property the schema types as a
    number, a boolean or JSON gets the word parsed, when it parses."""
    types = property_types(tool, name)
    if "string" in types or not types & TYPES_PARSED_AS_JSON:
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def parse_arguments(words, tool=None):
    """Arguments from the shell: one JSON object, or key=value words."""
    if len(words) == 1 and words[0].lstrip().startswith("{"):
        try:
            arguments = json.loads(words[0])
        except ValueError as problem:
            raise Failure(f"the arguments are not valid JSON: {problem}")
        if not isinstance(arguments, dict):
            raise Failure("the arguments must be a JSON object")
        return arguments
    arguments = {}
    for word in words:
        key, equals, value = word.partition("=")
        if not equals or not key:
            raise Failure(f"expected key=value or one JSON object, got {word!r}")
        arguments[key] = coerce(tool, key, value)
    return arguments


def stdin_has_text():
    """True when stdin is a pipe or a file rather than a terminal, which is
    when CooperBench's coop-send reads its message from it."""
    try:
        return sys.stdin is not None and not sys.stdin.isatty()
    except (ValueError, OSError):
        return False


def flag_in(tool, word):
    """The flag of this tool a word names, and the value it carries after
    "=": ("--wait", "10") for --wait=10, ("-n", None) for -n, and
    (None, None) for a word that names none of the tool's flags."""
    flags = flags_of(tool)
    if word in flags:
        return word, None
    head, equals, value = word.partition("=")
    if equals and head in flags:
        return head, value
    return None, None


def flag_value(tool, flag, inline, words):
    """What a flag sets its argument to: True for a switch given alone,
    else its value after "=" or the next word, typed by the schema."""
    name = flags_of(tool)[flag]
    if inline is None and is_switch(tool, name):
        return True
    value = inline if inline is not None else next(words, None)
    if value is None:
        raise Failure(f"{flag} takes a value; usage: {usage(tool)}")
    return coerce(tool, name, value)


def split_flags(tool, words):
    """The arguments the tool's flags set, wherever the flags stand among
    the words, and the words left for argv, in order."""
    arguments, rest = {}, []
    words = iter(words)
    for word in words:
        flag, inline = flag_in(tool, word)
        if flag is None:
            rest.append(word)
        else:
            arguments[flags_of(tool)[flag]] = flag_value(tool, flag, inline, words)
    return arguments, rest


def words_to_arguments(tool, words):
    """Map shell words onto the tool's flags (split_flags), then the words
    left onto its argv names in order."""
    flagged, rest = split_flags(tool, words)
    return {**argv_arguments(tool, rest), **flagged}


def argv_arguments(tool, words):
    """Map words onto the tool's argv names in order; a name ending in
    "..." takes the rest of the words joined by spaces, and, when no word
    is left for it but every earlier name got one, the text on stdin (as
    coop-send does in CooperBench's coop_msg.py at 63b9d44). A tool with
    no argv takes key=value words or one JSON object instead."""
    argv = list(tool.get("argv") or [])
    if not argv:
        return parse_arguments(words, tool)
    arguments = {}
    for index, name in enumerate(argv):
        if name.endswith("..."):
            rest_name = name[:-3]
            if words[index:]:
                arguments[rest_name] = coerce(tool, rest_name, " ".join(words[index:]))
            elif len(words) == index and stdin_has_text():
                arguments[rest_name] = sys.stdin.read()
            return arguments
        if index < len(words):
            arguments[name] = coerce(tool, name, words[index])
    if len(words) > len(argv):
        raise Failure(f"too many words; usage: {usage(tool)}")
    return arguments


def run_tool(url, name, arguments):
    """The hub's answer to one call: {"text", "isError", "exitCode"}."""
    answer = exchange(url, {"tool": name, "arguments": arguments})
    if not isinstance(answer, dict):
        raise Failure(f"the coordbench hub at {url} answered a call without a result")
    return answer


def show(text, stream):
    if text:
        stream.write(text if text.endswith("\n") else text + "\n")
        stream.flush()


def exit_code(answer):
    """0 for a result; for a refusal, the code the tool gave its command,
    or 1 when the hub sent none, or one no shell can exit with."""
    if not answer.get("isError"):
        return OK
    code = answer.get("exitCode")
    if isinstance(code, int) and not isinstance(code, bool) and 0 <= code <= 255:
        return code
    return TOOL_ERROR


def report(answer):
    show(str(answer.get("text", "")), sys.stderr if answer.get("isError") else sys.stdout)
    return exit_code(answer)


def describe_tool(tool):
    lines = [f"{tool['name']}  (shell: {usage(tool)})"]
    lines += [f"    {line}" for line in (tool.get("description") or "").splitlines()]
    return "\n".join(lines)


def list_tools(args):
    description = describe_agent(args.url)
    show("\n".join(describe_tool(t) for t in description.get("tools") or []), sys.stdout)
    return OK


def print_prompt(args):
    show(describe_agent(args.url).get("prompt") or "", sys.stdout)
    return OK


def call(args):
    tool = None
    if any("=" in word for word in args.words) and not args.words[0].lstrip().startswith("{"):
        tool = find_tool(describe_agent(args.url), args.tool)
    return report(run_tool(args.url, args.tool, parse_arguments(args.words, tool)))


def alias_run(args):
    tool = find_tool(describe_agent(args.url), args.tool)
    return report(run_tool(args.url, args.tool, words_to_arguments(tool, args.words)))


def wrapper_script(tool_name, python, url=None):
    cli = Path(__file__).resolve()
    lines = ["#!/bin/sh"]
    if url:
        lines.append(f"export COORD_RPC_URL={shlex.quote(url)}")
    lines.append(f"exec {shlex.quote(python)} {shlex.quote(str(cli))} alias-run "
                 f"{shlex.quote(tool_name)} \"$@\"")
    return "\n".join(lines) + "\n"


def write_wrapper(bin_dir, command, script):
    """Replace whatever has the name, unlinking first so that a symlink
    (an earlier install's, or a system command's) is replaced rather than
    written through."""
    path = bin_dir / command
    path.unlink(missing_ok=True)
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return path


def commands_for(tool):
    names = [tool.get("alias"), f"coord-{tool['name']}"]
    return [n for n in names if n]


def install(args):
    """Write the wrappers. The tools come from the hub, so what an agent
    can type is exactly what its role in the arm allows."""
    baked_url = args.install_url or args.global_url
    bin_dir = Path(args.bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for tool in describe_agent(args.url).get("tools") or []:
        script = wrapper_script(tool["name"], args.python, baked_url)
        for command in commands_for(tool):
            if not SAFE_COMMAND.match(command):
                print(f"coordbench: skipped the command {command!r}: not a safe file name",
                      file=sys.stderr)
                continue
            written.append(write_wrapper(bin_dir, command, script).name)
    show(f"installed in {bin_dir.resolve()}: {' '.join(written) or 'nothing'}", sys.stdout)
    return OK


def parser():
    top = argparse.ArgumentParser(prog="cli.py", description="An arm's tools as shell commands.")
    top.add_argument("--url", dest="global_url",
                     help="this agent's /rpc URL (default: $COORD_RPC_URL)")
    commands = top.add_subparsers(dest="command", required=True)
    commands.add_parser("tools", help="list the tools").set_defaults(func=list_tools)
    commands.add_parser("prompt", help="print the agent's prompt").set_defaults(func=print_prompt)
    calling = commands.add_parser("call", help="call a tool with a JSON object or key=value words")
    calling.add_argument("tool")
    calling.add_argument("words", nargs=argparse.REMAINDER)
    calling.set_defaults(func=call)
    aliased = commands.add_parser("alias-run", help="call a tool with words mapped onto its flags and argv")
    aliased.add_argument("tool")
    aliased.add_argument("words", nargs=argparse.REMAINDER)
    aliased.set_defaults(func=alias_run)
    installing = commands.add_parser("install", help="write a shell command per tool into bin_dir")
    installing.add_argument("bin_dir")
    installing.add_argument("--url", dest="install_url", help="bake this /rpc URL into the commands")
    installing.add_argument("--python", default=sys.executable or "python3",
                            help="the Python the commands run (default: this one)")
    installing.set_defaults(func=install)
    return top


def hub_url(args):
    url = getattr(args, "install_url", None) or args.global_url or os.environ.get("COORD_RPC_URL", "")
    if not url.strip():
        raise Failure("no hub URL: pass --url or set COORD_RPC_URL to this agent's /rpc URL")
    return url.strip()


def main(argv=None):
    try:
        args = parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else FAILURE
    try:
        args.url = hub_url(args)
        return args.func(args)
    except Failure as failure:
        print(f"coordbench: {failure}", file=sys.stderr)
        return FAILURE


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    sys.exit(main())
