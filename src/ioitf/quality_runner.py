"""Compatibility entry point for the old coverage command."""

import sys
from equilane import quality_runner as _runner

if __name__ == "__main__":
    raise SystemExit(_runner.main())
else:
    sys.modules[__name__] = _runner
