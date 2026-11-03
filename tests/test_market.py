import json
import unittest
from datetime import date, timedelta
from unittest.mock import patch
from market import Market, MarketError, normalize_symbols

class MarketTests(unittest.TestCase):
    def test_a_share_symbols(self):
        self.assertEqual(normalize_symbols('600000，000001 sh600000 920002'),['sh600000','sz000001','bj920002'])
        for value in ('sh000001','510300','http://localhost','600000&x=1','',None):
            with self.assertRaises(ValueError): normalize_symbols(value)

    def test_quote_fields_and_missing(self):
        parts=['0']*33
        for i,value in {1:'浦发银行',3:'10.5',4:'10',6:'12345',30:'20261103145959'}.items():parts[i]=value
        market=Market()
        with patch.object(market,'request',return_value='v_sh600000="'+'~'.join(parts)+'";') as request:
            result=market.quotes('600000,000001')
            row=result['quotes'][0]
            self.assertEqual(row['name'],'浦发银行');self.assertEqual(row['change_pct'],5)
            self.assertTrue(row['quote_time'].endswith('+08:00'))
            self.assertEqual(result['missing'],['sz000001'])
            self.assertFalse(result['cached'])
            self.assertTrue(market.quotes('600000,000001')['cached'])
            self.assertEqual(request.call_count,1)

    def test_history_mapping_and_incomplete_day(self):
        market=Market()
        bars=[]
        today=date.today()
        for i in range(100):
            bars.append([(today-timedelta(days=99-i)).isoformat(),'10','11','12','9','50'])
        with patch.object(market,'request',return_value=json.dumps({'data':{'sh600000':{'qfqday':bars}}})):
            result=market.history('600000',100)
        parsed=result['data']['sh600000']
        self.assertLess(parsed[-1]['date'],today.isoformat())
        self.assertEqual(parsed[0]['high'],12)
        self.assertEqual(parsed[0]['close'],11)
        self.assertEqual(parsed[0]['volume'],5000)

    def test_no_unadjusted_fallback(self):
        market=Market()
        with patch.object(market,'request',return_value=json.dumps({'data':{'sh600000':{'day':[['2024-01-01','10','10','11','9','50']]}}})):
            with self.assertRaises(MarketError): market.history('600000')

    def test_paginated_stock_list(self):
        first=[{'f12':f'600{i:03}','f14':f'股票{i}'} for i in range(100)]
        second=[{'f12':'000001','f14':'平安银行'}]
        market=Market()
        responses=[json.dumps({'data':{'total':101,'diff':rows}}) for rows in (first,second)]
        with patch.object(market,'request',side_effect=responses) as request:
            result=market.stocks()
            self.assertEqual(len(result['stocks']),101)
            self.assertIn('pn=2',request.call_args.args[0])

    def test_failure_never_returns_demo_or_partial_history(self):
        market=Market()
        with patch.object(market,'request',side_effect=MarketError('network denied')):
            with self.assertRaises(MarketError):market.history('600000')
            self.assertEqual(market.cache,{})
        with patch.object(market,'request',return_value='v_pv_none_match="1";'):
            with self.assertRaises(MarketError):market.quotes('600000')

if __name__=='__main__':unittest.main()
