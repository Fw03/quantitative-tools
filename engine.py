"""Daily long-only research engine. Signals at close, fills at next open."""
import csv
import io
import math
import random
from collections import defaultdict
from datetime import date, timedelta


def load_csv(text):
    grouped = defaultdict(list)
    seen = set()
    for row in csv.DictReader(io.StringIO(text.lstrip('\ufeff'))):
        symbol = row['symbol'].strip()
        day = date.fromisoformat(row['date']).isoformat()
        key = (symbol, day)
        if not symbol or key in seen:
            raise ValueError('股票代码为空或存在重复日期')
        seen.add(key)
        bar = {'date': day}
        for field in ('open', 'high', 'low', 'close', 'volume'):
            value = float(row[field])
            if not math.isfinite(value) or value < 0 or (field != 'volume' and value == 0):
                raise ValueError('价格必须为正数，成交量不可为负数')
            bar[field] = value
        if bar['low'] > min(bar['open'], bar['close']) or bar['high'] < max(bar['open'], bar['close']) or bar['low'] > bar['high']:
            raise ValueError('OHLC 价格关系不合法')
        grouped[symbol].append(bar)
    if not grouped or len(grouped) > 500:
        raise ValueError('请上传 1–500 只股票的数据')
    for bars in grouped.values():
        bars.sort(key=lambda b: b['date'])
    return dict(grouped)


def demo():
    rng = random.Random(42)
    result = {}
    for n in range(8):
        bars, price = [], 12 + n * 8
        day = date(2024, 1, 1)
        while len(bars) < 520:
            if day.weekday() < 5:
                opening = price * math.exp(rng.gauss(0, .005))
                price = opening * math.exp(.0003 + .002 * math.sin(len(bars) / 35 + n) + rng.gauss(0, .018))
                bars.append(dict(date=day.isoformat(), open=opening, close=price,
                                 high=max(opening, price) * 1.008, low=min(opening, price) * .992,
                                 volume=rng.randint(1000000, 5000000)))
            day += timedelta(days=1)
        result[f'DEMO{n+1:02}'] = bars
    return result


def indicator(bars, i):
    if i < 60:
        return None
    closes = [b['close'] for b in bars[i-60:i+1]]
    close = closes[-1]
    ma20, ma60 = sum(closes[-20:])/20, sum(closes[-60:])/60
    momentum = close / closes[-21] - 1
    prior_high = max(b['high'] for b in bars[i-20:i])
    atr = sum(max(bars[k]['high']-bars[k]['low'], abs(bars[k]['high']-bars[k-1]['close']), abs(bars[k]['low']-bars[k-1]['close'])) for k in range(i-13, i+1))/14
    buy = close > prior_high and close > ma20 > ma60 and momentum > .03
    return dict(buy=buy, exit=close < ma20, score=round(momentum*100, 2), atr=atr,
                close=round(close, 3), ma20=round(ma20, 3), ma60=round(ma60, 3))


