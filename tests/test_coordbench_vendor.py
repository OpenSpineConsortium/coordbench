"""coordbench/vendor holds a copy of sdlc-protocol, the library whose merge
gate the sdlc arm decides through. The library's scripts/vendor.py makes
the copy, and its sync workflow lands each new one here as the pull
request sdlc-protocol/sync; coordbench/vendor/VENDORED records it, the
version and the commit on its first line and then the sha256 of every
copied file. scripts/check.sh holds the copy to that record with the
copy's own vendoring.py before any test runs. These tests hold the record
to a release and a clean commit and to the files it lists, the copy to the
library itself where a clone of it is at hand, and the arm to deciding
with that copy, under the policy it states, and in its own words where the
gate's leave the arm's commands unnamed. The library's own tests cover the
gate itself; tests/test_coordbench_arm_sdlc.py shows the arm keeps every
promise it made before the gate moved there.

The record sits in this tree beside the copy, so a pull request that
edits the copy can rewrite the file's line in VENDORED too, and the copy's
own check passes; the comparison with the library is what fails it. It
reads the clone SDLC_PROTOCOL names, which this repository's CI makes and
names (.github/workflows/ci.yml), and fails there when the clone is
missing or has no commit of the one VENDORED names, so a made-up commit
does not pass as skipped. With SDLC_PROTOCOL unset it reads a clone
beside this repository, as the clones sit on a developer's machine and in
the library's downstream job, and skips when there is none or when it is
behind."""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COORDBENCH = ROOT / "coordbench"
VENDOR = COORDBENCH / "vendor"
VENDORED = VENDOR / "VENDORED"
PACKAGE = VENDOR / "sdlc_protocol"
LIBRARY = Path(os.environ.get("SDLC_PROTOCOL") or ROOT.parent / "sdlc-protocol")
# A clone named on purpose, as CI names its own, is one the comparison must
# have; a clone that only happens to sit beside this one may be absent or
# behind, and then the comparison is skipped.
LIBRARY_NAMED = bool(os.environ.get("SDLC_PROTOCOL"))
CI = ROOT / ".github" / "workflows" / "ci.yml"
CLONE = 'git clone --quiet https://github.com/OpenSpineConsortium/sdlc-protocol "$RUNNER_TEMP/sdlc-protocol"'
NAMED_IN_CI = "SDLC_PROTOCOL: ${{ runner.temp }}/sdlc-protocol"
COMPARISON = "TheCopyIsTheLibrarysTest.test_every_file_is_the_librarys_at_the_recorded_commit"
sys.path.insert(0, str(COORDBENCH))

from arm import Call, Run, ToolError, find_arm  # noqa: E402

# Read here with patterns of this repository's own rather than with the
# copy's vendoring.py, so a copy whose check was itself edited is still
# held to the record.
FIRST_LINE = re.compile(r"sdlc-protocol ([0-9]+\.[0-9]+\.[0-9]+) ([0-9a-f]{40})")
FILE_LINE = re.compile(r"([0-9a-f]{64})  (\S+)")


def record_lines():
    return VENDORED.read_text(encoding="ascii").splitlines()


def recorded_release():
    """The version and the commit the record's first line names."""
    first = (record_lines() or [""])[0]
    match = FIRST_LINE.fullmatch(first)
    if not match:
        raise AssertionError(f"VENDORED's first line does not name a version and a clean commit: {first!r}")
    return match.groups()


def recorded_files():
    """The record's lines after the first, as (path, sha256) in their order."""
    found = []
    for line in record_lines()[1:]:
        match = FILE_LINE.fullmatch(line)
        if not match:
            raise AssertionError(f"{VENDORED} has a line that is not '<sha256>  <path>': {line!r}")
        found.append((match.group(2), match.group(1)))
    return found


