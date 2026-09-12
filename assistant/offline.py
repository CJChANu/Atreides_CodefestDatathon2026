"""Offline question answering (no API key needed): a transparent rule-based parser that turns common
plain-English questions into SQL for the warehouse. It is the fallback engine and the demo mode.

The parser extracts *slots* (metric, grouping, zones, borough, period, hours, day type, payment) and fills a
SQL template. When a slot is ambiguous it asks a clarifying question instead of guessing; when the question
is outside the data it says so.
"""
import calendar
import difflib
import re
from dataclasses import dataclass, field

DATA_START, DATA_END = "2025-04-01", "2026-04-01"       # [start, end)
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
BOROUGHS = ["manhattan", "brooklyn", "queens", "bronx", "staten island"]
ALIASES = {"jfk": [132], "kennedy": [132], "laguardia": [138], "la guardia": [138], "lga": [138], "newark": [1], "ewr": [1],
           "times square": [230], "times sq": [230], "penn station": [186], "grand central": [162], "wall street": [87],
           "central park": [43], "midtown": [161, 162, 163, 164], "upper east side": [236, 237], "upper west side": [238, 239],
           "airport": [1, 132, 138], "airports": [1, 132, 138]}
PAYMENTS = {"flex": 0, "flex fare": 0, "card": 1, "credit": 1, "credit card": 1, "cash": 2, "dispute": 4, "disputed": 4}
OUT_OF_SCOPE = {"weather": "weather", "rain": "weather", "driver name": "driver identities", "drivers name": "driver identities",
                "license plate": "vehicle identities", "medallion": "vehicle identities", "uber": "other companies' trips",
                "lyft": "other companies' trips", "stock": "financial markets", "password": "credentials"}
WRITE_WORDS = re.compile(r"\b(delete|drop|remove|update|insert|modify|truncate|alter|wipe|erase|overwrite)\b", re.I)

HELP = ("I answer questions about 12 months of taxi trips (Apr 2025 – Mar 2026). Try for example:\n"
        "• Which 5 zones had the most pickups in March 2026?\n"
        "• How many trips started at JFK in January 2026?\n"
        "• What is the average fare from LaGuardia to Times Square?\n"
        "• Show revenue by month for Flex Fare trips\n"
        "• What is the busiest hour on weekends in Brooklyn?\n"
        "• What share of trips are paid in cash?\n"
        "• How many refund reversals were recorded per month?")


@dataclass
class Result:
    text: str
    sql: str | None = None
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    kind: str = "answer"          # answer | clarify | refuse | help | error


