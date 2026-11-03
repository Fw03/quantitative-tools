"""Public A-share adapters. No credentials; TLS/proxy settings remain inherited."""
import csv
import io
import json
import math
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from engine import load_csv

SHANGHAI = ZoneInfo('Asia/Shanghai')

class MarketError(Exception):
    pass


def now():
    return datetime.now(SHANGHAI).isoformat(timespec='seconds')


def normalize_symbols(value):
    tokens = re.split(r'[,，;；\s]+', value.strip()) if isinstance(value, str) else value
    if not isinstance(tokens, list):
        raise ValueError('股票代码须为文本或列表')
    result=[]
    for token in tokens:
        if not isinstance(token,str):
            raise ValueError('股票代码须为字符串')
        token=token.strip().lower()
        if not token:
            continue
        match=re.fullmatch(r'(?:(sh|sz|bj))?(\d{6})',token)
        if not match:
            raise ValueError('请输入六位 A 股代码，如 600000、000001、920002')
        prefix, code=match.groups()
        market='sh' if code.startswith(('600','601','603','605','688','689')) else 'sz' if code.startswith(('000','001','002','003','300','301')) else 'bj' if code.startswith(('43','83','87','88','92')) else None
        if not market or prefix and prefix!=market:
            raise ValueError(f'非支持的 A 股代码：{token}')
        symbol=market+code
        if symbol not in result:
            result.append(symbol)
    if not 1 <= len(result) <= 30:
        raise ValueError('一次请输入 1–30 只 A 股代码')
    return result


class Market:
    def __init__(self):
        self.cache={}
        self.lock=threading.Lock()

    def cached(self,key,ttl,loader):
        # Only cache successes. No stale data fallback on network errors.
        with self.lock:
            entry=self.cache.get(key)
            if entry and time.monotonic()-entry[0]<ttl:
                return dict(entry[1],cached=True)
        result=loader()
        result.update(fetched_at=now())
        with self.lock:
            self.cache[key]=(time.monotonic(),result)
        return dict(result,cached=False)

    def request(self,url,encoding='utf-8'):
        request=Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'*/*'})
        for attempt in range(2):
            try:
                with urlopen(request,timeout=12) as response:
                    raw=response.read(8_000_001)
                    if len(raw)>8_000_000:
                        raise MarketError('行情响应超过大小限制')
                    return raw.decode(encoding)
            except (URLError,TimeoutError,OSError) as exc:
                if '403' in str(exc):
                    raise MarketError('行情访问被拒绝，请检查环境网络白名单或数据源访问限制') from exc
                if attempt:
                    raise MarketError('行情服务连接失败或超时，请稍后重试') from exc
                time.sleep(.3)
        raise MarketError('行情请求失败')

    def stocks(self):
        def fetch():
            stocks={}
            total=None
            for page in range(1,101):
                query=urlencode(dict(pn=page,pz=100,fs='m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81',fields='f12,f14,f13',fid='f12',po=1))
                raw=json.loads(self.request('https://82.push2.eastmoney.com/api/qt/clist/get?'+query))
                data=raw.get('data') or {}
                rows=data.get('diff')
                total=int(data.get('total',0))
                if isinstance(rows,dict):
                    rows=list(rows.values())
                if not rows or not total:
                    raise MarketError('股票列表为空或分页不完整，请稍后重试')
                before=len(stocks)
                for row in rows:
                    try:
                        symbol=normalize_symbols([row['f12']])[0]
                    except ValueError:
                        continue
                    stocks[symbol]=dict(symbol=symbol,code=row['f12'],name=str(row['f14']),market=symbol[:2])
                if len(stocks)==before:
                    raise MarketError('股票列表分页重复，无法确认完整股票池')
                if page*100>=total:
                    break
            else:
                raise MarketError('股票列表超出分页上限')
            return dict(stocks=list(stocks.values()),provider_total=total,source='东方财富公开股票列表')
        try:
            return self.cached('stocks',21600,fetch)
        except (ValueError,KeyError,TypeError) as exc:
            raise MarketError('股票列表格式变化，请稍后重试') from exc

    def quotes(self,symbols):
        symbols=normalize_symbols(symbols)
        def fetch():
            text=self.request('https://qt.gtimg.cn/q='+','.join(symbols),'gb18030')
            rows=[]
            for symbol,body in re.findall(r'v_((?:sh|sz|bj)\d{6})="([^"]*)"',text):
                parts=body.split('~')
                if symbol not in symbols or len(parts)<33:
                    continue
                try:
                    stamp=datetime.strptime(parts[30],'%Y%m%d%H%M%S').replace(tzinfo=SHANGHAI)
                    price,previous=float(parts[3]),float(parts[4])
                    volume=float(parts[6])
                    if not all(math.isfinite(v) for v in (price,previous,volume)) or price<=0 or previous<=0 or volume<0:
                        continue
                    rows.append(dict(symbol=symbol,name=parts[1],price=price,previous_close=previous,change_pct=round((price/previous-1)*100,2),volume_lots=volume,quote_time=stamp.isoformat(),age_seconds=max(0,int((datetime.now(SHANGHAI)-stamp).total_seconds()))))
                except (ValueError,IndexError):
                    continue
            missing=[s for s in symbols if s not in {r['symbol'] for r in rows}]
            if not rows:
                raise MarketError('未取得有效报价，股票可能停牌、已退市或接口暂不支持')
            return dict(quotes=rows,missing=missing,source='腾讯公开报价（可能延迟）')
        return self.cached(('quotes',tuple(symbols)),30,fetch)

    def history(self,symbols,days=500):
        symbols=normalize_symbols(symbols)
        if not isinstance(days,int) or not 62<=days<=1000:
            raise ValueError('日线数量须为 62–1000')
        def fetch():
            data={}
            today=datetime.now(SHANGHAI).date().isoformat()
            # Exclude today's bar even after close: prevents incomplete daily bars entering signals.
            for symbol in symbols:
                query=urlencode({'param':f'{symbol},day,,,{days},qfq'})
                payload=json.loads(self.request('https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?'+query))
                entry=(payload.get('data') or {}).get(symbol) or {}
                bars=entry.get('qfqday') or entry.get('day')
                if not bars:
                    raise MarketError(f'{symbol} 未返回日线；本次不进行部分股票回测')
                if 'qfqday' not in entry:
                    raise MarketError(f'{symbol} 未返回前复权日线，不能混用价格口径')
                output=io.StringIO();writer=csv.writer(output)
                writer.writerow(['symbol','date','open','high','low','close','volume'])
                for row in bars:
                    if len(row)<6:
                        raise MarketError(f'{symbol} 日线字段不足')
                    if row[0]<today:
                        writer.writerow([symbol,row[0],row[1],row[3],row[4],row[2],float(row[5])*100])
                parsed=load_csv(output.getvalue())
                if len(parsed.get(symbol,[]))<62:
                    raise MarketError(f'{symbol} 有效日线少于 62 根')
                data.update(parsed)
            return dict(data=data,source='腾讯历史日线 / 前复权',adjustment='qfq',excluded_today=True,as_of={s:bs[-1]['date'] for s,bs in data.items()})
        try:
            return self.cached(('history',tuple(symbols),days,today_key()),900,fetch)
        except (ValueError,KeyError,TypeError,IndexError) as exc:
            raise MarketError('历史日线数据格式异常，未执行回测') from exc


def today_key():
    return datetime.now(SHANGHAI).date().isoformat()