def package_files():
    """The copy's files, relative to it, without Python's caches, which
    running the tests writes there and git ignores."""
    return sorted(path.relative_to(PACKAGE).as_posix() for path in PACKAGE.rglob("*")
                  if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")


def verify(vendor_dir):
    """The copy's own check, run as scripts/check.sh runs it; -B keeps
    Python's caches out of the tree."""
    return subprocess.run([sys.executable, "-B", str(Path(vendor_dir) / "sdlc_protocol" / "vendoring.py"),
                           "verify", str(vendor_dir)], capture_output=True, text=True)


def library(*args):
    return subprocess.run(["git", "-C", str(LIBRARY), *args], capture_output=True)


def why_the_library_cannot_compare(commit):
    """What stands in the way of comparing the copy with the library at
    commit, or "" when nothing does."""
    if not (LIBRARY / ".git").exists():
        return f"no clone of sdlc-protocol at {LIBRARY}"
    if library("cat-file", "-e", f"{commit}^{{commit}}").returncode != 0:
        return (f"the clone at {LIBRARY} does not have {commit}, the commit VENDORED names: "
                "vendor a commit of sdlc-protocol's, never write one into the record by hand")
    return ""


def ci_steps():
    """The steps of ci.yml's one job, each as its lines with the indent
    stripped. The workflow is read as text, the standard library having no
    YAML parser; each step starts with a dash six spaces in."""
    steps = CI.read_text(encoding="utf-8").split("\n    steps:\n", 1)[1]
    return [[line.strip() for line in step.splitlines()] for step in re.split(r"\n(?=      - )", steps)]


def without_git_variables():
    """The environment without git's own variables, which would point a
    git the tests start at another repository than the one they name."""
    return {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}


def compare_with(clone):
    """The comparison with the library run on its own, in a process of its
    own, with SDLC_PROTOCOL naming clone, as CI names its own."""
    env = dict(without_git_variables(), SDLC_PROTOCOL=str(clone))
    return subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), COMPARISON],
                          cwd=ROOT, env=env, capture_output=True, text=True)


def arm_globals():
    """The sdlc arm module's names, as the hub loads it."""
    return type(find_arm("sdlc")).setup.__globals__


class VendoredCopyTest(unittest.TestCase):
    def test_vendored_names_a_release_and_a_commit_and_the_copy_is_that_release(self):
        version, _ = recorded_release()
        package = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
        self.assertIn(f'__version__ = "{version}"\n', package)

    def test_vendored_lists_every_file_of_the_copy_once_in_order_with_its_sha256(self):
        listed = recorded_files()
        self.assertTrue(listed, "VENDORED lists no files: it is the old record of one line; re-vendor")
        self.assertEqual([path for path, _ in listed], package_files())
        for path, digest in listed:
            with self.subTest(file=path):
                self.assertEqual(hashlib.sha256((PACKAGE / path).read_bytes()).hexdigest(), digest)

    def test_the_copys_own_check_passes_as_check_sh_runs_it(self):
        version, commit = recorded_release()
        done = verify(VENDOR)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(done.stdout, f"vendored sdlc-protocol {version} at {commit[:7]}: "
                                      f"{len(package_files())} files match VENDORED\n")

    def test_the_copys_own_check_refuses_a_hand_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "vendor"
            shutil.copytree(VENDOR, copy, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            with open(copy / "sdlc_protocol" / "gate.py", "a", encoding="ascii") as gate:
                gate.write("# a hand edit\n")
            done = verify(copy)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("gate.py was edited by hand", done.stdout)

    def test_every_vendored_file_is_ascii_with_lf_and_a_final_newline(self):
        files = [path for path in sorted(VENDOR.rglob("*"))
                 if path.is_file() and "__pycache__" not in path.parts]
        self.assertTrue(files)
        for path in files:
            with self.subTest(file=str(path.relative_to(VENDOR))):
                data = path.read_bytes()
                self.assertNotIn(b"\r", data)
                self.assertTrue(all(byte < 128 for byte in data))
                self.assertTrue(data.endswith(b"\n"))


class TheCopyIsTheLibrarysTest(unittest.TestCase):
    def test_every_file_is_the_librarys_at_the_recorded_commit(self):
        _, commit = recorded_release()
        missing = why_the_library_cannot_compare(commit)
        if missing and LIBRARY_NAMED:
            self.fail(f"{missing} (SDLC_PROTOCOL names it)")
        if missing:
            self.skipTest(missing)
        listed = library("ls-tree", "-r", "--name-only", commit, "--", "sdlc_protocol").stdout
        theirs = sorted(name.removeprefix("sdlc_protocol/") for name in listed.decode("utf-8").splitlines())
        self.assertEqual(package_files(), theirs)
        for name in theirs:
            with self.subTest(file=name):
                self.assertEqual((PACKAGE / name).read_bytes(),
                                 library("show", f"{commit}:sdlc_protocol/{name}").stdout)


class TheComparisonOnCITest(unittest.TestCase):
    """CI clones sdlc-protocol and names the clone to the checks, so the
    comparison with the library runs on every pull request; where the clone
    it names is missing, or has no commit of the one VENDORED names, the
    comparison fails rather than skips."""

    def test_ci_clones_the_library_and_names_the_clone_to_the_checks(self):
        steps = ci_steps()
        clone = next(number for number, step in enumerate(steps) if f"run: {CLONE}" in step)
        checks = next(number for number, step in enumerate(steps) if "run: bash scripts/check.sh" in step)
        self.assertLess(clone, checks)
        self.assertIn(NAMED_IN_CI, steps[checks])

    def test_a_named_clone_that_is_not_there_fails_the_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            done = compare_with(Path(tmp) / "sdlc-protocol")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("no clone of sdlc-protocol at", done.stderr)
        self.assertNotIn("skipped", done.stderr)

    def test_a_named_clone_without_the_recorded_commit_fails_the_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "--quiet", tmp], check=True, capture_output=True,
                           env=without_git_variables())
            done = compare_with(tmp)
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("the commit VENDORED names", done.stderr)
        self.assertNotIn("skipped", done.stderr)


