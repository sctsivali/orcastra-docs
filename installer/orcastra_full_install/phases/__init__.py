"""Ordered phase registry. Each module exposes TITLE and run(ctx). Phases in ALWAYS run
on every install pass (they only validate or are cheap and idempotent)."""
from . import (p01_preflight, p02_wizard, p03_secrets, p04_lxd, p05_instances, p06_ssh,
               p07_base, p08_firewall, p09_vault, p10_authentik, p11_opensearch,
               p12_vault_logging, p13_cmp, p14_exposure, p15_watchdog, p16_verify,
               p17_summary)

PHASES = [
    ("preflight", p01_preflight),
    ("wizard", p02_wizard),
    ("secrets", p03_secrets),
    ("lxd", p04_lxd),
    ("instances", p05_instances),
    ("ssh", p06_ssh),
    ("base", p07_base),
    ("firewall", p08_firewall),
    ("vault", p09_vault),
    ("authentik", p10_authentik),
    ("opensearch", p11_opensearch),
    ("vault-logging", p12_vault_logging),
    ("cmp", p13_cmp),
    ("exposure", p14_exposure),
    ("watchdog", p15_watchdog),
    ("verify", p16_verify),
    ("summary", p17_summary),
]

ALWAYS = {"preflight", "wizard", "verify", "summary"}
