import copy
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from analytics import benchmark, performance
from engine import demo, run
from research import compare, lookahead_audit
from store import RunStore
from strategies import get_strategy


class ResearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = demo()

    def test_strategy_config_and_cross_signal(self):
        for params in ({'fast':60,'slow':20}, {'fast':True}, {'slow':251}, {'min_momentum':float('nan')}, {'future':1}):
            with self.assertRaises(ValueError): get_strategy(params=params)
        with self.assertRaises(ValueError): get_strategy('not-a-strategy')
        model=get_strategy('ma_cross',dict(fast=2,slow=3,lookback=2))
        prices=[100]*15+[90,130]
        bars=[dict(open=p,close=p,high=p+1,low=p-1,volume=1000) for p in prices]
        self.assertFalse(model.evaluate(bars[:-1])['buy'])
        self.assertTrue(model.evaluate(bars)['buy'])

    def test_both_strategies_are_prefix_invariant(self):
        for key in ('trend_breakout','ma_cross'):
            full=run(self.data,strategy=key)
            prefix={s:bars[:300] for s,bars in self.data.items()}
            sliced=run(prefix,strategy=key)
            self.assertEqual(sliced['curve'],full['curve'][:300])
            self.assertEqual(sliced['trades'],[t for t in full['trades'] if t['date']<=sliced['period']['end']])
            self.assertEqual(lookahead_audit(self.data,dict(strategy=key),full)['status'],'passed')

    def test_holdout_starts_with_fresh_account_and_warmup(self):
        result=compare(self.data,{})
        cutoff=result['split']['test_start']
        fresh=run(self.data,start_date=cutoff)
        self.assertEqual(fresh['curve'][0]['equity'],100000)
        self.assertFalse(any(t['date']<=cutoff for t in fresh['trades']))
        self.assertEqual(fresh['summary'],result['evaluations'][0]['out_of_sample'])
        self.assertEqual(fresh['period']['start'],cutoff)
        self.assertEqual(fresh['strategy']['warmup'],61)
        self.assertEqual(result['split']['train_sessions']+result['split']['test_sessions'],520)

    def test_audit_detects_a_changed_prefix(self):
        full=run(self.data)
        def corrupted(data,**options):
            result=run(data,**options)
            result['curve'][0]['equity']+=1
            return result
        with patch('research.run',side_effect=corrupted):
            self.assertEqual(lookahead_audit(self.data,{},full)['status'],'failed')

    def test_zero_trade_audit_is_inconclusive(self):
        flat=copy.deepcopy({'TEST':self.data['DEMO01'][:180]})
        for bar in flat['TEST']:
            bar.update(open=100,close=100,high=101,low=99)
        self.assertEqual(lookahead_audit(flat,{})['status'],'inconclusive')
        with self.assertRaises(ValueError):compare({'TEST':flat['TEST'][:100]}, {})
        with self.assertRaises(ValueError):compare(self.data,{},['ma_cross','ma_cross'])

    def test_analytics_known_returns_and_undefined_ratios(self):
        curve=[dict(equity=v,exposure_pct=50) for v in (100,110,99)]
        metrics=performance(curve,[],100)
        self.assertEqual(metrics['sharpe'],0)
        self.assertAlmostEqual(metrics['annual_volatility_pct'],round(.1*math.sqrt(252)*100,2))
        self.assertEqual(metrics['annual_return_pct'],round((.99**84-1)*100,2))
        self.assertIsNone(metrics['profit_factor'])
        flat=performance([dict(equity=100),dict(equity=100)],[],100)
        self.assertIsNone(flat['sharpe']);self.assertIsNone(flat['calmar'])

    def test_benchmark_keeps_cash_until_first_known_bar(self):
        data={'A':[dict(date='2025-01-01',open=10,close=10,volume=100),dict(date='2025-01-02',open=10,close=12,volume=100)],
              'B':[dict(date='2025-01-02',open=100,close=200,volume=100)]}
        result=benchmark(data,['2025-01-01','2025-01-02'],100)
        self.assertEqual([p['equity'] for p in result['curve']],[100,160])
        self.assertEqual(result['return_pct'],60)

    def test_costs_are_recorded_on_actual_trades(self):
        result=run(self.data)
        self.assertGreater(result['summary']['commission'],0)
        self.assertGreater(result['summary']['slippage_cost'],0)
        self.assertTrue(all(t['commission']>0 for t in result['trades']))
        free=run(self.data,fee=0,slippage=0)
        self.assertEqual(free['summary']['slippage_cost'],0)
        self.assertTrue(all(t['commission']==0 for t in free['trades'] if t['side']=='BUY'))
        # Sell-side stamp tax still applies when broker commission is zero.
        self.assertTrue(all(t['commission']>0 for t in free['trades'] if t['side']=='SELL'))

    def test_circuit_breaker_cancels_suspended_pending_buys(self):
        data={symbol:[dict(date=bar['date'],open=100,close=100,high=101,low=99,volume=10000)
                      for bar in self.data['DEMO01'][:63]] for symbol in 'ABCDEF'}
        for bars in data.values():
            bars[60].update(close=110,high=111)
            bars[61].update(open=112,close=113,high=114,low=111)
            bars[62].update(open=112,close=40,high=113,low=39)
        data['F'][61]['volume']=data['F'][62]['volume']=0
        result=run(data,stop=.01)
        self.assertTrue(result['summary']['halted'])
        self.assertFalse(any(t['symbol']=='F' for t in result['trades']))
        self.assertNotIn('F',result['pending'])
        self.assertTrue(all(order['side']=='SELL' for order in result['pending'].values()))


class StoreTests(unittest.TestCase):
    def test_snapshot_survives_reopen_and_is_replayable(self):
        data=demo();options=dict(strategy='ma_cross',strategy_params=dict(fast=10,slow=40))
        original=run(data,**options)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'runs.sqlite3'
            result=RunStore(path).save('backtest',original,data,options,'test')
            reopened=RunStore(path)
            record=reopened.get(result['run_id'],True)
            replay=run(record['snapshot']['data'],**record['snapshot']['options'])
            self.assertEqual(replay,original)
            self.assertEqual(record['result']['provenance']['data_sha256'],record['snapshot']['data_sha256'])
            self.assertEqual(len(record['snapshot']['code_sha256']),64)
            self.assertIsNone(reopened.get("' OR 1=1"))
            self.assertEqual(len(reopened.recent()),1)

    def test_retention_keeps_latest_fifty(self):
        with tempfile.TemporaryDirectory() as directory:
            store=RunStore(Path(directory)/'runs.sqlite3')
            first=None
            for i in range(51):
                record=store.save('backtest',{'index':i},{'TEST':[]},{},'test')
                first=first or record['run_id']
            self.assertEqual(len(store.recent()),50)
            self.assertIsNone(store.get(first))
            self.assertEqual(store.recent()[0]['id'],record['run_id'])


if __name__=='__main__':unittest.main()
