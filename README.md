# Swytch distributed control plane

This repository coordinates MACCA2026 and SNAPDRAGON without sharing product
working trees or Git common directories. One branch = one active worktree = one
machine. Control state is authoritative only after a committed, fast-forward
push.

Before work: `refresh` → `status` → `check` → `claim`.
While working: `heartbeat`.
Finish: commit product work → `complete` or `handoff`.
Integration requires `canonical-integration`; migrations require
`migration-allocation`; deployment requires `deployment` and capability; live
ATZ writes require `live-atz-write` and capability.

Run from this checkout with `python tools/swytch_control.py <command>`. Every
mutating command refreshes with `git pull --ff-only`, reloads state, checks
conflicts, changes only its control records, commits, and pushes. Push races
are retried at most three times; force-push and automatic ownership merging are
never used.
