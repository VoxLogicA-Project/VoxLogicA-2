# CLAUDE.md

The working agreement for this repository lives in **[AGENTS.md](AGENTS.md)**.
Read it before touching anything here. It is short, and every rule in it is
there because breaking it already cost something.

What it covers:

- **Multi-machine work.** Edit locally, commit, push, pull on the remote. Never
  edit or copy files onto the compute host as a substitute for committing.
- **Long-running commands.** How to launch a run so it survives, and how the
  user watches it.
- **Syncing clones.**
- **Where the time went.** `--profile` is deprecated and can be arbitrarily
  wrong; what to use instead.
- **Communication mistakes to avoid.**

Two more documents are worth knowing about before starting:

- `HANDOVER.md` — the engine's state, and the measurements behind its defaults.
- `VERIFICATION.md` — the known failure classes and what would actually catch
  each one.

Personal preferences (tone, verbosity) belong in your own
`~/.claude/CLAUDE.md`, not here. This file and `AGENTS.md` are what everyone
working on this repository shares.
