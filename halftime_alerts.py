#!/usr/bin/env python3
"""
NBA halftime alerts -> phone, via ntfy.

For every NBA game today, sends exactly two push notifications:
  1. when the game goes into halftime
  2. one minute before halftime ends (14 minutes after it started)

No scores, nothing else. Uses only the Python standard library.

Runs in the cloud on GitHub Actions (see halftime.yml), so no computer of
yours needs to be on. The ntfy topic comes from the NTFY_TOPIC secret.

  python halftime_alerts.py --test            send one test notification
  python halftime_alerts.py --max-hours 5.5   watch games, exit when the day's
                                              games are over or time is up
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

# ---------------------------------------------------------------- settings
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip() or "CHANGE-ME"
NTFY_SERVER = "https://ntfy.sh"

HALFTIME_MINUTES = 15               # NBA halftime length
WARN_BEFORE_END_MINUTES = 1         # second alert fires this long before the end

POLL_SECONDS_LIVE = 5               # how often to check while games are in progress
POLL_SECONDS_IDLE = 60              # how often to check when nothing is live
# -------------------------------------------------------------------------

SCOREBOARD_URL = (
    "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json"
)
USER_AGENT = "Mozilla/5.0 (halftime-alerts)"


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def fetch_games():
    req = urllib.request.Request(SCOREBOARD_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)["scoreboard"]["games"]


def notify(title, message):
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "high",      # heads-up banner + vibration
            "Tags": "basketball",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10):
        pass
    log(f"SENT: {title} | {message}")


def safe_notify(title, message, retries=3):
    for attempt in range(1, retries + 1):
        try:
            notify(title, message)
            return True
        except Exception as e:
            log(f"Notification failed (try {attempt}/{retries}): {e}")
            time.sleep(2)
    return False


def matchup(game):
    return f"{game['awayTeam']['teamTricode']} @ {game['homeTeam']['teamTricode']}"


def is_halftime(game):
    return (
        game.get("gameStatus") == 2
        and "half" in game.get("gameStatusText", "").lower()
    )


def is_live(game):
    return game.get("gameStatus") == 2


def day_is_over(games, seen_unfinished):
    """True when every listed game is final and the list is today's, not a
    leftover of yesterday's slate that the NBA hasn't replaced yet."""
    if not games or any(g.get("gameStatus") != 3 for g in games):
        return False
    if seen_unfinished:
        return True
    try:
        newest = max(
            datetime.strptime(g["gameTimeUTC"], "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
            for g in games
        )
        return (datetime.now(timezone.utc) - newest).total_seconds() < 12 * 3600
    except Exception:
        return False


def run(max_hours=None):
    started = time.monotonic()
    seen_unfinished = False
    warn_delay = (HALFTIME_MINUTES - WARN_BEFORE_END_MINUTES) * 60
    start_sent = set()     # gameIds that got the "halftime started" alert
    warn_due = {}          # gameId -> (monotonic time the warning is due, matchup)

    log("Watching today's NBA games.")
    while True:
        live = False
        fetched = True
        try:
            games = fetch_games()
        except Exception as e:
            log(f"Could not load scoreboard: {e}")
            games = []
            fetched = False
            live = True

        for g in games:
            gid = g["gameId"]
            live = live or is_live(g)

            if is_halftime(g) and gid not in start_sent:
                start_sent.add(gid)
                warn_due[gid] = (time.monotonic() + warn_delay, matchup(g))
                safe_notify("Halftime started", matchup(g))

            # Second half started before the warning fired: drop the warning.
            elif gid in warn_due and g.get("period", 0) >= 3:
                log(f"{warn_due[gid][1]}: second half already started, skipping warning")
                del warn_due[gid]

        now = time.monotonic()
        for gid, (due, name) in list(warn_due.items()):
            if now >= due:
                del warn_due[gid]
                safe_notify("1 minute left in halftime", name)

        # Decide whether to stop. Never stop in the middle of a halftime.
        if fetched and not warn_due and not any(is_halftime(g) for g in games):
            if any(g.get("gameStatus") != 3 for g in games):
                seen_unfinished = True
            if day_is_over(games, seen_unfinished):
                log("All of today's games are final. Done.")
                return
            if max_hours and time.monotonic() - started > max_hours * 3600:
                log("Time limit reached, handing off to the next run.")
                return

        # Sleep, but never past the next pending warning.
        sleep_for = POLL_SECONDS_LIVE if (live or warn_due) else POLL_SECONDS_IDLE
        if warn_due:
            next_due = min(due for due, _ in warn_due.values())
            sleep_for = min(sleep_for, max(0.5, next_due - time.monotonic()))
        time.sleep(sleep_for)


def main():
    if NTFY_TOPIC == "CHANGE-ME":
        sys.exit("NTFY_TOPIC is not set. Add it as a repository secret.")
    if "--test" in sys.argv:
        ok = safe_notify("Test alert", "Halftime alerts are set up.")
        sys.exit(0 if ok else 1)
    max_hours = None
    if "--max-hours" in sys.argv:
        max_hours = float(sys.argv[sys.argv.index("--max-hours") + 1])
    try:
        run(max_hours)
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
