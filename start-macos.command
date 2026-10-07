#!/bin/bash
cd "$(dirname "$0")" || exit 1
export PYTHONUTF8=1
for launcher_python in python3.13 python3.12 python3.11 python3; do
    if command -v "$launcher_python" >/dev/null 2>&1 && "$launcher_python" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
        "$launcher_python" start.py "$@"
        launcher_status=$?
        if [ "$launcher_status" -ne 0 ]; then
            read -r -p "Press Enter to close..." _
        fi
        exit "$launcher_status"
    fi
done
echo "Python 3.11+ is required. Install it from https://www.python.org/downloads/"
read -r -p "Press Enter to close..." _
exit 1
