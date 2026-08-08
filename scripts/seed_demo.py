#!/usr/bin/env python
"""Seed (or clear) demo mode.

    python scripts/seed_demo.py            # add demo data
    python scripts/seed_demo.py --reset    # replace existing demo data
    python scripts/seed_demo.py --clear    # remove demo data only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.deps import get_or_create_user  # noqa: E402
from app.db.seed import clear_demo_data, seed_demo_data  # noqa: E402
from app.db.session import session_scope  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic demo data.")
    parser.add_argument("--reset", action="store_true", help="clear demo data first")
    parser.add_argument("--clear", action="store_true", help="clear demo data and exit")
    parser.add_argument("--no-profile", action="store_true", help="do not seed a demo profile")
    args = parser.parse_args()

    with session_scope() as session:
        user = get_or_create_user(session)
        if args.clear:
            removed = clear_demo_data(session, user)
            print(f"Removed {removed} demo company(ies) and everything attached to them.")
            return 0

        counts = seed_demo_data(
            session, user, reset=args.reset, seed_profile=not args.no_profile
        )
        if not counts["companies"]:
            print("Demo data already present. Use --reset to rebuild it.")
        else:
            print(
                f"Seeded {counts['companies']} companies, {counts['jobs']} jobs, "
                f"{counts['recruiters']} recruiters, {counts['contacts']} contact records."
            )
        print("All demo records are flagged is_demo and use the reserved '.example' TLD.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
