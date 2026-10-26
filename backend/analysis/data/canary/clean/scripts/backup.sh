#!/bin/sh
# Copies the bookmarks database to the backup directory given as $1.
set -eu
cd "$1" || exit 1
cp "$DATABASE_PATH" backup/
