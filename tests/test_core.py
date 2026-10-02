"""Hand-computed checks. Run: python -m unittest discover tests"""
import unittest
from datetime import datetime, timezone

from nightshift import risk
from nightshift.data import STEP_MS
from nightshift.features import Market
from nightshift.paper import FEE, SLIPPAGE, Book
from nightshift.sessions import closed_sessions, current_session, is_market_open


def utc(*a):
    return int(datetime(*a, tzinfo=timezone.utc).timestamp() * 1000)


class Sessions(unittest.TestCase):
    def test_open_hours_in_daylight_time(self):
        # Tue 2026-09-29: New York is UTC-4, so the market is open 13:30-20:00 UTC.
        self.assertFalse(is_market_open(utc(2026, 9, 29, 13, 29)))
        self.assertTrue(is_market_open(utc(2026, 9, 29, 13, 30)))
        self.assertTrue(is_market_open(utc(2026, 9, 29, 19, 59)))
        self.assertFalse(is_market_open(utc(2026, 9, 29, 20, 0)))

    def test_weekend_window(self):
        s = current_session(utc(2026, 10, 3, 12, 0))          # a Saturday
        self.assertEqual(s.kind, "weekend")
        self.assertEqual(s.close_ms, utc(2026, 10, 2, 20, 0))  # Fri 16:00 ET
        self.assertEqual(s.open_ms, utc(2026, 10, 5, 13, 30))  # Mon 09:30 ET
        self.assertEqual(s.hours, 65.5)

    def test_holiday_makes_long_window(self):
        # Labor Day, Mon 2026-09-07: Friday's close runs to Tuesday's open.
        s = current_session(utc(2026, 9, 7, 15, 0))
        self.assertEqual(s.id, "2026-09-08")
        self.assertEqual(s.hours, 89.5)

    def test_overnight_count(self):
        ss = closed_sessions(utc(2026, 9, 28, 0, 0), utc(2026, 10, 2, 23, 0))
        self.assertEqual([s.id for s in ss], ["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-02"])
        self.assertTrue(all(s.kind == "overnight" and s.hours == 17.5 for s in ss))


class NoLookAhead(unittest.TestCase):
    def test_price_at_never_uses_an_unfinished_candle(self):
        t0 = utc(2026, 9, 29, 0, 0)
        rows = [(t0 + i * STEP_MS, 0, 0, 0, 100.0 + i, 0) for i in range(8)]
        m = Market({"X": rows})
        # At t0+15m only candle 0 has closed; candle 1 (close 101) is still forming.
        self.assertEqual(m.price_at("X", t0 + STEP_MS), 100.0)
        self.assertEqual(m.price_at("X", t0 + 2 * STEP_MS - 1), 100.0)
        self.assertEqual(m.price_at("X", t0 + 2 * STEP_MS), 101.0)
        self.assertIsNone(m.price_at("X", t0))                        # nothing closed yet
        self.assertIsNone(m.price_at("X", t0 + 60 * STEP_MS))         # stale: last candle ended >12h ago


class Paper(unittest.TestCase):
    def test_round_trip_pnl(self):
        b = Book(cash=10_000.0)
        b.set_weight("A", 0.10, {"A": 100.0})            # buy 10 units at 100.05
        self.assertAlmostEqual(b.qty["A"], 10.0)
        b.flatten({"A": 110.0})                          # sell 10 units at 109.945
        buy, sell = 100.0 * (1 + SLIPPAGE), 110.0 * (1 - SLIPPAGE)
        expected = 10_000 + 10 * (sell - buy) - 10 * (buy + sell) * FEE
        self.assertAlmostEqual(b.cash, expected)
        self.assertAlmostEqual(b.cash, 10_096.85005, places=5)
        self.assertEqual(b.qty, {})

    def test_short_gains_when_price_falls(self):
        b = Book(cash=10_000.0)
        b.set_weight("A", -0.10, {"A": 100.0})
        self.assertAlmostEqual(b.pnl_pct("A", 95.0), 1 - 95.0 / (100.0 * (1 - SLIPPAGE)) , places=3)
        b.flatten({"A": 95.0})
        self.assertGreater(b.cash, 10_040)


class Risk(unittest.TestCase):
    def setUp(self):
        self.prices = {t: 100.0 for t in "ABCDE"}
        self.book = Book(cash=10_000.0)
        self.state = risk.SessionRisk(10_000.0)

    def test_size_is_clamped(self):
        ok, v = risk.apply({"A": 0.5}, self.book, self.prices, 5, self.state)
        self.assertEqual(ok, {"A": 0.20})
        self.assertEqual(v, [("A", "size_clamped")])

    def test_gross_is_scaled(self):
        ok, v = risk.apply({t: 0.20 for t in "ABCDE"}, self.book, self.prices, 5, self.state)
        self.assertAlmostEqual(sum(abs(w) for w in ok.values()), 0.60)
        self.assertEqual({r for _, r in v}, {"gross_scaled"})

    def test_no_entry_near_open_but_exit_allowed(self):
        self.book.set_weight("A", 0.10, self.prices)
        ok, v = risk.apply({"A": 0.0, "B": 0.10}, self.book, self.prices, 0.25, self.state)
        self.assertEqual(ok, {"A": 0.0})
        self.assertEqual(v, [("B", "too_close_to_open")])

    def test_unknown_name_rejected(self):
        ok, v = risk.apply({"ZZZ": 0.1}, self.book, self.prices, 5, self.state)
        self.assertEqual((ok, v), ({}, [("ZZZ", "unknown_or_unpriced")]))

    def test_position_stop_closes_and_blocks(self):
        self.book.set_weight("A", 0.20, self.prices)
        fired = risk.check_stops(self.book, {**self.prices, "A": 96.0}, self.state)
        self.assertEqual(fired, [("A", "position_stop")])
        self.assertNotIn("A", self.book.qty)
        ok, v = risk.apply({"A": 0.1}, self.book, self.prices, 5, self.state)
        self.assertEqual(v, [("A", "stopped_out_name")])

    def test_session_stop_flattens_and_halts(self):
        for t in "ABC":
            self.book.set_weight(t, 0.20, self.prices)
        # 60% gross falling 2% loses 1.2% of equity (plus entry costs): no stop yet.
        dip = {t: (98.0 if t in "ABC" else 100.0) for t in "ABCDE"}
        self.assertEqual(risk.check_stops(self.book, dip, self.state), [])
        # A 4% gap loses 2.4% of equity: the session stop fires before the per-name stops.
        deeper = {t: (96.0 if t in "ABC" else 100.0) for t in "ABCDE"}
        fired = risk.check_stops(self.book, deeper, self.state)
        self.assertEqual(fired, [("*", "session_stop")])
        self.assertTrue(self.state.halted)
        self.assertEqual(self.book.qty, {})


if __name__ == "__main__":
    unittest.main()
