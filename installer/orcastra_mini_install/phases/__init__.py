"""Ordered phase registry. Each phase module exposes `TITLE` and `run(ctx)`."""
from . import (p01_preflight, p02_docker, p03_login, p04_wizard, p05_secrets,
               p06_tls, p07_write, p08_compose, p09_health, p10_vault,
               p11_bootstrap, p12_verify, p13_summary)

PHASES = [
    ("preflight", p01_preflight),
    ("docker", p02_docker),
    ("login", p03_login),
    ("wizard", p04_wizard),
    ("secrets", p05_secrets),
    ("tls", p06_tls),
    ("write", p07_write),
    ("compose", p08_compose),
    ("health", p09_health),
    ("vault", p10_vault),
    ("bootstrap", p11_bootstrap),
    ("verify", p12_verify),
    ("summary", p13_summary),
]