class OfflineAssistant:
    def __init__(self, con):
        self.con = con
        self.zones = {r[0]: (r[1], r[2]) for r in con.execute("SELECT loc_id, zone, borough FROM zones").fetchall()}
        self.zone_names = {name.lower(): lid for lid, (name, _) in self.zones.items()}

    # ---------------------------------------------------------------- slot extraction
    def _zones_in(self, q):
        """Return list of (phrase, [loc_ids]) found in the question, longest phrases first."""
        found, taken = [], q
        cands = [(a, ids) for a, ids in ALIASES.items()] + [(n, [lid]) for n, lid in self.zone_names.items() if len(n) > 3]
        for phrase, ids in sorted(cands, key=lambda c: -len(c[0])):
            m = re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", taken)
            if m:
                found.append((phrase, ids, m.start()))
                taken = taken[:m.start()] + "#" * len(phrase) + taken[m.end():]
        return sorted(found, key=lambda f: f[2])

    def _fuzzy_zone(self, word):
        return difflib.get_close_matches(word, list(self.zone_names) + list(ALIASES), n=3, cutoff=0.75)

    @staticmethod
    def _period(q):
        """-> (start, end, label) or None; 'None, None, msg' for out-of-range."""
        if re.search(r"\blast month\b", q):
            return "2026-03-01", "2026-04-01", "March 2026 (latest month in the data)"
        if re.search(r"\b(this|last) year\b|\bpast year\b|\ball time\b|\boverall\b", q):
            return DATA_START, DATA_END, "Apr 2025 – Mar 2026"
        m = re.search(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?\s*(20\d\d)?", q)
        if m and not re.search(r"\bmay\b", m.group(0)) or (m and re.search(r"\bmay\s+20\d\d|\bin may\b", q)):
            mo = MONTHS[m.group(1)]
            yr = int(m.group(2)) if m.group(2) else (2026 if mo <= 3 else 2025)
            start = f"{yr}-{mo:02d}-01"
            if not (DATA_START <= start < DATA_END):
                return None, None, f"{calendar.month_name[mo]} {yr} is outside the data (Apr 2025 – Mar 2026)"
            end = f"{yr + (mo == 12)}-{mo % 12 + 1:02d}-01"
            return start, end, f"{calendar.month_name[mo]} {yr}"
        y = re.search(r"\b(20\d\d)\b", q)
        if y:
            yr = int(y.group(1))
            if yr == 2025:
                return "2025-04-01", "2026-01-01", "Apr–Dec 2025"
            if yr == 2026:
                return "2026-01-01", "2026-04-01", "Jan–Mar 2026"
            return None, None, f"{yr} is outside the data (Apr 2025 – Mar 2026)"
        return None

    @staticmethod
    def _hours(q):
        named = {"morning rush": (7, 9), "rush hour": (7, 9), "morning": (6, 9), "midday": (10, 15), "lunch": (11, 13),
                 "afternoon": (12, 16), "evening rush": (16, 19), "evening": (16, 19), "night": (20, 23), "late night": (0, 5), "overnight": (0, 5)}
        for k in sorted(named, key=len, reverse=True):
            if re.search(r"\b" + k + r"\b", q):
                return named[k], k
        m = re.search(r"\b(?:at|around)\s+(\d{1,2})\s*(am|pm)?\b", q)
        if m:
            h = int(m.group(1)) % 12 + (12 if m.group(2) == "pm" else 0) if m.group(2) else int(m.group(1))
            if 0 <= h <= 23:
                return (h, h), f"{h}:00–{h}:59"
        return None

    @staticmethod
    def _metric(q):
        table = [
            (r"\b(tip rate|tip percentage|tip %|tipping rate)\b", "tip_rate"),
            (r"\btips?\b|\bgratuit", "tips"),
            (r"\b(revenue|earn|income|money|sales|billed)\b", "revenue"),
            (r"\b(average|avg|mean|typical|median)\s+(fare|price|cost)|\bhow much (does|would|will) it cost|\bfare\b|\bprice\b|\bcost\b", "avg_fare"),
            (r"\b(how long|duration|travel time|trip time|minutes)\b", "duration"),
            (r"\b(distance|how far|miles)\b", "distance"),
            (r"\b(speed|mph|how fast)\b", "speed"),
            (r"\b(refund|reversal|chargeback|negative fare)", "reversals"),
            (r"\b(invalid|bad|dirty|anomal|data quality|errors?)\b", "quality"),
            (r"\b(share|percentage|percent|proportion|mix|breakdown)\b.*\b(cash|card|flex|payment)|\bpayment (mix|breakdown|types?)\b", "payment_mix"),
            (r"\b(trips?|rides?|pickups?|journeys|demand|busiest|how many|volume|popular)\b", "trips"),
        ]
        for pat, name in table:
            if re.search(pat, q):
                return name
        return None

    @staticmethod
    def _group(q):
        if re.search(r"\b(by|per|each|every)\s+(month|monthly)\b|\bmonthly\b|\bover time\b|\btrend\b", q): return "month"
        if re.search(r"\b(by|per|each)\s+hour\b|\bhourly\b|\bbusiest hour\b|\bwhat time\b|\bwhich hour\b|\bpeak hour", q): return "hour"
        if re.search(r"\b(by|per|each)\s+(day of (the )?week|weekday)\b|\bbusiest day\b|\bwhich day\b", q): return "dow"
        if re.search(r"\b(by|per|each)\s+borough\b|\bwhich borough\b", q): return "borough"
        if re.search(r"\b(by|per)\s+(payment|payment type)\b", q): return "payment"
        if re.search(r"\b(routes?|corridors?|pairs?)\b", q): return "route"
        if re.search(r"\b(top|which|busiest|most|highest|best|biggest|least|lowest|worst)\b.*\b(zones?|areas?|neighbou?rhoods?|places?|locations?|spots?|hotspots?)\b|\bhotspots?\b", q): return "zone"
        return None

    # ---------------------------------------------------------------- main entry
    def answer(self, question: str) -> Result:
        q = " " + re.sub(r"\s+", " ", question.lower()).strip() + " "
        if len(re.sub(r"[^a-z]", "", q)) < 3:
            return Result(HELP, kind="help")
        if WRITE_WORDS.search(q) and not re.search(r"\b(how many|were|was)\b", q):
            return Result("I can only read the data — I can't change or delete records. Ask me a question about trips instead.", kind="refuse")
        for k, what in OUT_OF_SCOPE.items():
            if k in q:
                return Result(f"The dataset has no information about {what}, so I can't answer that. " + HELP.split("\n")[0], kind="refuse")
        if re.search(r"\b(predict|forecast|next week|tomorrow|next month|2027)\b", q):
            return Result("I answer questions about recorded trips (Apr 2025 – Mar 2026). For forward-looking pickup volumes use the "
                          "72-hour demand forecaster (models/demand_model.pkl, notebook section 3.1).", kind="refuse")
        if re.search(r"^\s*(hi|hello|hey|help|what can you do)\b", q):
            return Result(HELP, kind="help")

        metric, group = self._metric(q), self._group(q)
        zones = self._zones_in(q)
        period = self._period(q)
        if period and period[0] is None:
            return Result(period[2] + ".", kind="refuse")
        hours = self._hours(q)
        weekend = 1 if re.search(r"\bweekends?\b|\bsaturday|\bsunday", q) else 0 if re.search(r"\bweekdays?\b", q) else None
        pay = next((v for k, v in sorted(PAYMENTS.items(), key=lambda kv: -len(kv[0])) if re.search(r"\b" + k + r"\b", q)), None)
        borough = next((b for b in BOROUGHS if b in q), None)
        m = re.search(r"\b(?:top|best|worst|bottom)\s+(\d{1,2})\b|\b(\d{1,2})\s+(?:busiest|top|most|best|worst|zones?|routes?|areas?|places?)\b", q)
        n = min(50, int(m.group(1) or m.group(2))) if m else 10

        # --- ambiguity: a zone phrase that maps to several zones ("midtown", "upper east side")
        multi = [(p, ids) for p, ids, _ in zones if len(ids) > 1 and p not in ("airport", "airports")]
        if multi and not re.search(r"\b(all|any|combined|together)\b", q):
            p, ids = multi[0]
            opts = ", ".join(self.zones[i][0] for i in ids)
            return Result(f"“{p.title()}” covers several taxi zones: {opts}. Which one do you mean? "
                          f"(Or add “combined” to include them all.)", kind="clarify")

        # --- unknown place name: offer close matches
        if not zones:
            cand = re.search(r"\b(?:from|to|at|in|near|around)\s+([a-z][a-z .'/-]{2,30}?)(?:\s+(?:to|in|on|at|during|between|by|for|from|and)\b|[?.!,]|\s*$)", q)
            if cand and cand.group(1).strip() not in BOROUGHS and not self._period(" " + cand.group(1) + " ") and not self._hours(" " + cand.group(1) + " ") \
                    and cand.group(1).strip() not in PAYMENTS and cand.group(1).strip() not in ("total", "weekends", "weekdays", "the city", "nyc", "new york", "all"):
                sug = self._fuzzy_zone(cand.group(1).strip())
                if sug:
                    return Result(f"I couldn't find a zone called “{cand.group(1).strip()}”. Did you mean: {', '.join(s.title() for s in sug)}?", kind="clarify")

        if metric is None and group is None:
            if zones or period or borough:
                return Result("What would you like to know — number of trips, revenue, average fare, tips or trip duration?", kind="clarify")
            return Result("Sorry, I didn't understand that. " + HELP, kind="help")
        metric = metric or "trips"

        o_ids = d_ids = None
        if zones:
            if re.search(r"\bfrom\b", q) and re.search(r"\bto\b", q) and len(zones) >= 2:
                o_ids, d_ids = zones[0][1], zones[1][1]
            elif re.search(r"\b(to|into|drop ?offs?|drop-offs?|dropped( off)?|arriv\w*|destination)\b", q) and not re.search(r"\bfrom\b", q):
                d_ids = zones[0][1]
            else:
                o_ids = zones[0][1]
        notes = []
        if re.search(r"\bbusiest\b", q) and not re.search(r"\b(pick ?ups?|drop ?offs?)\b", q) and group == "zone":
            notes.append("“busiest” measured by pickups")

        try:
            return self._build(metric, group, o_ids, d_ids, borough, period, hours, weekend, pay, n, q, notes)
        except Exception as e:  # never crash the chat
            return Result(f"Something went wrong while answering ({e}). Try rephrasing, e.g. " + HELP.split("\n")[1][2:], kind="error")

    # ---------------------------------------------------------------- SQL templates
    def _build(self, metric, group, o_ids, d_ids, borough, period, hours, weekend, pay, n, q, notes):
        from guard import run
        ids = lambda xs: "(" + ",".join(map(str, xs)) + ")"
        desc = []
        if period: desc.append(period[2])
        if hours: desc.append(f"{hours[1]}")
        if weekend is not None: desc.append("weekends" if weekend else "weekdays")
        if borough: desc.append(f"pickups in {borough.title()}")
        if pay is not None: desc.append(["Flex Fare", "card", "cash", "", "disputed"][pay] + " trips")
        name = lambda xs: " + ".join(self.zones[i][0] for i in xs)

        # ---- per-trip questions (fare/duration/distance between places) use the 5 % sample
        if metric in ("duration", "distance") or (metric == "avg_fare" and d_ids):
            w = ["1=1"]
            if o_ids: w.append(f"pickup_loc_id IN {ids(o_ids)}")
            if d_ids: w.append(f"dropoff_loc_id IN {ids(d_ids)}")
            if period: w.append(f"pickup_time >= '{period[0]}' AND pickup_time < '{period[1]}'")
            if hours: w.append(f"hour(pickup_time) BETWEEN {hours[0][0]} AND {hours[0][1]}")
            if weekend is not None: w.append(f"(dayofweek(pickup_time) IN (0,6)) = {bool(weekend)}")
            if pay is not None: w.append(f"payment_code = {pay}")
            col = {"duration": "duration_min", "distance": "miles", "avg_fare": "base_fare"}[metric]
            sql = (f"SELECT count(*) AS sampled_trips, round(median({col}), 2) AS median, round(avg({col}), 2) AS mean, "
                   f"round(quantile_cont({col}, 0.1), 2) AS p10, round(quantile_cont({col}, 0.9), 2) AS p90 "
                   f"FROM trips_sample WHERE {' AND '.join(w)} AND {col} IS NOT NULL")
            cols, rows, _ = run(self.con, sql)
            cnt, med, mean, p10, p90 = rows[0]
            if not cnt:
                return Result("No trips match that route and filter in the data.", sql, cols, rows)
            unit = {"duration": "min", "distance": "miles", "avg_fare": ""}[metric]
            fmt = (lambda v: f"${v:,.2f}") if metric == "avg_fare" else (lambda v: f"{v:,.1f} {unit}")
            route = (f"from {name(o_ids)} " if o_ids else "") + (f"to {name(d_ids)} " if d_ids else "")
            label = {"duration": "trip time", "distance": "distance", "avg_fare": "base fare (before surcharges, tolls and tip)"}[metric]
            text = (f"Typical {label} {route}{'(' + ', '.join(desc) + ') ' if desc else ''}is {fmt(med)} (median); "
                    f"80 % of trips fall between {fmt(p10)} and {fmt(p90)}. Based on {cnt:,} trips in the 5 % sample.")
            if cnt < 30: text += " ⚠ Small sample — treat as indicative."
            return Result(text, sql, cols, rows)

        # ---- data quality / refunds: ledger
        if metric in ("reversals", "quality"):
            w = [f"month >= '{period[0][:7]}' AND month < '{period[1][:7]}'"] if period else ["1=1"]
            if metric == "reversals":
                sql = (f"SELECT month, sum(n_rows) AS reversal_records, round(sum(charge_total), 2) AS amount_usd FROM ledger "
                       f"WHERE row_class = 'reversal' AND {w[0]} GROUP BY month ORDER BY month")
                cols, rows, _ = run(self.con, sql)
                tot = sum(r[1] for r in rows)
                return Result(f"{tot:,} refund/reversal records (negative fares) were recorded{' in ' + period[2] if period else ''}; "
                              f"they fell sharply from December 2025. Monthly breakdown below.", sql, cols, rows)
            sql = (f"SELECT row_class, sum(n_rows) AS records, round(100.0 * sum(n_rows) / sum(sum(n_rows)) OVER (), 2) AS pct "
                   f"FROM ledger WHERE {w[0]} GROUP BY row_class ORDER BY records DESC")
            cols, rows, _ = run(self.con, sql)
            bad = sum(r[1] for r in rows if r[0] != "valid"); tot = sum(r[1] for r in rows)
            return Result(f"{bad:,} of {tot:,} raw records ({100 * bad / tot:.1f} %) were not usable as valid trips"
                          f"{' in ' + period[2] if period else ''}. Breakdown by class below.", sql, cols, rows)

        # ---- aggregate questions on trip_stats (all valid trips)
        w = ["1=1"]
        if o_ids: w.append(f"t.pickup_loc_id IN {ids(o_ids)}")
        if d_ids and group != "route":
            # trip_stats has no drop-off zone -> use od_flows for arrivals
            return self._arrivals(d_ids, period, hours, weekend, desc, name)
        if borough: w.append(f"z.borough = '{borough.title()}'")
        if period: w.append(f"t.day >= '{period[0]}' AND t.day < '{period[1]}'")
        if hours: w.append(f"t.hour BETWEEN {hours[0][0]} AND {hours[0][1]}")
        if weekend is not None: w.append(f"(dayofweek(t.day) IN (0,6)) = {bool(weekend)}")
        if pay is not None and metric != "payment_mix": w.append(f"t.payment_code = {pay}")
        where = " AND ".join(w)
        value = {"trips": "sum(t.trips)", "revenue": "round(sum(t.revenue), 0)", "tips": "round(sum(t.tips), 0)",
                 "avg_fare": "round(sum(t.base_fare) / sum(t.trips), 2)", "tip_rate": "round(100 * sum(t.tips) / sum(t.base_fare), 2)",
                 "speed": "round(sum(t.miles) / sum(t.meter_minutes) * 60, 1)", "payment_mix": "sum(t.trips)"}[metric]
        vname = {"trips": "trips", "revenue": "revenue_usd", "tips": "card_tips_usd", "avg_fare": "avg_base_fare_usd",
                 "tip_rate": "tip_rate_pct", "speed": "avg_mph", "payment_mix": "trips"}[metric]
        base = f"FROM trip_stats t JOIN zones z ON t.pickup_loc_id = z.loc_id WHERE {where}"
        nice = {"trips": "trips", "revenue_usd": "revenue", "card_tips_usd": "card tips", "avg_base_fare_usd": "average base fare",
                "tip_rate_pct": "tip rate", "avg_mph": "average speed"}[vname]

        if metric == "payment_mix" or group == "payment":
            sql = (f"SELECT p.payment_type, sum(t.trips) AS trips, round(100.0 * sum(t.trips) / sum(sum(t.trips)) OVER (), 1) AS share_pct "
                   f"{base.replace('WHERE', 'JOIN payment_types p USING (payment_code) WHERE')} GROUP BY 1 ORDER BY trips DESC")
            cols, rows, _ = run(self.con, sql)
            desc = [d for d in desc if not d.endswith(" trips")]
            top = ", ".join(f"{r[0]} {r[2]} %" for r in rows[:3])
            lead = ""
            if pay is not None:
                asked = next((r for r in rows if r[0] == ["Flex Fare", "Credit card", "Cash", "", "Dispute"][pay]), None)
                if asked: lead = f"{asked[0]} accounts for {asked[2]} % of valid trips ({asked[1]:,}). "
            return Result(f"{lead}Payment mix{' (' + ', '.join(desc) + ')' if desc else ''}: {top}.", sql, cols, rows)

        if group == "route":
            band = self._band(hours)
            ww = [f"time_band = '{band}'"] if band else ["1=1"]
            if weekend is not None: ww.append(f"is_weekend = {bool(weekend)}")
            if o_ids: ww.append(f"origin_loc_id IN {ids(o_ids)}")
            if d_ids: ww.append(f"dest_loc_id IN {ids(d_ids)}")
            sql = (f"SELECT o.zone AS origin, d.zone AS destination, sum(f.trips) AS trips, round(sum(f.avg_base_fare * f.trips) / sum(f.trips), 2) AS avg_base_fare "
                   f"FROM od_flows f JOIN zones o ON f.origin_loc_id = o.loc_id JOIN zones d ON f.dest_loc_id = d.loc_id "
                   f"WHERE {' AND '.join(ww)} AND f.origin_loc_id <> f.dest_loc_id GROUP BY 1, 2 ORDER BY trips DESC LIMIT {n}")
            cols, rows, _ = run(self.con, sql)
            if period: notes.append("route counts cover the full year (flows are not stored by month)")
            return Result(f"Top {len(rows)} routes{' (' + band.replace('_', ' ') + ')' if band else ''}: " +
                          "; ".join(f"{r[0]} → {r[1]} ({r[2]:,})" for r in rows[:3]) + ". Full list below." + self._notes(notes), sql, cols, rows)

        if group == "zone":
            order = "ASC" if re.search(r"\b(least|lowest|worst|quietest)\b", q) else "DESC"
            min_trips = "HAVING sum(t.trips) >= 1000" if metric in ("avg_fare", "tip_rate", "speed") else ""
            sql = (f"SELECT z.zone, z.borough, {value} AS {vname} {base} GROUP BY 1, 2 {min_trips} ORDER BY {vname} {order} LIMIT {n}")
            cols, rows, _ = run(self.con, sql)
            if metric in ("avg_fare", "tip_rate", "speed"): notes.append("zones with at least 1,000 trips")
            return Result(f"{'Top' if order == 'DESC' else 'Bottom'} {len(rows)} pickup zones by {nice}"
                          f"{' (' + ', '.join(desc) + ')' if desc else ''}: " + ", ".join(f"{r[0]} ({self._fmt(r[2], metric)})" for r in rows[:3])
                          + ". Full list below." + self._notes(notes), sql, cols, rows)

        if group in ("month", "hour", "dow", "borough"):
            if group == "borough":
                base += " AND z.borough NOT IN ('Unknown', 'N/A')"
            key = {"month": "strftime(t.day, '%Y-%m') AS month", "hour": "t.hour", "dow": "dayname(t.day) AS weekday", "borough": "z.borough"}[group]
            order = {"month": "1", "hour": "1", "dow": "min(isodow(t.day))", "borough": f"{vname} DESC"}[group]
            sql = f"SELECT {key}, {value} AS {vname} {base} GROUP BY 1 ORDER BY {order}"
            cols, rows, _ = run(self.con, sql)
            if not rows:
                return Result("No trips match those filters.", sql, cols, rows)
            best = max(rows, key=lambda r: r[1] or 0)
            where_txt = (" from " + name(o_ids)) if o_ids else ""
            return Result(f"{nice.capitalize()} by {group if group != 'dow' else 'day of week'}{where_txt}"
                          f"{' (' + ', '.join(desc) + ')' if desc else ''}. Highest: {best[0]}{':00' if group == 'hour' else ''} "
                          f"({self._fmt(best[1], metric)})." + self._notes(notes), sql, cols, rows)

        sql = f"SELECT {value} AS {vname}, sum(t.trips) AS trips {base}"
        cols, rows, _ = run(self.con, sql)
        v, trips = rows[0]
        if not trips:
            return Result("No trips match those filters in the data.", sql, cols, rows)
        subject = (f"from {name(o_ids)}" if o_ids else "") + (f" ({', '.join(desc)})" if desc else " (Apr 2025 – Mar 2026)")
        label = {"trips": "Valid trips", "revenue": "Revenue billed", "tips": "Card tips", "avg_fare": "Average base fare",
                 "tip_rate": "Tip rate (card tips ÷ base fare)", "speed": "Average meter speed"}[metric]
        extra = f" over {trips:,} trips" if metric not in ("trips",) else ""
        return Result(f"{label} {subject.strip()}: {self._fmt(v, metric)}{extra}." + self._notes(notes), sql, cols, rows)

    def _arrivals(self, d_ids, period, hours, weekend, desc, name):
        from guard import run
        band = self._band(hours)
        ww = [f"dest_loc_id IN ({','.join(map(str, d_ids))})"]
        if band: ww.append(f"time_band = '{band}'")
        if weekend is not None: ww.append(f"is_weekend = {bool(weekend)}")
        sql = f"SELECT sum(trips) AS arriving_trips FROM od_flows WHERE {' AND '.join(ww)}"
        cols, rows, _ = run(self.con, sql)
        note = " (drop-off counts cover the full year)" if period else ""
        return Result(f"{rows[0][0]:,} trips were dropped off at {name(d_ids)}{' during ' + band.replace('_', ' ') if band else ''}{note}.", sql, cols, rows)

    @staticmethod
    def _band(hours):
        if not hours: return None
        h = hours[0][0]
        return "late_night" if h < 6 else "morning_peak" if h < 10 else "midday" if h < 16 else "evening_peak" if h < 20 else "night"

    @staticmethod
    def _fmt(v, metric):
        if v is None: return "n/a"
        return {"revenue": f"${v:,.0f}", "tips": f"${v:,.0f}", "avg_fare": f"${v:,.2f}", "tip_rate": f"{v:.1f} %", "speed": f"{v:.1f} mph"}.get(metric, f"{int(v):,}")

    @staticmethod
    def _notes(notes):
        return (" Note: " + "; ".join(notes) + ".") if notes else ""
