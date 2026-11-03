"""Chronological holdout and prefix-invariance checks; no parameter optimization."""
from engine import run
from strategies import NAMES, get_strategy


def lookahead_audit(data, options, full=None):
    full = full or run(data, **options)
    dates = [p['date'] for p in full['curve']]
    checks = []
    for fraction in (.5, .75):
        count = int(len(dates) * fraction)
        cutoff = dates[count - 1]
        prefix = {s: [b for b in bars if b['date'] <= cutoff] for s, bars in data.items()}
        try:
            sliced = run(prefix, **options)
        except ValueError:
            continue
        original_trades = [t for t in full['trades'] if t['date'] <= cutoff]
        matching = sliced['curve'] == full['curve'][:count] and sliced['trades'] == original_trades
        checks.append(dict(cutoff=cutoff, matching=matching, trades=len(original_trades)))
    if any(not c['matching'] for c in checks):
        status = 'failed'
    elif checks and any(c['trades'] for c in checks):
        status = 'passed'
    else:
        status = 'inconclusive'
    return dict(status=status, checks=checks,
                scope='仅检测当前数据和截断点的权益/成交一致性，不证明复权、股票池或所有策略不存在未来信息')


def compare(data, options, strategy_keys=None):
    keys = list(NAMES) if strategy_keys is None else strategy_keys
    if not isinstance(keys, list) or not keys or len(keys) > 5 or any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
        raise ValueError('对比策略须为不重复的策略名称列表，最多 5 项')
    if len(data) > 100:
        raise ValueError('策略对比每次最多 100 只股票')
    dates = sorted({b['date'] for bars in data.values() for b in bars})
    if len(dates) < 160:
        raise ValueError('样本外对比至少需要 160 个交易日')
    split = int(len(dates) * .7)
    training_end, test_start = dates[split - 1], dates[split]
    training = {s: [b for b in bars if b['date'] <= training_end] for s, bars in data.items()}
    evaluations = []
    base = {k:v for k,v in options.items() if k not in ('strategy', 'start_date')}
    for key in keys:
        get_strategy(key, base.get('strategy_params'))
        settings = dict(base, strategy=key)
        train = run(training, **settings)
        test = run(data, **settings, start_date=test_start)
        full = run(data, **settings)
        audit = lookahead_audit(data, settings, full)
        evaluations.append(dict(strategy=test['strategy'], in_sample=train['summary'],
                                out_of_sample=test['summary'], full_sample=full['summary'],
                                audit=audit, test_period=test['period']))
    return dict(split=dict(train_start=dates[0], train_end=training_end, test_start=test_start,
                           test_end=dates[-1], train_sessions=split, test_sessions=len(dates)-split),
                evaluations=evaluations,
                method='固定前70%样本内、后30%样本外；不自动调参。样本外独立资金起步，之前数据仅用于指标预热，无继承订单或持仓。',
                warnings=['重复查看样本外后调参会污染样本外；这不是滚动交叉验证。',
                          '自动行情为前复权且股票池为当前选择，不是时点复权/历史成分数据库。',
                          '基准为所选股票池等权买入持有，不是沪深300等市场指数。'])
