import unittest
from engine import demo, indicator, load_csv, run

class EngineTests(unittest.TestCase):
    def test_demo_accounting(self):
        r=run(demo())
        self.assertGreater(len(r['trades']),0)
        self.assertEqual(len(r['curve']),520)
        cash=100000
        for t in r['trades']:
            self.assertEqual(t['qty']%100,0)
        self.assertAlmostEqual(r['summary']['equity'],r['summary']['cash']+sum(h['qty']*h['price'] for h in r['holdings']),places=1)
        self.assertGreaterEqual(r['summary']['cash'],0)
    def test_no_future_lookahead(self):
        d=demo(); prefix={s:bs[:300] for s,bs in d.items()}
        short=run(prefix); full=run(d)
        self.assertEqual(short['curve'],full['curve'][:300])
        self.assertEqual(short['trades'],[t for t in full['trades'] if t['date']<=short['curve'][-1]['date']])
    def test_next_open_fill(self):
        bars=[dict(date=f'2024-{1+i//28:02}-{1+i%28:02}',open=100.,high=101.,low=99.,close=100.,volume=10000) for i in range(63)]
        bars[60].update(close=110.,high=111.)
        bars[61].update(open=112.,close=113.,high=114.,low=111.)
        r=run({'TEST':bars},fee=0,slippage=0)
        buy=r['trades'][0]
        self.assertEqual(buy['date'],bars[61]['date']);self.assertEqual(buy['price'],112.)
    def test_bad_data(self):
        with self.assertRaises(ValueError):
            load_csv('symbol,date,open,high,low,close,volume\nX,2024-01-01,10,9,8,10,100\n')
        with self.assertRaises(ValueError):
            run(demo(),stop=float('nan'))
    def test_indicator_prefix(self):
        bars=demo()['DEMO01']
        self.assertEqual(indicator(bars,100),indicator(bars[:101],100))

if __name__=='__main__': unittest.main()
