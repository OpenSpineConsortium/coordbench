"""The shell surface: an arm's tools as commands an agent runs in its shell.

Not every agent speaks MCP, and CooperBench's own prompts name shell
commands (coop-send, coop-recv and the rest), so every tool the hub serves
is also a command. The hub's /rpc endpoint for this agent (COORD_RPC_URL,
or --url) lists the tools with their aliases and runs them:

    python3 cli.py tools                       each tool, its command and what it does
    python3 cli.py prompt                      the agent's prompt
    python3 cli.py call <tool> '{"to": "agent2", "text": "hi"}'
    python3 cli.py call <tool> to=agent2 text=hi
    python3 cli.py alias-run <tool> agent2 "hi there"
    python3 cli.py install /usr/local/bin [--url URL] [--python PATH] [--token-file PATH] [--debug]

install writes one small sh wrapper per alias (coop-send) and one per tool
(coord-send_message), each running `alias-run <tool>`, so the agent types
`coop-send agent2 "hi there"` as in the authors' runs. Given --url, the
wrappers carry the URL themselves, which lets agents that share one host
each have a bin directory of their own.

A run the hub made with agent tokens wants this agent's bearer token on
every request, so each request to /rpc carries
"Authorization: Bearer <COORD_AGENT_TOKEN>" when that variable is set and
not empty. Given --token-file, install reads the token from that file for
its own request and writes wrappers that read it with cat at run time and
export it, so the token is in no wrapper's text and on no command line. A
401 from the hub is reported as the hub's own words, exit 2, as any other
HTTP error is. Given --debug, install writes wrappers that export
COORD_TRACE=debug, so the commands trace in debug (below) whatever the
environment that runs them says: the session driver's agents run them from
a shell the runner does not start, and a --debug run has them time
themselves all the same.

alias-run reads the words as the tool's command line. A word naming one of
the tool's flags sets that flag's argument wherever it stands, before,
among or after the other words: a flag whose argument the tool's schema
types as a boolean is a switch (--mine), any other takes the next word
(-n "a note") or the value after "=" (--wait=10). The words left go onto
the tool's argv in order, one word per name, a name ending in "..."
taking the rest of the line; the last name, "..." or not, takes the text
on stdin when no word is left for it and the tool's schema does not
require it (`coop-send agent2 < note.txt`, as coop_msg.py reads its
content from stdin when the word is missing; coop-task-create with no
title sends none, and the hub refuses it with exit 2, as argparse refuses
coop_task.py's). A word beyond the names is refused, as argparse refuses
it. A word that is not one of the tool's flags stays a word even when it
starts with a dash, since a message may. A word for an argument the
schema types as a number is refused, exit 2 and argparse's wording, when
it is true, false or null, which Python would read as 1.0, 0.0 or None.

What a command prints: the tool's text on stdout and exit 0, or on stderr
when the tool's schema says so ("x-coordbench-prints": "stderr", which
coop-send carries so that "sent to agent2" goes where coop_msg.py sends
it and stdout stays empty); a tool that refused its call, its text on
stderr and the exit code the tool gave its command, 1 when it gave none,
so a ported command keeps the codes its callers branch on (coop-task-claim
exits 2 for a task another agent holds); a hub that cannot be reached or
answers with an HTTP error, or a command used wrongly, one line on stderr
and exit 2.

In debug (trace.py's level, COORD_TRACE=debug in the command's
environment) every request to the hub is an "rpc_call" span: method, path,
tool and request_bytes as it starts, and status, response_bytes,
duration_ms and ok on its rpc_call_done; every alias-run is also one
"command" event with the command the agent typed, the tool, its exit code
and duration_ms. They go as JSON lines to stderr, the agent's own stream,
so a debug run's shell commands show their timings beside their output,
or to the file COORD_TRACE_FILE names when it is set; a message's text,
a tool's arguments and the agent's token are never among them. In prod,
the default, nothing is registered and nothing is written, so what a
command prints is exactly what is said above.

The file imports nothing from coordbench but trace.py, which it takes
from beside itself when the directory was copied whole and does without
otherwise (a stand-in that records nothing), so it can be copied alone
into an agent's container and run by whatever Python 3 is there.
"""

import argparse
import importlib.util
import json
import os
import re
import shlex
import sys
import time
import urllib.error
import urllib.request
from http.client import HTTPException
from pathlib import Path
from urllib.parse import urlsplit

OK, TOOL_ERROR, FAILURE = 0, 1, 2

# Generous, because a tool may block by design (wait_for_message and the
# team arm's request wait up to 300 seconds); the hub decides how long a
# call lasts.
TIMEOUT_SECONDS = 600

