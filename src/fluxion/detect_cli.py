"""Backward-compatible shim for ``python -m fluxion.detect_cli``.

The implementation moved to :mod:`fluxion.cli.detect`. The bundled macOS app
invokes ``-m fluxion.detect_cli``, so this module path is kept as a thin
forwarder. New callers should use ``fluxion.cli.detect`` directly.
"""

from __future__ import annotations

from fluxion.cli.detect import main

if __name__ == "__main__":
    main()
