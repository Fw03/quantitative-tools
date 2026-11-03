"""Strategy plugins receive a historical prefix, never the full future dataset."""
from dataclasses import dataclass
import math
from typing import Protocol


class Strategy(Protocol):
    key: str
    name: str
    warmup: int

    def evaluate(self, history: list[dict]) -> dict | None: ...


DEFAULTS = {'fast': 20, 'slow': 60, 'lookback': 20, 'min_momentum': .03}


def validate_params(params=None):
    if params is not None and not isinstance(params, dict):
        raise ValueError('策略参数必须是对象')
    params = params or {}
    if set(params) - set(DEFAULTS):
        raise ValueError('未知策略参数')
    result = dict(DEFAULTS, **params)
    for key in ('fast', 'slow', 'lookback'):
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 2 <= value <= 250:
            raise ValueError(f'{key} 须为 2–250 的整数')
    if result['fast'] >= result['slow']:
        raise ValueError('短均线周期必须小于长均线周期')
    momentum = result['min_momentum']
    if isinstance(momentum, bool) or not isinstance(momentum, (int, float)) or not math.isfinite(momentum) or not 0 <= momentum <= 1:
        raise ValueError('最小动量须在 0–1 之间')
    return result


@dataclass(frozen=True)
class DailyStrategy:
    key: str
    name: str
    params: dict

    @property
    def warmup(self):
        return max(self.params['slow'] + 1, self.params['lookback'] + 1, 15)

    def evaluate(self, history):
        if len(history) < self.warmup:
            return None
        p = self.params
        close = history[-1]['close']
        fast = sum(b['close'] for b in history[-p['fast']:]) / p['fast']
        slow = sum(b['close'] for b in history[-p['slow']:]) / p['slow']
        momentum = close / history[-p['lookback'] - 1]['close'] - 1
        atr = sum(max(history[k]['high'] - history[k]['low'],
                      abs(history[k]['high'] - history[k - 1]['close']),
                      abs(history[k]['low'] - history[k - 1]['close'])) for k in range(len(history) - 14, len(history))) / 14
        if self.key == 'trend_breakout':
            buy = (close > max(b['high'] for b in history[-p['lookback']-1:-1])
                   and close > fast > slow and momentum > p['min_momentum'])
            exit_signal = close < fast
            reason, exit_reason = '突破 + 均线趋势 + 动量', '跌破短期均线'
        else:
            previous_fast = sum(b['close'] for b in history[-p['fast']-1:-1]) / p['fast']
            previous_slow = sum(b['close'] for b in history[-p['slow']-1:-1]) / p['slow']
            buy = previous_fast <= previous_slow and fast > slow
            exit_signal = fast < slow
            reason, exit_reason = '短期均线上穿长期均线', '短期均线低于长期均线'
        return dict(buy=buy, exit=exit_signal, score=round(momentum * 100, 2), atr=atr,
                    close=round(close, 3), ma_fast=round(fast, 3), ma_slow=round(slow, 3),
                    reason=reason, exit_reason=exit_reason)


NAMES = {'trend_breakout': '趋势突破', 'ma_cross': '双均线交叉'}


def get_strategy(key='trend_breakout', params=None):
    if not isinstance(key, str) or key not in NAMES:
        raise ValueError('未知策略，请选择 trend_breakout 或 ma_cross')
    return DailyStrategy(key, NAMES[key], validate_params(params))


def catalog():
    return dict(strategies=[dict(key=k, name=v) for k, v in NAMES.items()], defaults=DEFAULTS,
                interface='evaluate(history_prefix) -> signal',
                execution='收盘生成信号，下一可交易日开盘模拟成交')