# The hub is local to the agent (localhost or the Docker host), so a proxy
# set for the container's outbound traffic must not carry these requests.
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# Command names become file names in a bin directory; anything else is
# refused rather than written somewhere unexpected.
SAFE_COMMAND = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# Where the agent's bearer token is, when the run wants one.
TOKEN_VARIABLE = "COORD_AGENT_TOKEN"

TYPES_PARSED_AS_JSON = {"integer", "number", "boolean", "array", "object", "null"}
NUMBER_TYPES = {"integer", "number"}

# The trace level, which trace.py reads from the environment once, and
# where a debug run's events go instead of stderr, when set.
TRACE_VARIABLE = "COORD_TRACE"
TRACE_FILE_VARIABLE = "COORD_TRACE_FILE"


class NoSpan:
    """What the stand-in's debug_span gives: a block that is not timed and
    a decorator that leaves its function alone. fields is where the caller
    puts what it learns on the way, read by nobody here."""

    def __init__(self, fields):
        self.fields = fields

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False

    def __call__(self, function):
        return function


class NoTrace:
    """What stands in for trace.py when the file is not beside this one:
    the level off and the three calls this file makes, each doing nothing,
    so a copy of cli.py alone works as before and debug is never a
    dependency of the agent's commands."""

    level = "off"

    @staticmethod
    def event(kind, **fields):
        return None

    @staticmethod
    def debug(kind, **fields):
        return None

    @staticmethod
    def debug_span(name, **fields):
        return NoSpan(fields)


def same_file(module, path):
    try:
        return Path(module.__file__).resolve() == path
    except (AttributeError, TypeError, OSError):
        return False