class TheSdlcArmDecidesWithTheCopyTest(unittest.TestCase):
    def test_the_gate_and_the_world_are_the_vendored_ones(self):
        names = arm_globals()
        package = VENDOR / "sdlc_protocol"
        self.assertEqual(Path(names["gate"].__file__).resolve(), package / "gate.py")
        self.assertEqual(Path(names["bare_git"].__file__).resolve(), package / "adapters" / "bare_git.py")

    def test_the_gate_merges_and_a_branch_brings_main_in_by_merging_it(self):
        names = arm_globals()
        gate, policy = names["gate"], names["POLICY"]
        self.assertEqual((policy.who_merges, policy.bring_main_in), (gate.GATE, gate.MERGE))
        self.assertEqual((policy.required_check, policy.remote, policy.default_branch), ("", "team", "main"))

    def test_two_refusals_name_the_arms_commands_as_they_did_before_the_move(self):
        """The gate's words for these two say what to do next without naming
        a tool; the arm's answers named its commands there before the gate
        moved to the library, and still do. Neither refusal touches git, so
        the shared remote named here need not exist."""
        with tempfile.TemporaryDirectory() as tmp:
            arm = find_arm("sdlc")
            run = Run("r1", arm, arm.layout([1, 2]), {"git_remote_host": str(Path(tmp) / "none.git"),
                                                      "base_ref": "0a1b2c3"})
            arm.setup(run)
            self.addCleanup(arm.teardown, run)
            answers = {}
            for tool, issue in (("open_pull_request", 1), ("mark_ready", 2)):
                try:
                    arm.tools_for(run, "agent1")[tool].handler(Call(run, "agent1", {"issue": issue}))
                except ToolError as refusal:
                    answers[tool] = (str(refusal), refusal.exit_code)
        self.assertEqual(answers["open_pull_request"],
                         ("only the agent holding the claim on #1 opens its pull request, and nobody has "
                          "claimed it; claim it first with sdlc-claim 1", 1))
        self.assertEqual(answers["mark_ready"],
                         ("#2 has no pull request; open it with open_pull_request (sdlc-pr 2) once claude/2 "
                          "is pushed", 1))


if __name__ == "__main__":
    unittest.main()
