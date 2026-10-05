"""Unit tests for orcastra_core (shared by the Mini and Full installers).

Run:  cd installer && python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orcastra_core import answers, log, prompt, retry, state  # noqa: E402
from orcastra_core.errors import AbortByUser, ConfigError, InstallError  # noqa: E402


class _Log:
    def __init__(self):
        self.lines = []

    def detail(self, m):
        self.lines.append(m)


class Redaction(unittest.TestCase):
    def test_longest_secret_masked_whole(self):
        r = log.Redactor()
        r.add("abcd")
        r.add("abcdefgh")
        self.assertEqual(r.scrub("x abcdefgh y abcd"), "x *** y ***")

    def test_short_values_ignored(self):
        r = log.Redactor()
        r.add("abc")
        self.assertEqual(r.scrub("abc"), "abc")

    def test_log_file_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.log")
            lg = log.Log(p, color=False, name="t-private")
            lg.add_secret("s3cr3t-value")
            lg.info("token=s3cr3t-value")
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            with open(p, encoding="utf-8") as fh:
                self.assertNotIn("s3cr3t-value", fh.read())


class StateLedger(unittest.TestCase):
    def test_dry_run_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "sub", "state.json")
            st = state.State.load(p, dry_run=True)
            st.set_phase("x", "done")
            st.set_artifact("a", 1)
            self.assertFalse(os.path.exists(p))
            self.assertFalse(os.path.exists(os.path.dirname(p)))

    def test_corrupt_strict_raises(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            with open(p, "w") as fh:
                fh.write("{not json")
            with self.assertRaises(InstallError):
                state.State.load(p, strict=True)
            self.assertTrue(os.path.exists(p))  # evidence kept

    def test_corrupt_lenient_moves_aside(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            with open(p, "w") as fh:
                fh.write("[]")
            st = state.State.load(p)
            self.assertIsNotNone(st.recovered_from)
            self.assertTrue(os.path.exists(st.recovered_from))
            self.assertFalse(os.path.exists(p))

    def test_mode_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "state.json")
            st = state.State.load(p)
            st.set_phase("a", "done")
            st.reset_phases(["a"])
            st.set_artifact("k", {"v": 1})
            self.assertEqual(os.stat(p).st_mode & 0o777, 0o600)
            st2 = state.State.load(p, strict=True)
            self.assertEqual(st2.phase_status("a"), "pending")
            self.assertEqual(st2.artifact("k"), {"v": 1})


class Answers(unittest.TestCase):
    def test_file_quotes_comments_and_case(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.env")
            with open(p, "w") as fh:
                fh.write("# c\n\nip_vault=10.0.0.1\nNAME='a b'\nX=\"q\"\nnoequals\n")
            a = answers.parse_answer_file(p)
            self.assertEqual(a, {"IP_VAULT": "10.0.0.1", "NAME": "a b", "X": "q"})

    def test_env_prefix_and_installer_vars_skipped(self):
        env = {"ORCASTRA_HOST_ADDRESS": "1.2.3.4", "ORCASTRA_INSTALLER_URL": "u", "PATH": "/bin"}
        self.assertEqual(answers.env_answers(environ=env), {"HOST_ADDRESS": "1.2.3.4"})

    def test_file_beats_env(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.env")
            with open(p, "w") as fh:
                fh.write("HOST_ADDRESS=9.9.9.9\n")
            merged = answers.combined_answers(p, environ={"ORCASTRA_HOST_ADDRESS": "1.1.1.1",
                                                          "ORCASTRA_SIZING": "custom"})
            self.assertEqual(merged["HOST_ADDRESS"], "9.9.9.9")
            self.assertEqual(merged["SIZING"], "custom")

    def test_as_bool(self):
        self.assertTrue(answers.as_bool("Yes"))
        self.assertFalse(answers.as_bool("0"))
        with self.assertRaises(ValueError):
            answers.as_bool("maybe")


class Prompts(unittest.TestCase):
    def test_noninteractive_default_and_validation(self):
        p = prompt.Prompter(_Log(), interactive=False)
        self.assertEqual(p.ask("q", default="a"), "a")
        with self.assertRaises(ConfigError):
            p.ask("q", default="bad", validate=lambda v: "nope")
        with self.assertRaises(AbortByUser):
            p.ask("q")
        self.assertFalse(p.confirm("c", default=False))
        self.assertTrue(prompt.Prompter(_Log(), interactive=False, assume_yes=True).confirm("c"))

    def test_interactive_reads_stream_and_revalidates(self):
        import io
        stream = io.StringIO("bad\ngood\n")
        p = prompt.Prompter(_Log(), interactive=True, tty=stream)
        self.assertEqual(p.ask("q", validate=lambda v: None if v == "good" else "no"), "good")

    def test_eof_aborts_instead_of_looping(self):
        import io
        p = prompt.Prompter(_Log(), interactive=True, tty=io.StringIO(""))
        with self.assertRaises(AbortByUser):
            p.ask("q")


class ProcQuiet(unittest.TestCase):
    def test_quiet_output_keeps_stdout_out_of_the_log(self):
        from orcastra_core.proc import Proc
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.log")
            lg = log.Log(p, color=False, name="t-quiet")
            pr = Proc(lg)
            r1 = pr.run(["echo", "visible-output"])
            r2 = pr.run(["echo", "$2y$12$secret-hash"], quiet_output=True)
            self.assertTrue(r1.ok and r2.ok)
            self.assertIn("secret-hash", r2.out)
            with open(p, encoding="utf-8") as fh:
                body = fh.read()
            self.assertIn("visible-output", body)
            self.assertNotIn("secret-hash", body.split("run: echo $2y$12$secret-hash")[-1])

    def test_stdin_never_inherited(self):
        from orcastra_core.proc import Proc
        r = Proc(_Log2()).run(["cat"], timeout=5)
        self.assertEqual(r.rc, 0)  # DEVNULL: cat ends at once instead of waiting on a tty


class _Log2:
    redactor = log.Redactor()

    def debug(self, m):
        pass


class TerminalUnderPipe(unittest.TestCase):
    """The `curl ... | bash` case: stdin is a pipe, answers must come from /dev/tty."""

    def test_open_tty_with_piped_stdin(self):
        import pty
        code = ("import sys; sys.path.insert(0, %r); from orcastra_core.prompt import open_tty; "
                "t = open_tty(); print('TTY-OK' if t is not None and t.isatty() else 'TTY-NONE'); "
                "print('ANSWER=' + (t.readline().strip() if t else ''))"
                % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        pid, fd = pty.fork()
        if pid == 0:
            os.execvp("bash", ["bash", "-c", "echo ignored | %s -c %s" % (sys.executable, repr(code))])
        os.write(fd, b"typed-answer\n")
        out = b""
        while True:
            try:
                chunk = os.read(fd, 1024)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        os.waitpid(pid, 0)
        text = out.decode(errors="replace")
        self.assertIn("TTY-OK", text)
        self.assertIn("ANSWER=typed-answer", text)


class Retry(unittest.TestCase):
    def test_retry_until_ok(self):
        calls = []

        def fn():
            calls.append(1)
            return len(calls) >= 3
        self.assertTrue(retry.retry(fn, attempts=5, delay=0))
        self.assertEqual(len(calls), 3)

    def test_wait_until_timeout(self):
        self.assertFalse(retry.wait_until(lambda: False, timeout=0.05, interval=0.01))
        self.assertTrue(retry.wait_until(lambda: True, timeout=1))


if __name__ == "__main__":
    unittest.main()
