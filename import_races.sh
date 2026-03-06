#!/usr/bin/env bash
set -e

cd "$(dirname "$0")/src"

uv run strava_utils/pot10.py add --date='2021-10-23' --event='Sri Chinmoy 10km' --distance='10km' --time='00:35:41' --notes='17:54 + 17:48'

echo "Done — imported 113 races."
