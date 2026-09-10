"""Per-build in-game Validation Report classes.

One file per build (`four_rax_proxy_validator.py`, `upgrade_rush_validator.py`,
`speedling_all_in_validator.py`, and so on as more builds get added), each a
thin `base_validator.BaseValidator` subclass that does nothing but declare
which stages its own build's report shows and what Stage 4 is titled. See
`base_validator`'s module docstring for why this is split into one file per
build rather than one shared class, and `registry.py` for how a build's name
resolves to its validator class.
"""
