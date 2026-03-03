#!/usr/bin/env bash
# One-time import of historical race results into data/race_results.json
#examples
set -e
uv run pot10.py add --date="2021-07-12" --event="Battersea Park Sri Chinmoy 5km" --distance="5km" --time="00:16:56" --notes="3:15; 3:15; 3:22; 3:36; 3:26. weight: 76.1"
uv run pot10.py add --date="2021-07-17" --event="Battersea Park Sri Chinmoy 5km" --distance="5km" --time="00:16:37" --notes="3:24; 3:12; 3:20; 3:26; 3:13. weight: 76.5"
uv run pot10.py add --date="2021-08-22" --event="Battersea 10km race" --distance="10km" --time="00:37:15"
uv run pot10.py add --date="2021-10-16" --event="Big Half" --distance="21.1km" --time="01:24:44" --notes="hard one"
uv run pot10.py add --date="2021-10-23" --event="Met League number 1 Claybury" --distance="7.8km" --time="00:30:23"
uv run pot10.py add --date="2023-02-04" --event="Sri Chinmoy 10km race" --distance="10km" --time="00:35:41" --notes="17:54 + 17:48"

echo "Done — $(grep -c 'pot10.py add' "$0") races imported."
