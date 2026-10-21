"""Fakes shared by the Full installer tests: a recording LXD, state, prompt, secrets and a
context with just the attributes the phases under test read."""
import os
import sys

INSTALLER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, INSTALLER)

from orcastra_full_install import config as C  # noqa: E402
from orcastra_full_install import topology as T  # noqa: E402

IPS = {"vault": "10.77.0.11", "authentik": "10.77.0.12", "opensearch": "10.77.0.13", "cmp": "10.77.0.14"}


class FakeLog:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        return lambda msg: self.lines.append((level, msg))


class FakeState:
    def __init__(self, artifacts=None, phases=None):
        self.artifacts = dict(artifacts or {})
        self.phases = dict(phases or {})

    def is_done(self, name):
        return self.phases.get(name) == "done"

    def artifact(self, key):
        return self.artifacts.get(key)

    def set_artifact(self, key, value):
        self.artifacts[key] = value


class FakeLxd:
    """Answers the queries the cluster code paths make and records every write."""

    def __init__(self, members=(), network=None, networks=(), pools=(), instances=()):
        self.calls = []
        self._members = [{"server_name": m, "status": s} for m, s in members]
        self._network = network
        self._networks = list(networks)
        self._pools = list(pools)
        self._instances = list(instances)
        self.lxc = "lxc"

    def must(self, *args, **kw):
        from orcastra_core.proc import Result
        self.calls.append(args)
        return Result(0, "", "", list(args))

    def network(self, name):
        return self._network

    def networks(self):
        return self._networks

    def cluster_members(self):
        return self._members

    def pools(self):
        return self._pools

    def instances(self, all_projects=False):
        return self._instances

    def instance(self, name):
        return None

    def server(self):
        return {"environment": {"server_version": "5.21.8", "firewall": "nftables",
                                "storage_supported_drivers": [{"Name": "zfs"}]}}


class FakePrompt:
    """Answers with the default, or with a scripted answer per key; confirm and secrets are
    scripted too."""

    def __init__(self, answers=None, confirm=False, secret=""):
        self.defaults = {}
        self.answers = dict(answers or {})
        self.confirm_answer = confirm
        self.secret = secret
        self.asked_secret = []

    def ask(self, question, default=None, key=None, validate=None):
        self.defaults[key] = default
        ans = self.answers.get(key, default)
        err = validate(ans) if validate and ans is not None else None
        if err:
            raise AssertionError(f"scripted answer for {key} rejected: {err}")
        return ans

    def confirm(self, question, default=False):
        return self.confirm_answer

    def ask_secret(self, question, validate=None, confirm=True):
        self.asked_secret.append(question)
        return self.secret


class FakeSecrets:
    def __init__(self, data=None):
        self.data = dict(data or {})

    def maybe(self, key):
        return self.data.get(key)

    def get(self, key):
        return self.data[key]

    def put(self, key, value):
        self.data[key] = value


class FakeCtx:
    def __init__(self, member=None, lxd=None, values=None, answered=(), interactive=False,
                 artifacts=None, facts=None):
        self.cluster_member = member
        self.lxd = lxd or FakeLxd()
        self.values = dict(values or {})
        self.answered = set(answered)
        self.interactive = interactive
        self.state = FakeState(artifacts)
        self.facts = dict(facts or {})
        self.log = FakeLog()
        self.prompt = FakePrompt()
        self.secrets = FakeSecrets()
        self.project = "orcastra"
        self.proc = None
        self.is_vm = True
        self.dry_run = False
        self.bridge_gateway = "10.77.0.1"
        self.sizes = T.SIZING["compact"]

    def ip(self, role):
        return IPS[role]

    @property
    def domain_mode(self):
        return not C.is_off(self.values.get("PUBLIC_DOMAIN"))