def run(data, capital=100000, fee=.0003, slippage=.001, stop=.08, max_positions=5):
    if not 1000 <= capital <= 1e9 or not 0 <= fee <= .01 or not 0 <= slippage <= .05 or not .01 <= stop <= .3:
        raise ValueError('参数超出有效范围')
    if not 1 <= max_positions <= 20:
        raise ValueError('持仓数量须为 1–20')
    lookup = {s: {b['date']: i for i,b in enumerate(bs)} for s,bs in data.items()}
    dates = sorted({b['date'] for bs in data.values() for b in bs})
    if not any(len(bs) >= 62 for bs in data.values()):
        raise ValueError('至少一只股票需要 62 根日线')
    cash, holdings, pending, trades, curve = capital, {}, {}, [], []
    last_prices, peak, halted = {}, capital, False
    for day in dates:
        today = {s: (lookup[s][day], bs[lookup[s][day]]) for s,bs in data.items() if day in lookup[s]}
        # Missing sessions retain pending orders; each symbol fills only on its next available bar.
        for s in sorted(list(pending), key=lambda x: pending[x]['side'] == 'BUY'):
            if s not in today:
                continue
            order = pending.pop(s)
            _, bar = today[s]
            if bar['volume'] <= 0:
                pending[s] = order
                continue
            if order['side'] == 'SELL' and s in holdings:
                h = holdings.pop(s)
                price = bar['open'] * (1-slippage)
                proceeds = price*h['qty']*(1-fee-.0005)
                cash += proceeds
                trades.append(dict(date=day, symbol=s, side='SELL', price=round(price,3), qty=h['qty'], reason=order['reason'], pnl=round(proceeds-h['cost'],2)))
            elif order['side'] == 'BUY' and s not in holdings and len(holdings) < max_positions and not halted:
                equity = cash + sum(h['qty']*last_prices.get(k,h['entry']) for k,h in holdings.items())
                price = bar['open']*(1+slippage)
                # At most 20% capital per position and 1% equity risk at initial stop.
                budget = min(cash, equity*min(.2,1/max_positions), equity*.01/stop)
                qty = int(budget/(price*(1+fee))/100)*100
                if qty:
                    cost = qty*price*(1+fee)
                    cash -= cost
                    holdings[s] = dict(qty=qty, entry=price, cost=cost, high=price)
                    trades.append(dict(date=day, symbol=s, side='BUY', price=round(price,3), qty=qty, reason='20日突破 + 均线趋势 + 动量', pnl=None))
        for s,(i,bar) in today.items():
            last_prices[s] = bar['close']
            if s in holdings:
                holdings[s]['high'] = max(holdings[s]['high'],bar['close'])
        equity = cash + sum(h['qty']*last_prices[s] for s,h in holdings.items())
        peak = max(peak,equity)
        dd = equity/peak-1
        if dd <= -.15:
            halted = True
        curve.append(dict(date=day, equity=round(equity,2), drawdown=round(dd*100,2)))
        candidates = []
        for s,(i,bar) in today.items():
            sig = indicator(data[s],i)
            if s in holdings:
                h = holdings[s]
                reason = '组合回撤熔断' if halted else '收盘止损/移动止盈' if bar['close'] <= max(h['entry']*(1-stop),h['high']*(1-stop)) else '跌破20日均线' if sig and sig['exit'] else None
                if reason:
                    pending[s] = dict(side='SELL',reason=reason)
            elif sig and sig['buy'] and not halted:
                candidates.append((sig['score'],s))
        slots = max_positions-len(holdings)-sum(o['side']=='BUY' for o in pending.values())
        for _,s in sorted(candidates,reverse=True)[:max(0,slots)]:
            pending[s] = dict(side='BUY',reason='趋势突破')
    monthly = {}
    previous = capital
    for point in curve:
        monthly[point['date'][:7]] = point['equity']
    returns = []
    for month, value in monthly.items():
        returns.append(dict(month=month, return_pct=round((value/previous-1)*100,2)))
        previous=value
    sells = [t for t in trades if t['side']=='SELL']
    signals=[]
    for s,bs in data.items():
        sig=indicator(bs,len(bs)-1)
        if sig:
            signals.append(dict(symbol=s,date=bs[-1]['date'], action=pending.get(s,{}).get('side','HOLD' if s in holdings else 'WATCH'), **sig))
    return dict(summary=dict(initial=capital,equity=round(equity,2),cash=round(cash,2),return_pct=round((equity/capital-1)*100,2),max_drawdown=round(min(p['drawdown'] for p in curve),2),closed_trades=len(sells),win_rate=round(sum(t['pnl']>0 for t in sells)/len(sells)*100,1) if sells else None,halted=halted),curve=curve,monthly=returns,trades=trades,holdings=[dict(symbol=s,**h,price=last_prices[s]) for s,h in holdings.items()],signals=sorted(signals,key=lambda s:s['score'],reverse=True),pending=pending)
