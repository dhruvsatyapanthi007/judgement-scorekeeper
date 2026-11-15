# ♠ Judgement & Kaadi Tiri Scorekeeper

A scorekeeper's dashboard for two card games popular in India (and Gujarat in particular):
**Judgement** (also called Kachuful / Oh Hell) and **Kaadi Tiri** (Kali Teeri, "black three").
The games are played with real cards at the table; this app is the score sheet. Pick the game when you start a new one.

## Judgement

- **Round setup** — cards go 1 → n → n → 1 (the peak round twice, optional). Trump rotates ♠ ♦ ♣ ♥, no no-trump round.
- **Who goes first** — each round shows the dealer, the first player to call (left of the dealer), and the calling order.
- **Hands asked** — enter each player's call. The dealer is warned and blocked from the one call that would make the total equal the cards dealt.
- **Hands won** — enter tricks after the round; the total must match the cards dealt.
- **Scoring** — hit your call exactly and you score your call with a 5 appended (0 → 5, 3 → 35, 10 → 105). A miss scores 0 (or, optionally, minus that amount).
- **Score sheet** — every round shows asked / won, the round score, and the running total beside it. Click a round to edit it.
- **Leaderboard and trend chart** — ranked totals with the last round's change, and a line chart of running totals per round.
## Kaadi Tiri

- **Each hand** — pick who bid, the points they asked (x), their partners, and (optionally) the trump suit. Then the app asks: did the bidder win?
- **Scoring** — bidder wins (*Jitya*): bidder **+2x**, each partner **+x**. Bidder loses (*Harya*): bidder **−x**, partners 0. Everyone else always gets 0.
- **Hands table** — every hand with bidder, bid, partners, trump, result, each player's points and running total. Click a hand to edit or delete it.

## Both games

- **Chart emojis** — pick an emoji for each player (or type your own). It marks their line on the score trend chart, and the leader gets a 👑. Change them mid-game from the buttons under the chart.
- **Gujarati touch** — suit names Kali ♠, Charkat ♦, Fallai ♣, Laal ♥; Raja / Rani / Gulam on the leaderboard; Gujarati labels and Gujarati-English cheers; a marigold toran and petal shower for the winner.
- **Saved in SQLite** — games persist in `~/Library/Application Support/Judgement/judgement.db`; quit any time and resume.

## Run it

**Desktop app (macOS)**

```bash
./build.sh                 # creates dist/Judgement.app (installs pywebview + PyInstaller into .venv)
open dist/Judgement.app
```

**In a browser** (needs only Python 3, no packages)

```bash
python3 server.py                  # http://127.0.0.1:8765
python3 server.py --host 0.0.0.0   # reachable from a phone/tablet on the same Wi-Fi
```

Both use the same database, so games show up in either.

## Files

| File | What it is |
| --- | --- |
| `server.py` | Small HTTP server + SQLite storage and the scoring / rule checks |
| `index.html` | The dashboard (vanilla JS) |
| `app.py` | Opens the dashboard in a native window via pywebview |
| `chart.umd.min.js` | Chart.js 4.4.1, bundled so the app works offline (MIT license) |
| `fonts/` | Rozha One, Mukta and Mukta Vaani (Gujarati), bundled for offline use (SIL Open Font License) |
| `build.sh`, `icon.icns` | Builds the macOS `.app` |