def load_trace():
    """coordbench's trace.py, beside this file when the directory was
    copied whole (drivers put it at /opt/coordbench), loaded by its
    location: the standard library has a module named trace too, and
    `import trace` would take that one whenever this directory is not
    first on sys.path. A process that imported coordbench's already
    (run.py calls cli.main in-process) shares it, so a --debug run sees
    these events on its own stream; a fresh process registers the module
    under its name when no module holds it yet, so the rest of coordbench
    imported later finds the same one, and leaves another's alone. With
    no trace.py beside this file, the stand-in."""
    path = Path(__file__).resolve().with_name("trace.py")
    known = sys.modules.get("trace")
    if same_file(known, path):
        return known
    if not path.is_file():
        return NoTrace
    spec = importlib.util.spec_from_file_location("trace", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if known is None:
        sys.modules["trace"] = module
    return module


coordtrace = load_trace()


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


def request_headers(token):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def exchange(url, body=None, token=None):
    """GET the url, or POST body to it as JSON, with the agent's bearer
    token when there is one; the answer as JSON. In debug the request is
    one "rpc_call" span: what is asked and how big, then, once the hub
    has answered, its status and the answer's size, which fetch puts on
    the span's fields so that the closing event, rpc_call_done, carries
    them with the duration. The tool's name goes in, never its arguments;
    in prod the span is one comparison on enter and a flag on exit."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                     headers=request_headers(token))
    tool = body.get("tool") if isinstance(body, dict) else None
    with coordtrace.debug_span("rpc_call", method=request.get_method(), path=urlsplit(url).path,
                               tool=tool, request_bytes=len(data or b"")) as span:
        raw = fetch(request, url, span.fields)
    try:
        return json.loads(raw)
    except ValueError:
        raise Failure(f"the coordbench hub at {url} answered something that is not JSON")


def fetch(request, url, fields):
    """The hub's raw answer to one request, or Failure. fields, the
    rpc_call span's, learn the status when the hub answered at all and
    the answer's size when it was read; an unreachable hub leaves both
    out, which is the honest shape of that event."""
    try:
        with OPENER.open(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            fields.update(status=response.status, response_bytes=len(raw))
            return raw
    except urllib.error.HTTPError as problem:
        fields["status"] = problem.code
        raise Failure(explain(url, problem))
    except (OSError, HTTPException) as problem:
        raise Failure(explain(url, problem))


def describe_agent(url, token=None):
    answer = exchange(url, token=token)
    if not isinstance(answer, dict):
        raise Failure(f"the coordbench hub at {url} did not describe this agent")
    return answer


def tool_named(description, name):
    return next((t for t in description.get("tools") or [] if t.get("name") == name), None)


def find_tool(description, name):
    tool = tool_named(description, name)
    if tool is None:
        names = ", ".join(t.get("name", "?") for t in description.get("tools") or []) or "none"
        raise Failure(f"this agent has no tool named {name!r}; its tools are: {names}")
    return tool


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


def coerce(tool, name, value, label=None):
    """A word from the shell is a string; a property the schema types as a
    number, a boolean or JSON gets the word parsed, when it parses. The
    words true, false and null are refused for a property typed as a
    number alone, in argparse's words for its type=float or type=int
    (label is the flag or the name argparse would show), since the hub
    would read the parsed JSON as 1.0, 0.0 or None where coop_task.py
    exits 2 on them."""
    types = property_types(tool, name)
    if "string" in types or not types & TYPES_PARSED_AS_JSON:
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    if not_a_number(types, parsed):
        kind = "int" if (types & NUMBER_TYPES) == {"integer"} else "float"
        raise Failure(f"argument {label or name}: invalid {kind} value: {value!r}")
    return parsed


def not_a_number(types, parsed):
    """True for a boolean or a null parsed from a word the schema types as
    a number and not also as that: what Python's float() would take and
    argparse's type=float refuses."""
    if not types & NUMBER_TYPES:
        return False
    if isinstance(parsed, bool):
        return "boolean" not in types
    return parsed is None and "null" not in types


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
    """True when stdin is a pipe or a file rather than a terminal. The
    terminal check is coordbench's own rule: coop_msg.py reads stdin
    whenever its content word is missing, terminal or not, and a person
    who types `coop-send agent2` at a terminal then sits in a read that
    nothing ends; here the word is left missing instead."""
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
    return coerce(tool, name, value, label=flag)


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


def required_names(tool):
    """The arguments the tool's schema lists as required; none from a hub
    that lists none."""
    required = ((tool or {}).get("inputSchema") or {}).get("required")
    return set(required) if isinstance(required, list) else set()


def argv_arguments(tool, words):
    """Map words onto the tool's argv names in order: one word per name,
    the rest of the words joined by spaces for a name ending in "...",
    which is the last, and for the last name, "..." or not, the text on
    stdin when no word is left for it, every earlier name got one, and
    the schema does not require it. That is coop_msg.py's content and
    coop_task.py's body at 63b9d44, optional positionals read from stdin
    when missing, so `coop-send agent2 < note.txt` works as in
    CooperBench; a required one left missing (coop-task-create's title,
    coop-task-claim's task_id, coop-task-update's status) is sent as
    missing, for the hub to refuse with exit 2 as argparse refuses it,
    rather than read as "" from a stdin at /dev/null. Words beyond the
    names are refused, as argparse refuses them. A tool with no argv
    takes key=value words or one JSON object."""
    argv = list(tool.get("argv") or [])
    if not argv:
        return parse_arguments(words, tool)
    if not argv[-1].endswith("...") and len(words) > len(argv):
        raise Failure(f"too many words; usage: {usage(tool)}")
    arguments, required = {}, required_names(tool)
    for index, name in enumerate(argv):
        key = name.removesuffix("...")
        from_stdin = index == len(argv) - 1 and key not in required
        value = argv_word(name, index, words, from_stdin)
        if value is not None:
            arguments[key] = coerce(tool, key, value)
    return arguments


def argv_word(name, index, words, from_stdin):
    """The word for the argv name at index: the word at its place, or the
    rest of the words joined for a name ending in "..."; for a name that
    may come from stdin (the last, when the schema does not require it)
    with no word left for it, the text on stdin, when there is some."""
    if index < len(words):
        return " ".join(words[index:]) if name.endswith("...") else words[index]
    if from_stdin and index == len(words) and stdin_has_text():
        return sys.stdin.read()
    return None


def run_tool(url, name, arguments, token=None):
    """The hub's answer to one call: {"text", "isError", "exitCode"}."""
    answer = exchange(url, {"tool": name, "arguments": arguments}, token)
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


def prints_on_stderr(tool):
    """True for a tool whose schema says its text goes to stderr. The mark
    is a keyword of the input schema ("x-coordbench-prints": "stderr")
    because the hub's description already carries the schema whole, JSON
    Schema lets a schema carry keywords it does not define, and an MCP
    client ignores one; coop-send carries it, so "sent to agent2" goes
    where coop_msg.py sends it and stdout stays what a caller would pipe."""
    return ((tool or {}).get("inputSchema") or {}).get("x-coordbench-prints") == "stderr"


def report(answer, tool=None):
    """Print the answer where it belongs and give the exit code: a refusal
    on stderr, a result on stdout, or on stderr when the tool says so."""
    to_stderr = bool(answer.get("isError")) or prints_on_stderr(tool)
    show(str(answer.get("text", "")), sys.stderr if to_stderr else sys.stdout)
    return exit_code(answer)


def describe_tool(tool):
    lines = [f"{tool['name']}  (shell: {usage(tool)})"]
    lines += [f"    {line}" for line in (tool.get("description") or "").splitlines()]
    return "\n".join(lines)


def list_tools(args):
    description = describe_agent(args.url, args.token)
    show("\n".join(describe_tool(t) for t in description.get("tools") or []), sys.stdout)
    return OK


def print_prompt(args):
    show(describe_agent(args.url, args.token).get("prompt") or "", sys.stdout)
    return OK


def call(args):
    """A tool by name with its arguments as given. The description types
    the key=value words and says where the text goes; a name the hub does
    not list is left for the hub to refuse, with its own reason."""
    tool = tool_named(describe_agent(args.url, args.token), args.tool)
    return report(run_tool(args.url, args.tool, parse_arguments(args.words, tool), args.token), tool)


def alias_run(args):
    """A tool by its shell command's words. In debug, one "command" event
    closes the invocation: the command the agent typed (the tool's alias,
    or coord-<tool>; the tool's name alone when the hub could not even be
    asked for it), the tool, the exit code the shell sees and the whole
    duration, so a debug run's commands show their timings beside the
    hub's own calls. In prod that is one comparison."""
    began = time.monotonic() if coordtrace.level == "debug" else None
    command, code = args.tool, FAILURE
    try:
        tool = find_tool(describe_agent(args.url, args.token), args.tool)
        command = command_name(tool)
        code = report(run_tool(args.url, args.tool, words_to_arguments(tool, args.words), args.token), tool)
        return code
    finally:
        if began is not None:
            coordtrace.debug("command", command=command, tool=args.tool, exit_code=code,
                             duration_ms=round((time.monotonic() - began) * 1000, 3))


def wrapper_script(tool_name, python, url=None, token_file=None, debug=False):
    """The wrapper's text. A token file is read when the command runs,
    with cat, so the token is in neither this text nor a process list;
    the shell drops the newline the file ends with. The path is written
    absolute (install makes it so), since the command runs from whatever
    directory the agent is in, not the one install ran in. With debug the
    wrapper sets the trace level itself, before cli.py reads it."""
    cli = Path(__file__).resolve()
    lines = ["#!/bin/sh"]
    if url:
        lines.append(f"export COORD_RPC_URL={shlex.quote(url)}")
    if token_file:
        lines.append(f'export {TOKEN_VARIABLE}="$(cat {shlex.quote(str(token_file))})"')
    if debug:
        lines.append(f"export {TRACE_VARIABLE}=debug")
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
    token_file = Path(args.token_file).absolute() if args.token_file else None
    bin_dir = Path(args.bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for tool in describe_agent(args.url, args.token).get("tools") or []:
        script = wrapper_script(tool["name"], args.python, baked_url, token_file, args.debug)
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
    installing.add_argument("--token-file", metavar="PATH",
                            help=f"have the commands read this agent's bearer token from PATH when they "
                                 f"run, as {TOKEN_VARIABLE} (default: the variable as set)")
    installing.add_argument("--debug", action="store_true",
                            help=f"have the commands trace in debug ({TRACE_VARIABLE}=debug) whatever "
                                 f"their environment says")
    installing.set_defaults(func=install)
    return top


def hub_url(args):
    url = getattr(args, "install_url", None) or args.global_url or os.environ.get("COORD_RPC_URL", "")
    if not url.strip():
        raise Failure("no hub URL: pass --url or set COORD_RPC_URL to this agent's /rpc URL")
    return url.strip()


def agent_token(args):
    """The bearer token the hub wants for this agent: the --token-file's
    text on install, else the variable's; empty when there is neither."""
    path = getattr(args, "token_file", None)
    if not path:
        return os.environ.get(TOKEN_VARIABLE, "").strip()
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError as problem:
        raise Failure(f"cannot read the token file {path}: {problem}")


def configure_trace():
    """Where this process's debug events go when the command is the
    process (python3 cli.py ...; a caller that imports cli, as run.py
    does, has listeners of its own and gets none here): one JsonLines on
    the file COORD_TRACE_FILE names, appended, else on stderr, the agent's
    own stream. In prod, and with the stand-in, nothing is registered and
    every event stays one check. Returns the listener, for release_trace,
    or None."""
    if coordtrace.level != "debug":
        return None
    path = os.environ.get(TRACE_FILE_VARIABLE, "").strip()
    return coordtrace.listen(coordtrace.JsonLines(path or sys.stderr))


def release_trace(sink):
    """Take configure_trace's listener off again and close its file; a
    stream it was given stays open, since it is the process's own."""
    if sink is not None:
        coordtrace.unlisten(sink)
        sink.close()


def main(argv=None):
    try:
        args = parser().parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else FAILURE
    try:
        args.url = hub_url(args)
        args.token = agent_token(args)
        return args.func(args)
    except Failure as failure:
        print(f"coordbench: {failure}", file=sys.stderr)
        return FAILURE


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    sink = configure_trace()
    try:
        code = main()
    finally:
        release_trace(sink)
    sys.exit(code)
