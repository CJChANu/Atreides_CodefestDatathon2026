"""Terminal chat for the mobility assistant.

  python assistant/cli.py                  interactive chat (Claude if ANTHROPIC_API_KEY is set, otherwise offline engine)
  python assistant/cli.py --offline        force the offline engine
  python assistant/cli.py --ask "..."      answer one question and exit
Commands inside the chat:  :sql (toggle showing SQL)   :reset (forget the conversation)   :quit
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent import MobilityAssistant  # noqa: E402


def cell(v):
    return f"{v:,}" if isinstance(v, int) else f"{v:,.2f}" if isinstance(v, float) else str(v)


def show(r, with_sql):
    print("\n" + r.text)
    if r.rows and len(r.rows) > 1:
        widths = [max(len(str(c)), *(len(cell(row[i])) for row in r.rows[:15])) for i, c in enumerate(r.columns)]
        fmt = lambda row: "  ".join(cell(v).ljust(w) for v, w in zip(row, widths))
        print("\n  " + fmt(r.columns) + "\n  " + "  ".join("-" * w for w in widths))
        for row in r.rows[:15]:
            print("  " + fmt(row))
        if len(r.rows) > 15:
            print(f"  ... {len(r.rows) - 15} more rows")
    if with_sql and r.sql:
        print("\n  SQL: " + " ".join(r.sql.split()))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--ask")
    ap.add_argument("--sql", action="store_true", help="show the SQL behind each answer")
    a = ap.parse_args()
    bot = MobilityAssistant("offline" if a.offline else "auto")
    if a.ask:
        show(bot.ask(a.ask), a.sql)
        return
    print(f"Urban Flow Mobility Assistant ({'Claude ' + 'claude-opus-5' if bot.mode == 'claude' else 'offline engine'}). "
          "Ask about trips, fares, zones, tips, demand. :sql :reset :quit")
    with_sql = a.sql
    while True:
        try:
            q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if q in (":q", ":quit", "exit", "quit"):
            break
        if q == ":sql":
            with_sql = not with_sql
            print(f"(SQL display {'on' if with_sql else 'off'})")
            continue
        if q == ":reset":
            bot.reset()
            print("(conversation cleared)")
            continue
        show(bot.ask(q), with_sql)


if __name__ == "__main__":
    main()
