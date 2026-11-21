#!/bin/bash
exec git -C /tmp/claude-0/-home-user-code-canvas/f73966bb-7be3-5269-bc4d-ca2eebd0fcae/scratchpad/corpus2/sglang -c safe.directory='*' -c core.hooksPath=/dev/null -c gc.auto=0 "$@"
