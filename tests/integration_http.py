"""HTTP integration tests. Run explicitly; requires local socket permission."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from http.server import ThreadingHTTPServer

import app
from market import MarketError
from store import RunStore


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.binding=patch.object(app,'store',RunStore(Path(self.directory.name)/'runs.sqlite3'))
        self.binding.start()

    def tearDown(self):
        self.binding.stop();self.directory.cleanup()

    def call(self,path,payload=None):
        req=Request(self.base+path) if payload is None else Request(self.base+path,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        with urlopen(req,timeout=30) as response:return json.load(response)

    def test_end_to_end_save_restore_export_replay(self):
        result=self.call('/api/run',{'mode':'demo','strategy':'ma_cross'})
        self.assertEqual(len(result['curve']),520)
        identifier=result['run_id']
        self.assertEqual(self.call('/api/runs')['runs'][0]['id'],identifier)
        restored=self.call('/api/runs/'+identifier)
        self.assertEqual(restored['result'],result)
        snapshot=self.call('/api/runs/'+identifier+'/snapshot')['snapshot']
        self.assertEqual(len(snapshot['data']),8)
        with patch.object(app.market,'history',side_effect=AssertionError('Replay must not fetch fresh data')):
            replay=self.call('/api/runs/'+identifier+'/replay',{})
        self.assertEqual(replay['curve'],result['curve'])
        self.assertEqual(replay['trades'],result['trades'])
        self.assertNotEqual(replay['run_id'],identifier)
        self.assertEqual(replay['provenance']['replay_of'],identifier)

    def test_comparison_and_assets(self):
        result=self.call('/api/compare',{'mode':'demo'})
        self.assertEqual(len(result['evaluations']),2)
        self.assertTrue(all(e['audit']['status']=='passed' for e in result['evaluations']))
        for path,kind in [('/static/app.js','javascript'),('/static/style.css','css')]:
            with urlopen(self.base+path) as response:
                self.assertIn(kind,response.headers['Content-Type']);self.assertGreater(len(response.read()),100)
        self.assertEqual(len(self.call('/api/strategies')['strategies']),2)

    def test_invalid_input_and_network_failures_do_not_save(self):
        for payload in ([],{'mode':'demo','strategy':'unknown'},{'mode':'demo','strategy_params':{'fast':60,'slow':20}}):
            with self.assertRaises(HTTPError) as caught:self.call('/api/run',payload)
            self.assertEqual(caught.exception.code,400)
        with patch.object(app.market,'history',side_effect=MarketError('network denied')):
            with self.assertRaises(HTTPError) as caught:self.call('/api/run',{'mode':'market','symbols':'600000'})
            self.assertEqual(caught.exception.code,502)
        self.assertEqual(self.call('/api/runs')['runs'],[])
        with self.assertRaises(HTTPError) as caught:self.call('/api/runs/'+'0'*32)
        self.assertEqual(caught.exception.code,404)


if __name__=='__main__':unittest.main()
