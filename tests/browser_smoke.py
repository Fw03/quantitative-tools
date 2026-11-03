"""Optional real Chromium UI test; playwright is a development-only requirement."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from playwright.sync_api import sync_playwright, expect
import app
from market import MarketError
from store import RunStore


class BrowserTests(unittest.TestCase):
    def test_research_dashboard_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            try:
                with patch.object(app,'store',RunStore(Path(directory)/'experiments.sqlite3')), patch.object(app.market,'request',side_effect=MarketError('测试：离线行情')):
                    with sync_playwright() as playwright:
                        browser=playwright.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
                        page=browser.new_page(viewport={'width':1280,'height':900})
                        errors=[]
                        page.on('pageerror',lambda error:errors.append(str(error)))
                        page.goto('http://127.0.0.1:'+str(server.server_port),wait_until='networkidle')
                        expect(page.locator('#quoteStatus')).to_contain_text('报价获取失败')
                        expect(page.locator('#history tbody tr')).to_have_count(0)
                        page.select_option('#mode','demo')
                        page.click('#run')
                        expect(page.locator('#stats .card')).to_have_count(14)
                        expect(page.locator('#chart polyline')).to_have_count(2)
                        expect(page.locator('#source')).to_contain_text('合成演示数据')
                        expect(page.locator('#history tbody tr')).to_have_count(1)
                        expect(page.locator('#signals tbody tr')).to_have_count(8)
                        page.click('#compare')
                        expect(page.locator('#comparisons tbody tr')).to_have_count(2)
                        expect(page.locator('#auditInfo')).to_contain_text('一致')
                        expect(page.locator('#history tbody tr')).to_have_count(2)
                        with page.expect_download() as download_info:
                            page.locator('#history tbody tr').first.get_by_role('button',name='导出快照').click()
                        download=download_info.value
                        snapshot=json.loads(Path(download.path()).read_text())
                        self.assertEqual(len(snapshot['snapshot']['data']),8)
                        page.locator('#history tbody tr').first.get_by_role('button',name='复跑快照').click()
                        expect(page.locator('#history tbody tr')).to_have_count(3)
                        expect(page.locator('#comparisonReport')).to_be_visible()
                        page.locator('#history tbody tr').nth(2).get_by_role('button',name='查看',exact=True).click()
                        expect(page.locator('#backtestReport')).to_be_visible()
                        expect(page.locator('#comparisonReport')).to_be_hidden()
                        page.screenshot(path='/tmp/quant-workbench.png')
                        page.set_viewport_size({'width':390,'height':844})
                        self.assertFalse(page.evaluate('document.documentElement.scrollWidth > window.innerWidth'))
                        self.assertEqual(errors,[])
                        browser.close()
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
