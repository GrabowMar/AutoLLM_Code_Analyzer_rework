#!/bin/sh
cd /workspace || exit 70
exec goose run --no-session --no-profile --output-format stream-json --with-builtin developer --max-turns 40 -i /run/brief.md
