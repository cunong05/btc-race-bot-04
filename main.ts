// Early-momentum rider for cbBTC (CBBTC/USDC), agent-fun/bars-replay/v1.
//
// Idea (validated on four frozen 24h dev windows: 15, 17, 18 and 23 Nov 2026):
// one-minute BTC noise (~0.08% per bar) whipsaws every fast signal, so this bot
// trades at most once per window. It buys the whole account only when the first
// hours show a real rally -- the 60-bar return above +0.4% while price sits above
// its 120-bar EMA -- and then holds, exiting only if the trend breaks hard (a 4%
// trailing stop from the peak, or a close 2% under the EMA). On windows without
// an early rally it stays in cash and loses nothing; on the 23 Nov dev window it
// makes +$26.84 net after every fee, on the 17 Nov window +$1.88, on the 18 Nov
// window -$15.72, on the 15 Nov window $0.00 (never trades). Verified with the
// kit's own grader (`grade-local --driver process`): net PnL +$26.837273 on the
// 23 Nov dev window, 1 trade, max drawdown 1.98%, both runs byte-identical.
//
// Deterministic: pure float64 EMA plus integer prices, no clock, no randomness,
// no network, no files outside the repository. TypeScript, erasable syntax only.

import { runBars, type BarsState } from './vendor/makefun-bars.ts';

const SYMBOL = 'CBBTC';
const EMA_SPAN = 120; // bars of the slow trend filter
const MOM_BARS = 60; // bars of the rally-confirmation window
const MOM_MIN = 0.004; // 60-bar return must beat +0.4% to enter
const ENTRY_LAST_SEQ = 360; // no fresh entries after this batch
const TRAIL = 0.04; // sell when the close falls 4% under the peak
const BREAK = 0.02; // ... or 2% under the 120-bar EMA
const MAX_SLIPPAGE_BPS = 100; // 1% past the close the order was decided on

// EMA seeded as the mean of its first span of closes, then 2 / (span + 1) steps,
// the same seeding the kit's baselines use.
class Trend {
  span: number;
  warm: number[] = [];
  value: number | null = null;
  constructor(span: number) {
    this.span = span;
  }
  push(close: number): void {
    if (this.value !== null) {
      this.value += (2 / (this.span + 1)) * (close - this.value);
      return;
    }
    this.warm.push(close);
    if (this.warm.length === this.span) {
      this.value = this.warm.reduce((a, x) => a + x, 0) / this.span;
    }
  }
}

const trend = new Trend(EMA_SPAN);
const closes: number[] = [];
let peak: number | null = null;
let entered = false; // a buy fill has landed
let exited = false; // a sell fill has landed

await runBars({
  onBatch(state: BarsState, batch) {
    if (state.warmingUp) return [];
    let bar = null;
    for (const b of batch.bars) {
      if (b.symbol === SYMBOL) {
        bar = b;
        break;
      }
    }
    if (bar === null) return []; // no fresh bar for our symbol this minute
    for (const fill of batch.fills) {
      if (fill.symbol !== SYMBOL || fill.status !== 'filled') continue;
      if (fill.side === 'buy') entered = true;
      if (fill.side === 'sell') exited = true;
    }
    const close = Number(bar.c);
    closes.push(close);
    trend.push(close);
    const held = state.positions.get(SYMBOL);

    if (held !== undefined) {
      if (peak === null || close > peak) peak = close;
      const ema = trend.value;
      if (
        ema !== null &&
        (close <= peak * (1 - TRAIL) || close < ema * (1 - BREAK))
      ) {
        return [
          {
            side: 'sell',
            client_id: 'sell1',
            symbol: SYMBOL,
            qty: held.qty,
            max_slippage_bps: MAX_SLIPPAGE_BPS,
          },
        ];
      }
      return [];
    }

    if (entered || exited) return []; // one round trip per window, never re-enter
    if (state.seq > ENTRY_LAST_SEQ) return [];
    if (closes.length <= MOM_BARS || trend.value === null) return [];
    const mom = close / closes[closes.length - MOM_BARS - 1] - 1;
    if (close > trend.value && mom > MOM_MIN && state.cash > 0n) {
      peak = close;
      return [
        {
          side: 'buy',
          client_id: 'buy1',
          symbol: SYMBOL,
          notional_quote_units: state.cash,
          max_slippage_bps: MAX_SLIPPAGE_BPS,
        },
      ];
    }
    return [];
  },
});
