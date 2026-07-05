"""Fluxion command-line entry points.

Each module exposes a ``main()`` wired to a console script in pyproject.toml:

- ``main``   → ``fluxion``         (the primary CLI: init/doctor/run)
- ``sub``    → ``fluxion-sub``     (local sub-agent runner)
- ``detect`` → ``fluxion-detect``  (executor detection / env bootstrap)
- ``usage``  → ``fluxion-usage``   (usage/quota probe payloads)
"""
