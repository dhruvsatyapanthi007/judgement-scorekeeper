#!/usr/bin/env python3
"""Judgement & Kaadi Tiri scorekeeper: a tiny local server backed by SQLite.

Run:  python3 server.py            -> http://127.0.0.1:8765
      python3 server.py --host 0.0.0.0   (open it from a tablet/phone on the same Wi-Fi)

No third-party packages needed. Scores live in
~/Library/Application Support/Judgement/judgement.db (shared with the desktop app).
"""
import argparse
import json
import os
import re
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# When packaged by PyInstaller, bundled files live under sys._MEIPASS
ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
DATA_DIR = Path(os.environ.get("JUDGEMENT_DATA_DIR") or Path.home() / "Library" / "Application Support" / "Judgement")
DB_PATH = DATA_DIR / "judgement.db"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/chart.umd.min.js": ("chart.umd.min.js", "application/javascript"),
}
TRUMPS = ["spades", "diamonds", "clubs", "hearts"]  # no no-trump round
RANKS = ["A", "K", "Q", "J", "10", "9", "8", "7", "6", "5", "4", "3", "2"]
DEFAULT_EMOJIS = ["🦁", "🐯", "🐼", "🦊", "🐸", "🐵", "🦄", "🐙", "🐧", "🐨"]

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS games (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  max_cards INTEGER NOT NULL,
  repeat_peak INTEGER NOT NULL DEFAULT 1,
  miss_rule TEXT NOT NULL DEFAULT 'zero',
  kind TEXT NOT NULL DEFAULT 'judgement'      -- 'judgement' or 'kaadi_tiri'
);
CREATE TABLE IF NOT EXISTS players (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  seat INTEGER NOT NULL,
  name TEXT NOT NULL,
  emoji TEXT
);
CREATE TABLE IF NOT EXISTS rounds (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  round_no INTEGER NOT NULL,
  cards INTEGER NOT NULL,
  trump TEXT NOT NULL,
  dealer_seat INTEGER NOT NULL,
  first_seat INTEGER NOT NULL,
  UNIQUE (game_id, round_no)
);
CREATE TABLE IF NOT EXISTS entries (
  round_id INTEGER NOT NULL REFERENCES rounds(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  bid INTEGER,
  tricks INTEGER,
  score INTEGER,
  PRIMARY KEY (round_id, player_id)
);
-- Kaadi Tiri: one row per hand played
CREATE TABLE IF NOT EXISTS kt_rounds (
  id INTEGER PRIMARY KEY,
  game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
  bidder_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  bid INTEGER NOT NULL,
  won INTEGER NOT NULL,
  trump TEXT,
  called TEXT NOT NULL DEFAULT '[]',  -- partner cards called: [{"rank","suit","nth"}], nth 1/2 for two decks
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS kt_partners (
  round_id INTEGER NOT NULL REFERENCES kt_rounds(id) ON DELETE CASCADE,
  player_id INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
  PRIMARY KEY (round_id, player_id)
);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def schedule(max_cards, repeat_peak):
    up = list(range(1, max_cards + 1))
    down = list(range(max_cards if repeat_peak else max_cards - 1, 0, -1))
    return up + down


def score_for(bid, tricks, miss_rule):
    """Hit = bid with a 5 appended (0 -> 05, 3 -> 35, 12 -> 125). Miss = 0 or negative."""
    if bid is None or tricks is None:
        return None
    hit = bid * 10 + 5
    if bid == tricks:
        return hit
    return -hit if miss_rule == "negative" else 0


def kt_scores(player_ids, bidder_id, partner_ids, bid, won):
    """Kaadi Tiri: bidder wins -> bidder +2x, each partner +x. Bidder loses -> bidder -x. Everyone else 0."""
    scores = {pid: 0 for pid in player_ids}
    if won:
        scores[bidder_id] = 2 * bid
        for pid in partner_ids:
            scores[pid] = bid
    else:
        scores[bidder_id] = -bid
    return scores


def kt_rounds(conn, gid, player_ids):
    out = []
    for i, r in enumerate(conn.execute("SELECT * FROM kt_rounds WHERE game_id=? ORDER BY id", (gid,)).fetchall()):
        partners = [x[0] for x in conn.execute("SELECT player_id FROM kt_partners WHERE round_id=?", (r["id"],))]
        sc = kt_scores(player_ids, r["bidder_id"], partners, r["bid"], r["won"])
        out.append({"id": r["id"], "round_no": i + 1, "bidder_id": r["bidder_id"], "bid": r["bid"],
                    "won": bool(r["won"]), "trump": r["trump"], "partners": partners,
                    "called": json.loads(r["called"] or "[]"),
                    "scores": {str(k): v for k, v in sc.items()}})
    return out


def save_kt_round(conn, gid, body, rid=None):
    g = conn.execute("SELECT * FROM games WHERE id=? AND kind='kaadi_tiri'", (gid,)).fetchone()
    if not g:
        raise ApiError("Game not found", 404)
    players = {r[0] for r in conn.execute("SELECT id FROM players WHERE game_id=?", (gid,))}
    bidder = int(body["bidder_id"])
    bid = int(body["bid"])
    partners = sorted({int(x) for x in body.get("partner_ids", [])})
    won = body.get("won")
    trump = body.get("trump") or None
    if bidder not in players:
        raise ApiError("Pick who made the bid")
    if not 1 <= bid <= 10000:
        raise ApiError("Enter the points asked")
    if any(p not in players for p in partners) or bidder in partners:
        raise ApiError("Partners must be other players in this game")
    if won not in (True, False):
        raise ApiError("Say whether the bidder won")
    if trump is not None and trump not in TRUMPS:
        raise ApiError("Unknown trump suit")
    max_partners = -(-len(players) // 2)  # ceil(n/2)
    called = []
    for c in body.get("called") or []:
        card = {"rank": str(c.get("rank")), "suit": c.get("suit"), "nth": int(c.get("nth") or 1)}
        if card["rank"] not in RANKS or card["suit"] not in TRUMPS or card["nth"] not in (1, 2):
            raise ApiError("Unknown called card")
        if any(card == {k: d[k] for k in ("rank", "suit", "nth")} for d in called):
            raise ApiError("That card is already called")
        holder = c.get("player_id")
        if holder is not None:
            holder = int(holder)
            if holder not in players:
                raise ApiError("The card's holder must be a player in this game")
        card["player_id"] = holder
        called.append(card)
    # Partners are whoever turned up holding a called card (the bidder holding one doesn't count)
    partners = sorted(set(partners) | {c["player_id"] for c in called if c["player_id"] is not None} - {bidder})
    if len(partners) > max_partners:
        raise ApiError(f"At most {max_partners} partners with {len(players)} players")
    if len(called) > max_partners:
        raise ApiError(f"At most {max_partners} cards can be called with {len(players)} players")
    called_json = json.dumps(called)
    if rid is None:
        rid = conn.execute("INSERT INTO kt_rounds(game_id, bidder_id, bid, won, trump, called) VALUES (?,?,?,?,?,?)",
                           (gid, bidder, bid, int(won), trump, called_json)).lastrowid
    else:
        cur = conn.execute("UPDATE kt_rounds SET bidder_id=?, bid=?, won=?, trump=?, called=? WHERE id=? AND game_id=?",
                           (bidder, bid, int(won), trump, called_json, rid, gid))
        if not cur.rowcount:
            raise ApiError("Round not found", 404)
        conn.execute("DELETE FROM kt_partners WHERE round_id=?", (rid,))
    for pid in partners:
        conn.execute("INSERT INTO kt_partners(round_id, player_id) VALUES (?,?)", (rid, pid))
    conn.commit()
    return load_game(conn, gid)


def clean_emoji(v):
    v = str(v or "").strip()
    return v[:8] if v else None


def set_emoji(conn, gid, pid, body):
    emoji = clean_emoji(body.get("emoji"))
    if not emoji:
        raise ApiError("Pick an emoji")
    cur = conn.execute("UPDATE players SET emoji=? WHERE id=? AND game_id=?", (emoji, pid, gid))
    if not cur.rowcount:
        raise ApiError("Player not found", 404)
    conn.commit()
    return load_game(conn, gid)


class ApiError(Exception):
    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.status = status


def load_game(conn, gid):
    g = conn.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    if not g:
        raise ApiError("Game not found", 404)
    players = [dict(r) for r in conn.execute(
        "SELECT id, seat, name, emoji FROM players WHERE game_id=? ORDER BY seat", (gid,))]
    if g["kind"] == "kaadi_tiri":
        return {"game": dict(g), "players": players,
                "rounds": kt_rounds(conn, gid, [p["id"] for p in players])}
    rounds = []
    for r in conn.execute("SELECT * FROM rounds WHERE game_id=? ORDER BY round_no", (gid,)):
        rd = dict(r)
        rd["entries"] = {
            str(e["player_id"]): {"bid": e["bid"], "tricks": e["tricks"], "score": e["score"]}
            for e in conn.execute("SELECT * FROM entries WHERE round_id=?", (r["id"],))
        }
        rounds.append(rd)
    return {"game": dict(g), "players": players, "rounds": rounds}


def list_games(conn):
    out = []
    for g in conn.execute("SELECT * FROM games ORDER BY id DESC").fetchall():
        if g["kind"] == "kaadi_tiri":
            plist = [dict(p) for p in conn.execute(
                "SELECT id, name, emoji, seat FROM players WHERE game_id=? ORDER BY seat", (g["id"],))]
            rounds = kt_rounds(conn, g["id"], [p["id"] for p in plist])
            for p in plist:
                p["total"] = sum(r["scores"][str(p["id"])] for r in rounds)
            plist.sort(key=lambda p: (-p["total"], p["seat"]))
            out.append({**dict(g), "players": [{"name": p["name"], "emoji": p["emoji"], "total": p["total"]} for p in plist],
                        "rounds_total": None, "rounds_done": len(rounds)})
            continue
        players = conn.execute(
            """SELECT p.name, p.emoji, COALESCE(SUM(e.score),0) AS total FROM players p
               LEFT JOIN entries e ON e.player_id = p.id
               WHERE p.game_id=? GROUP BY p.id ORDER BY total DESC, p.seat""", (g["id"],)).fetchall()
        total_rounds = conn.execute("SELECT COUNT(*) FROM rounds WHERE game_id=?", (g["id"],)).fetchone()[0]
        done = conn.execute(
            """SELECT COUNT(*) FROM rounds r WHERE r.game_id=? AND NOT EXISTS (
                 SELECT 1 FROM players p LEFT JOIN entries e ON e.player_id=p.id AND e.round_id=r.id
                 WHERE p.game_id=r.game_id AND e.tricks IS NULL)""", (g["id"],)).fetchone()[0]
        out.append({**dict(g), "players": [dict(p) for p in players],
                    "rounds_total": total_rounds, "rounds_done": done})
    return out


def create_game(conn, body):
    raw = [p if isinstance(p, dict) else {"name": p} for p in body.get("players", [])]
    raw = [p for p in raw if str(p.get("name") or "").strip()]
    names = [str(p["name"]).strip() for p in raw]
    emojis = [clean_emoji(p.get("emoji")) or DEFAULT_EMOJIS[i % len(DEFAULT_EMOJIS)] for i, p in enumerate(raw)]
    kind = "kaadi_tiri" if body.get("kind") == "kaadi_tiri" else "judgement"
    if not (3 if kind == "kaadi_tiri" else 2) <= len(names) <= 10:
        raise ApiError(f"Add between {3 if kind == 'kaadi_tiri' else 2} and 10 players")
    if len(set(n.lower() for n in names)) != len(names):
        raise ApiError("Player names must be unique")
    if kind == "kaadi_tiri":
        name = str(body.get("name") or "").strip() or "Kaadi Tiri night"
        gid = conn.execute("INSERT INTO games(name, max_cards, kind) VALUES (?,0,'kaadi_tiri')", (name,)).lastrowid
        for seat, pname in enumerate(names):
            conn.execute("INSERT INTO players(game_id, seat, name, emoji) VALUES (?,?,?,?)",
                         (gid, seat, pname, emojis[seat]))
        conn.commit()
        return load_game(conn, gid)
    n = len(names)
    limit = 52 // n
    max_cards = int(body.get("max_cards") or limit)
    if not 1 <= max_cards <= limit:
        raise ApiError(f"Max cards must be between 1 and {limit} for {n} players")
    repeat_peak = 1 if body.get("repeat_peak", True) else 0
    miss_rule = "negative" if body.get("miss_rule") == "negative" else "zero"
    first_dealer = int(body.get("first_dealer", 0)) % n
    name = str(body.get("name") or "").strip() or "Judgement night"

    cur = conn.execute("INSERT INTO games(name, max_cards, repeat_peak, miss_rule) VALUES (?,?,?,?)",
                       (name, max_cards, repeat_peak, miss_rule))
    gid = cur.lastrowid
    for seat, pname in enumerate(names):
        conn.execute("INSERT INTO players(game_id, seat, name, emoji) VALUES (?,?,?,?)",
                     (gid, seat, pname, emojis[seat]))
    for i, cards in enumerate(schedule(max_cards, repeat_peak)):
        dealer = (first_dealer + i) % n
        conn.execute(
            "INSERT INTO rounds(game_id, round_no, cards, trump, dealer_seat, first_seat) VALUES (?,?,?,?,?,?)",
            (gid, i + 1, cards, TRUMPS[i % 4], dealer, (dealer + 1) % n))
    conn.commit()
    return load_game(conn, gid)


def save_round(conn, gid, round_no, body):
    g = conn.execute("SELECT * FROM games WHERE id=?", (gid,)).fetchone()
    if not g:
        raise ApiError("Game not found", 404)
    r = conn.execute("SELECT * FROM rounds WHERE game_id=? AND round_no=?", (gid, round_no)).fetchone()
    if not r:
        raise ApiError("Round not found", 404)
    players = {p["id"]: p for p in conn.execute("SELECT * FROM players WHERE game_id=?", (gid,))}
    cards = r["cards"]

    def clean(v):
        if v is None or v == "":
            return None
        v = int(v)
        if not 0 <= v <= cards:
            raise ApiError(f"Values must be between 0 and {cards}")
        return v

    rows = {}
    for e in body.get("entries", []):
        pid = int(e["player_id"])
        if pid not in players:
            raise ApiError("Unknown player")
        rows[pid] = (clean(e.get("bid")), clean(e.get("tricks")))

    bids = [rows.get(pid, (None, None))[0] for pid in players]
    tricks = [rows.get(pid, (None, None))[1] for pid in players]
    if all(b is not None for b in bids) and sum(bids) == cards:
        raise ApiError(f"Total hands asked can't equal {cards}: the dealer must change their call")
    if any(t is not None for t in tricks):
        if any(t is None for t in tricks):
            raise ApiError("Enter hands won for every player")
        if any(b is None for b in bids):
            raise ApiError("Enter hands asked for every player first")
        if sum(tricks) != cards:
            raise ApiError(f"Hands won add up to {sum(tricks)}, but {cards} were played")

    for pid, (bid, tr) in rows.items():
        conn.execute(
            """INSERT INTO entries(round_id, player_id, bid, tricks, score) VALUES (?,?,?,?,?)
               ON CONFLICT(round_id, player_id) DO UPDATE SET bid=excluded.bid, tricks=excluded.tricks, score=excluded.score""",
            (r["id"], pid, bid, tr, score_for(bid, tr, g["miss_rule"])))
    conn.commit()
    return load_game(conn, gid)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def dispatch(self, method):
        path = self.path.split("?")[0]
        m = re.fullmatch(r"/fonts/([A-Za-z0-9_-]+\.(woff2|css))", path)
        if method == "GET" and m and (ROOT / "fonts" / m[1]).is_file():
            body = (ROOT / "fonts" / m[1]).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "font/woff2" if m[2] == "woff2" else "text/css")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if method == "GET" and path in STATIC:
            fname, ctype = STATIC[path]
            body = (ROOT / fname).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            with db() as conn:
                if path == "/api/games" and method == "GET":
                    return self.send_json(list_games(conn))
                if path == "/api/games" and method == "POST":
                    return self.send_json(create_game(conn, self.read_body()), 201)
                m = re.fullmatch(r"/api/games/(\d+)", path)
                if m and method == "GET":
                    return self.send_json(load_game(conn, int(m[1])))
                if m and method == "DELETE":
                    conn.execute("DELETE FROM games WHERE id=?", (int(m[1]),))
                    conn.commit()
                    return self.send_json({"ok": True})
                m = re.fullmatch(r"/api/games/(\d+)/players/(\d+)", path)
                if m and method == "PUT":
                    return self.send_json(set_emoji(conn, int(m[1]), int(m[2]), self.read_body()))
                m = re.fullmatch(r"/api/games/(\d+)/kt-rounds", path)
                if m and method == "POST":
                    return self.send_json(save_kt_round(conn, int(m[1]), self.read_body()), 201)
                m = re.fullmatch(r"/api/games/(\d+)/kt-rounds/(\d+)", path)
                if m and method == "PUT":
                    return self.send_json(save_kt_round(conn, int(m[1]), self.read_body(), int(m[2])))
                if m and method == "DELETE":
                    conn.execute("DELETE FROM kt_rounds WHERE id=? AND game_id=?", (int(m[2]), int(m[1])))
                    conn.commit()
                    return self.send_json(load_game(conn, int(m[1])))
                m = re.fullmatch(r"/api/games/(\d+)/rounds/(\d+)", path)
                if m and method == "PUT":
                    return self.send_json(save_round(conn, int(m[1]), int(m[2]), self.read_body()))
            self.send_json({"error": "Not found"}, 404)
        except ApiError as e:
            self.send_json({"error": str(e)}, e.status)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            self.send_json({"error": "Invalid input"}, 400)

    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def do_PUT(self):
        self.dispatch("PUT")

    def do_DELETE(self):
        self.dispatch("DELETE")


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.executescript(SCHEMA)
        # Older databases: add the game type column
        if "kind" not in [c[1] for c in conn.execute("PRAGMA table_info(games)")]:
            conn.execute("ALTER TABLE games ADD COLUMN kind TEXT NOT NULL DEFAULT 'judgement'")
        # Older databases: add the called-cards column for Kaadi Tiri hands
        if "called" not in [c[1] for c in conn.execute("PRAGMA table_info(kt_rounds)")]:
            conn.execute("ALTER TABLE kt_rounds ADD COLUMN called TEXT NOT NULL DEFAULT '[]'")
        # Older databases: add the emoji column and give existing players one
        if "emoji" not in [c[1] for c in conn.execute("PRAGMA table_info(players)")]:
            conn.execute("ALTER TABLE players ADD COLUMN emoji TEXT")
        for pid, seat in conn.execute("SELECT id, seat FROM players WHERE emoji IS NULL").fetchall():
            conn.execute("UPDATE players SET emoji=? WHERE id=?", (DEFAULT_EMOJIS[seat % len(DEFAULT_EMOJIS)], pid))
        conn.commit()


def start_in_background(host="127.0.0.1", port=0):
    """Start the server on a thread; port 0 picks a free port. Returns the URL."""
    init_db()
    httpd = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://{host}:{httpd.server_address[1]}/"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    init_db()
    print(f"Judgement scorekeeper running at http://{args.host}:{args.port}  (data: {DB_PATH})", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
