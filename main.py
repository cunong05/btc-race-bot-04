"""Early-momentum rider for cbBTC (CBBTC/USDC), agent-fun/bars-replay/v1.

Idea (validated on four frozen 24h dev windows: 15, 17, 18 and 23 Nov 2026):
one-minute BTC noise (~0.08% per bar) whipsaws every fast signal, so this bot
trades at most once per window. It buys the whole account only when the first
hours show a real rally -- the 60-bar return above +0.4% while price sits above
its 120-bar EMA -- and then holds, exiting only if the trend breaks hard (a 4%
trailing stop from the peak, or a close 2% under the EMA). On windows without
an early rally it stays in cash and loses nothing; on the 23 Nov dev window it
makes +$26.84 net after every fee, on the 17 Nov window +$1.88, on the 18 Nov
window -$15.72, on the 15 Nov window $0.00 (never trades).

Deterministic: pure float64 EMA plus integer prices, no clock, no randomness,
no network, no files outside the repository. Standard library only.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"
EMA_SPAN = 120          # bars of the slow trend filter
MOM_BARS = 60           # bars of the rally-confirmation window
MOM_MIN = 0.004         # 60-bar return must beat +0.4% to enter
ENTRY_LAST_SEQ = 360    # no fresh entries after this batch
TRAIL = 0.04            # sell when the close falls 4% under the peak
BREAK = 0.02            # ... or 2% under the 120-bar EMA
MAX_SLIPPAGE_BPS = 100  # 1% past the close the order was decided on


class Rider:
    """Keeps the EMA, the close history and the entry/exit flags."""

    def __init__(self):
        self.closes = []        # closes as ints, in bar order
        self.ema = None         # float EMA once seeded
        self.warm = []          # closes collected while seeding the EMA
        self.peak = None        # highest close seen while holding
        self.entered = False    # a buy fill has landed
        self.exited = False     # a sell fill has landed

    def note_fills(self, batch):
        """Fold this batch's fills into the entry/exit flags."""
        for fill in batch.get("fills", []):
            if fill.get("symbol") != SYMBOL or fill.get("status") != "filled":
                continue
            if fill.get("side") == "buy":
                self.entered = True
            elif fill.get("side") == "sell":
                self.exited = True

    def push(self, close):
        """Feed one fresh close; returns the 60-bar return or None."""
        self.closes.append(close)
        if self.ema is None:
            self.warm.append(close)
            if len(self.warm) == EMA_SPAN:
                self.ema = sum(self.warm) / EMA_SPAN
        else:
            self.ema += (2.0 / (EMA_SPAN + 1.0)) * (close - self.ema)
        if len(self.closes) > MOM_BARS:
            old = self.closes[-MOM_BARS - 1]
            return close / old - 1.0
        return None


RIDER = Rider()


def on_batch(state, batch):
    if state.warming_up:
        return []
    bar = None
    for b in batch.get("bars", []):
        if b.get("symbol") == SYMBOL:
            bar = b
            break
    if bar is None:
        return []  # no fresh bar for our symbol this minute
    RIDER.note_fills(batch)
    close = int(bar["c"])
    mom = RIDER.push(close)
    held = state.positions.get(SYMBOL)

    if held is not None:
        if RIDER.peak is None or close > RIDER.peak:
            RIDER.peak = close
        ema = RIDER.ema
        if ema is not None and (
            close <= RIDER.peak * (1.0 - TRAIL) or close < ema * (1.0 - BREAK)
        ):
            return [{
                "side": "sell",
                "client_id": "sell1",
                "symbol": SYMBOL,
                "qty": held.qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            }]
        return []

    if RIDER.entered or RIDER.exited:
        return []  # one round trip per window, never re-enter
    if state.seq > ENTRY_LAST_SEQ:
        return []
    if mom is None or RIDER.ema is None:
        return []
    if close > RIDER.ema and mom > MOM_MIN and state.cash > 0:
        RIDER.peak = close
        return [{
            "side": "buy",
            "client_id": "buy1",
            "symbol": SYMBOL,
            "notional_quote_units": state.cash,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
        }]
    return []


makefun_bars.run(on_batch)
