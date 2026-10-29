# Game Theory Lab — Classroom Edition

A complete, small-class (2–20 participants) web app with instructor and student portals, seven experimental economics games, anonymous participation, real-time polling, SQLite persistence, and CSV export.

## Run locally

1. Install Python 3.10+.
2. In this folder, run `python -m pip install -r requirements.txt`.
3. Set an instructor password: `export GTL_ADMIN_PASSWORD='your-strong-password'` (macOS/Linux) or `$env:GTL_ADMIN_PASSWORD='your-strong-password'` (PowerShell).
4. Start: `python server.py`. If `GTL_ADMIN_PASSWORD` is not set, a random password is generated for that run and printed in the terminal.
5. Instructor opens `http://localhost:8000`, creates a session, and shares the PIN.
6. Students on the same Wi-Fi network visit `http://YOUR-LAPTOP-LAN-IP:8000` or use a cloud-deployed URL. Your firewall must allow incoming connections to port 8000.

For internet-based play, deploy behind HTTPS using a Python-capable host and persistent storage. The database path is configurable through `GTL_DB`. Always set `GTL_ADMIN_PASSWORD` for a deployment, and do not run a public instance without HTTPS; tokens and exported responses need protection.

## Games

1. **Prisoner's Dilemma:** Anonymous pair matching, CC=3/3, CD=0/5, DC=5/0, DD=1/1. Odd participant rests.
2. **Keynesian Beauty Contest:** Guess 0–100; nearest to 2/3 of the realized average wins 100 points (ties possible).
3. **Public Goods:** Groups of ~4, individual contributions, shared multiplied return.
4. **Ultimatum:** Proposer and responder sequential phases; acceptance implements 100-point split; rejection gives zero to both.
5. **Auction:** One item, secret valuations, first-price or second-price sealed bids; random tie-breaking.
6. **Market Trading:** Simultaneous buy bids/sell asks, sorted matching when bid >= ask; trade price midpoint of bid and ask. This is a simplified double-auction mechanism, not a continuous double auction.
7. **Cournot Competition:** Simultaneous quantities; P=max(0,100-Q), MC=10; profit=(P−MC)q.

## Instructor workflow

Create class -> students join -> choose game and time limit -> start round -> wait for responses -> advance / close round -> review and project the class summary -> launch next round -> export CSV.

**Time limit.** Each round can have a time limit per stage (30 s to 3 min, default 90 s, or none). Students and the instructor see a countdown. When it reaches zero the stage closes by itself: Ultimatum moves to the responder stage with a fresh countdown, and other games show results. Students who haven't submitted get no payoff for that round. The instructor can still close a stage early.

**Class summary.** When a round finishes, the instructor sees a summary with no names: headline numbers, a chart (guess or contribution distribution, offers accepted vs rejected, bids against valuations, supply and demand with trade prices, quantities against the Nash prediction), what theory predicts, and a round-by-round trend when the same game has been played more than once. **Project class results** opens it full screen for the classroom projector (Esc to close).

The app keeps a record of decisions and computed payoffs across rounds. Participant nicknames are stored only in the participants table and are **not included in CSV exports**. CSV contains pseudonymous participant IDs, plus each player's role, pair or group number and private value, so matching can be reconstructed. Resting players appear with an empty decision. Student screens never reveal partner names or other students' decisions. Instructor sees the private roster (label and name) and outcome rows, and can remove students who have left so rounds no longer wait for them.

## Important limitations / classroom safeguards

- This is a usable **prototype**, not a security-audited research platform.
- Only one instructor password is used (configured in the environment); session PIN is shareable.
- Students should retain the same browser tab (or sessionStorage in the same tab) to keep their anonymous identity. They can refresh (the PIN is kept in the page address); opening a different device will create a different identity.
- In odd-sized paired rounds, one random participant rests. Assignment is randomized each round, **not guaranteed balanced across rounds**.
- Students who join while a round is running wait and are included from the next round.
- Instructor can close a round early. Missing decisions produce no payoff; results reflect received submissions only.
- In Ultimatum, the instructor must wait for all proposers before moving to responder stage; otherwise unmatched responder offers may be absent.
- Public Goods forms groups of about four (3–5 depending on class size; one group of up to 5 for classes under 6). The multiplier is not capped, so keep it between 1 and the group size for a social dilemma; group returns are shared equally including non-submitters, so close only after all participants have acted for clean outcomes.
- Stored records are not automatically deleted. To reset data, stop server and remove `game_lab.db` (and any `-wal`/`-shm` files) or use a new `GTL_DB` file.
- The instructor password is sent in a request header (never in URLs) and kept only in the open page. There is no rate limiting on password or PIN guesses; use HTTPS, or run only on a trusted classroom LAN.
- No fixed-pair assignment, leaderboard, communication channel, or research-grade treatment randomization yet.

## Files

- `server.py`: Python FastAPI backend, matching engine and payoff rules
- `static/index.html`: responsive instructor/student UI
- `requirements.txt`: Python dependencies
- `tests/`: API tests; run `python -m pip install -r requirements-dev.txt` then `python -m pytest tests`
