"""Phase 16 - End-to-end verification across every layer. Also available any time as
`orcastra-full verify`. Fails the run when any check fails."""
from __future__ import annotations

from typing import List

from orcastra_core.errors import VerifyError

from .. import verify_app, verify_infra

TITLE = "End-to-end verification"


def run(ctx) -> None:
    failed: List[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        (ctx.log.ok if ok else ctx.log.error)(f"{label}" + (f"  ({detail})" if detail else ""))
        if not ok:
            failed.append(label)

    verify_infra.all_checks(ctx, check)
    verify_app.all_checks(ctx, check)
    verify_infra.logs(ctx, check)
    if failed:
        raise VerifyError(f"{len(failed)} check(s) failed: " + "; ".join(failed),
                          remediation="Fix the items above, then run `orcastra-full verify`.")
    ctx.log.ok("All checks passed")
