"""Portfolio performance, using 252 sessions/year and zero risk-free rate."""
import math
import statistics


def performance(curve, trades, initial):
    values = [initial] + [p['equity'] for p in curve]
    daily = [b / a - 1 for a, b in zip(values, values[1:])]
    deviation = statistics.stdev(daily) if len(daily) > 1 else 0
    annual = (values[-1] / initial) ** (252 / len(daily)) - 1
    pnl = [t['pnl'] for t in trades if t['side'] == 'SELL']
    gain, loss = sum(max(0, v) for v in pnl), -sum(min(0, v) for v in pnl)
    drawdown = 0
    peak = initial
    for value in values:
        peak = max(peak, value)
        drawdown = min(drawdown, value / peak - 1)
    average_equity = statistics.mean(values[1:])
    return dict(annual_return_pct=round(annual * 100, 2),
                annual_volatility_pct=round(deviation * math.sqrt(252) * 100, 2),
                sharpe=round(statistics.mean(daily) / deviation * math.sqrt(252), 3) if deviation > 1e-12 else None,
                calmar=round(annual / abs(drawdown), 3) if drawdown < -1e-12 else None,
                profit_factor=round(gain / loss, 3) if loss > 0 else None,
                commission=round(sum(t.get('commission', 0) for t in trades), 2),
                slippage_cost=round(sum(t.get('slippage_cost', 0) for t in trades), 2),
                turnover=round(sum(t['price'] * t['qty'] for t in trades) / average_equity, 3),
                average_exposure_pct=round(statistics.mean(p.get('exposure_pct', 0) for p in curve), 2),
                sessions=len(daily))


def benchmark(data, dates, initial):
    """Equal initial cash weights; buy at each symbol's first available open.

    Fractional shares, no friction, no rebalancing. Missing/newly listed symbols
    keep their cash until their first available bar. This is NOT a market index.
    """
    weight = initial / len(data)
    remaining = initial
    qty, marks = {}, {}
    lookup = {s: {b['date']: b for b in bars} for s, bars in data.items()}
    curve = []
    for day in dates:
        for symbol, bars in lookup.items():
            bar = bars.get(day)
            if bar is None:
                continue
            if symbol not in qty and bar['volume'] > 0:
                qty[symbol] = weight / bar['open']
                remaining -= weight
            marks[symbol] = bar['close']
        value = remaining + sum(qty[s] * marks[s] for s in qty)
        curve.append(dict(date=day, equity=round(value, 2)))
    return dict(name='所选股票池等权买入持有（非指数、无交易成本）', curve=curve,
                return_pct=round((curve[-1]['equity'] / initial - 1) * 100, 2))
