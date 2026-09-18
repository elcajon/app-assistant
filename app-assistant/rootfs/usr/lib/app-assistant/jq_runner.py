"""Set subprocess resource limits before replacing this process with jq."""

import os
import resource
import sys

resource.setrlimit(resource.RLIMIT_CPU, (4, 4))
resource.setrlimit(resource.RLIMIT_FSIZE, (1024 * 1024, 1024 * 1024))
# RLIMIT_AS is effective on Linux, where the app runs.
if sys.platform == "linux":
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
os.execvp("jq", ["jq", "-L", "/nonexistent", "-c", "--", sys.argv[1]])
