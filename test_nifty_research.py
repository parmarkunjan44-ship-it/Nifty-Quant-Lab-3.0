"""Small deterministic smoke tests for the NIFTY spot research engine."""
import ast
from pathlib import Path
import unittest
import numpy as np
import pandas as pd
import plotly.graph_objects as go

SOURCE = Path(__file__).resolve().parents[1] / "app.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
WANTED = {"prepare_spot", "candle_parts", "build_signals", "run_backtest", "build_trade_chart"}
NODES = [n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
SETUP_NODE = next(n for n in TREE.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SETUP_NAMES" for t in n.targets))
NS = {"pd": pd, "np": np, "go": go}
exec(compile(ast.Module(body=[SETUP_NODE, *NODES], type_ignores=[]), str(SOURCE), "exec"), NS)


def sample_data():
    rows = []
    start = pd.Timestamp("2024-01-02 09:15")
    sessions = [
        [(100, 110, 95, 105), (105, 115, 100, 112)],
        [(112, 113, 108, 110), (110, 112, 109, 111), (111, 130, 110, 128),
         (128, 132, 126, 131), (131, 133, 129, 130)],
        [(115, 116, 112, 114), (114, 115, 110, 111), (111, 112, 108, 109)],
    ]
    for day, candles in enumerate(sessions):
        for i, (o, h, l, c) in enumerate(candles):
            rows.append({"datetime": start + pd.Timedelta(days=day, minutes=5*i),
                         "open": o, "high": h, "low": l, "close": c})
    return pd.DataFrame(rows)


class NiftyResearchSmokeTests(unittest.TestCase):
    def test_one_trade_per_day_is_hard_capped(self):
        trades, _, _ = NS["run_backtest"](sample_data(), max_trades_day=9,
            min_breakout_body=5, sl_buffer=1, rr=2)
        if not trades.empty:
            self.assertTrue((trades.groupby("trade_date").size() <= 1).all())

    def test_individual_setup_filter(self):
        trades, _, signals = NS["run_backtest"](sample_data(), min_breakout_body=5,
            sl_buffer=1, rr=2, selected_setups=["PDH momentum"])
        if not trades.empty:
            self.assertEqual(set(trades["setup"].unique()), {"PDH momentum"})
        self.assertTrue(set(signals.loc[signals.signal != "", "setup"].unique()).issubset({"PDH momentum"}))

    def test_chart_draws_actual_trade_zones(self):
        trades, _, _ = NS["run_backtest"](sample_data(), min_breakout_body=5,
            sl_buffer=1, rr=2, selected_setups=["PDH momentum"])
        fig = NS["build_trade_chart"](sample_data(), trades)
        self.assertEqual(len(fig.data), 1)
        if not trades.empty:
            # Two risk/reward rectangles and three price lines per trade.
            self.assertGreaterEqual(len(fig.layout.shapes), 5)


if __name__ == "__main__":
    unittest.main()
