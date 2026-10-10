"""One stream of timings and events for the hub, the runner and the arms.

This module imports nothing from coordbench, so it can be copied alone into
a container or another repository. It has three levels, read once from the
environment variable COORD_TRACE (default prod), set by set_level(name):

- off: nothing is recorded; event() returns after one check.
- prod: the passive production listener. Every event costs one check, one
  dict and one json.dumps per listener; nothing is computed beyond what
  the caller already had in hand, so it rides along in a deployment.
- debug: everything the playground records to find where time goes, the
  debug() events and debug_span() timings on top of prod's. Never a
  default: the hub and the runner switch it on with --debug.

A reader adds logging in one line, anywhere:

    trace.event("message", agent="agent1", to="agent2")   # one event
    with trace.span("provision", agent="agent1"): ...     # two: start, _done
    @trace.span("score")                                  # the same, on a def
    trace.debug("queue", depth=len(queue))                # only in debug

Every event is a dict {"kind", "t", "ts", **fields}: t is seconds since
import (started, a monotonic reference), ts is epoch seconds; a field named
like one of these three replaces it, so a caller with its own clock keeps
it. A span's closing event, <name>_done, carries duration_ms (a float,
three decimals) and ok (False when the block raised; the exception
propagates). Listeners are callables that take the dict and must not
raise: one that raises is dropped after one complaint on stderr, so a bad
sink never breaks the hub. JsonLines writes one line per event, flushed,
to a path (opened lazily, append mode) or a stream; Memory is a list that
is also a listener, for tests and a run's in-memory events.
"""

import functools
import json
import os
import sys
import threading
import time

LEVELS = ("off", "prod", "debug")

started = time.monotonic()
_lock = threading.Lock()
_listeners = []
_sinks = ()


def _level_from_environment():
    """COORD_TRACE, read once at import. A value that is no level is taken
    as prod under a complaint: a typo must not keep the hub from starting."""
    name = os.environ.get("COORD_TRACE", "prod").strip().lower()
    if name in LEVELS:
        return name
    print(f"trace: COORD_TRACE={name!r} is none of {', '.join(LEVELS)}; tracing at prod", file=sys.stderr)
    return "prod"


level = _level_from_environment()


def _rewire():
    """The one global the fast path reads: the listeners unless the level is off. Under _lock."""
    global _sinks
    _sinks = tuple(_listeners) if level != "off" else ()


def set_level(name):
    global level
    if name not in LEVELS:
        raise ValueError(f"trace level {name!r} is none of {', '.join(LEVELS)}")
    with _lock:
        level = name
        _rewire()


def now_ms():
    """Milliseconds since started, three decimals like duration_ms, for a caller timing a stretch itself."""
    return round((time.monotonic() - started) * 1000, 3)


def event(kind, **fields):
    """Hand one event to every listener; the first line is the whole cost when off or unheard."""
    if not _sinks:
        return
    record = {"kind": kind, "t": time.monotonic() - started, "ts": time.time(), **fields}
    for listener in _sinks:
        try:
            listener(record)
        except Exception as error:
            _drop(listener, error)


def debug(kind, **fields):
    """event(), in debug only; one comparison otherwise."""
    if level != "debug":
        return
    event(kind, **fields)


def _drop(listener, error):
    print(f"trace: listener {listener!r} raised {error!r}; it gets no more events", file=sys.stderr)
    unlisten(listener)


def listen(listener):
    """Register a listener and give it back, so it works as a decorator.
    Told apart by identity: two empty Memory lists compare equal."""
    with _lock:
        if not any(known is listener for known in _listeners):
            _listeners.append(listener)
        _rewire()
    return listener


def unlisten(listener):
    """Remove a listener; one already dropped, or never registered, is a no-op."""
    with _lock:
        _listeners[:] = [known for known in _listeners if known is not listener]
        _rewire()


class Span:
    """A timed stretch, as a context manager or a decorator; span() and debug_span() make
    one. The decorator form runs a fresh Span per call, so threads each measure their own."""

    def __init__(self, name, fields, debug_only=False):
        self.name, self.fields, self.debug_only = name, fields, debug_only
        self.active, self.start = False, 0.0

    def __enter__(self):
        if self.debug_only and level != "debug":
            return self
        self.active, self.start = True, time.monotonic()
        event(self.name, **self.fields)
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.active:
            self.active = False
            duration = round((time.monotonic() - self.start) * 1000, 3)
            event(self.name + "_done", duration_ms=duration, ok=exc is None, **self.fields)
        return False

    def __call__(self, function):
        @functools.wraps(function)
        def traced(*args, **kwargs):
            with Span(self.name, self.fields, self.debug_only):
                return function(*args, **kwargs)
        return traced


def span(name, **fields):
    """event(name, **fields) on enter, event(name + "_done", duration_ms,
    ok, **fields) on exit; the decorator form adds no fields of its own."""
    return Span(name, fields)


def debug_span(name, **fields):
    """span(), in debug only; a comparison on enter, a flag on exit otherwise."""
    return Span(name, fields, debug_only=True)


class JsonLines:
    """The passive production listener: one json.dumps and one write per
    event, flushed at once, so a crashed process leaves every event it saw.
    A path is opened on the first event, in append mode, so a run that
    emits nothing leaves no file; a stream (anything with write) is left
    open by close(), since the caller owns it. A field that is not JSON (a
    Path, a set) is written as its repr rather than costing the sink."""

    def __init__(self, path_or_stream):
        self.lock, self.opened = threading.Lock(), False
        if hasattr(path_or_stream, "write"):
            self.path, self.stream = None, path_or_stream
        else:
            self.path, self.stream = path_or_stream, None

    def __call__(self, event):
        with self.lock:
            if self.stream is None:
                self.stream, self.opened = open(self.path, "a", encoding="utf-8"), True
            self.stream.write(json.dumps(event, default=repr) + "\n")
            self.stream.flush()

    def close(self):
        with self.lock:
            if self.opened:
                self.stream.close()
                self.stream, self.opened = None, False


class Memory(list):
    """A list that is also a listener: a test or a run reads it as the
    plain list of its events."""

    def __call__(self, event):
        self.append(event)
