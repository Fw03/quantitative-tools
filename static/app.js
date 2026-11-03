'use strict';
const $ = id => document.getElementById(id);
const riskFields = ['capital', 'fee', 'slippage', 'stop', 'max_positions'];
const strategyFields = ['fast', 'slow', 'lookback', 'min_momentum'];
let result = null;
let busy = false;
let quoteTimer;

try {
  const saved = JSON.parse(localStorage.getItem('quant-params') || '{}');
  [...riskFields, 'mode', 'symbols', 'days', 'strategy'].forEach(key => {
    if (saved[key] != null) $(key).value = saved[key];
  });
  strategyFields.forEach(key => {
    if (saved.strategy_params?.[key] != null) {
      $(key).value = saved.strategy_params[key] * (key === 'min_momentum' ? 100 : 1);
    }
  });
} catch (_) { /* Browser storage is optional. */ }

async function request(path, payload) {
  const response = await fetch(path, payload === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
  });
  const value = await response.json();
  if (!response.ok) throw Error(value.error || `HTTP ${response.status}`);
  return value;
}

function table(id, rows, columns) {
  const root = $(id);
  root.replaceChildren();
  if (!rows.length) { root.textContent = '暂无记录'; return; }
  const element = document.createElement('table');
  const head = element.createTHead().insertRow();
  columns.forEach(([, label]) => {
    const th = document.createElement('th'); th.textContent = label; head.append(th);
  });
  const body = element.createTBody();
  rows.forEach(row => {
    const tr = body.insertRow();
    columns.forEach(([key]) => {
      const value = row[key];
      tr.insertCell().textContent = typeof value === 'number'
        ? Number(value.toFixed(3)).toLocaleString() : value ?? '—';
    });
  });
  root.append(element);
}

async function payload() {
  const value = {};
  riskFields.forEach(key => value[key] = Number($(key).value));
  ['mode', 'symbols', 'strategy'].forEach(key => value[key] = $(key).value);
  value.days = Number($('days').value);
  value.strategy_params = {};
  strategyFields.forEach(key => value.strategy_params[key] = Number($(key).value) / (key === 'min_momentum' ? 100 : 1));
  try { localStorage.setItem('quant-params', JSON.stringify(value)); } catch (_) { /* optional */ }
  if (value.mode === 'csv') {
    if (!$('file').files[0]) throw Error('请先选择 CSV 文件');
    if ($('file').files[0].size > 9_000_000) throw Error('CSV 文件须小于 9MB');
    value.csv = await $('file').files[0].text();
  }
  return value;
}

function setBusy(value) {
  busy = value;
  $('run').disabled = value;
  $('compare').disabled = value;
  $('export').disabled = value;
}

function resetReport() {
  result = null;
  $('backtestReport').hidden = true;
  $('comparisonReport').hidden = true;
  $('reportInfo').replaceChildren();
}

function chart(data) {
  const svg = $('chart');
  svg.replaceChildren();
  const groups = [data.curve, data.benchmark.curve];
  const values = groups.flatMap(group => group.map(p => p.equity));
  const low = Math.min(...values), high = Math.max(...values);
  const ns = 'http://www.w3.org/2000/svg';
  groups.forEach((group, index) => {
    const line = document.createElementNS(ns, 'polyline');
    line.setAttribute('points', group.map((p, i) => `${80 + i / Math.max(1, group.length - 1) * 900},${210 - (p.equity - low) / (high - low || 1) * 180}`).join(' '));
    line.setAttribute('stroke', index ? '#72a9f6' : '#62d6b7');
    line.setAttribute('fill', 'none'); line.setAttribute('stroke-width', '2.5');
    svg.append(line);
  });
  [[5, 30, high.toFixed(0)], [5, 210, low.toFixed(0)], [80, 250, data.period.start], [870, 250, data.period.end]].forEach(([x, y, label]) => {
    const text = document.createElementNS(ns, 'text');
    text.setAttribute('x', x); text.setAttribute('y', y); text.setAttribute('fill', '#a6b5cc');
    text.setAttribute('font-size', '14'); text.textContent = label; svg.append(text);
  });
}

function render(data) {
  result = data;
  $('source').textContent = `数据来源：${data.source} ｜ 运行时间：${data.created_at}`;
  $('reportInfo').replaceChildren();
  const info = document.createElement('small');
  info.textContent = `实验编号：${data.run_id} ｜ 行情 SHA256：${data.provenance.data_sha256} ｜ 代码 SHA256：${data.provenance.code_sha256}`;
  $('reportInfo').append(info);
  $('comparisonReport').hidden = !data.evaluations;
  $('backtestReport').hidden = !data.summary;
  if (data.evaluations) {
    const split = data.split;
    $('splitInfo').textContent = `样本内 ${split.train_start} — ${split.train_end}（${split.train_sessions} 日）；样本外 ${split.test_start} — ${split.test_end}（${split.test_sessions} 日）。${data.method}`;
    const status = { passed: '所测截断点一致', failed: '发现不一致', inconclusive: '数据或交易不足' };
    table('comparisons', data.evaluations.map(e => ({
      name: e.strategy.name, train: e.in_sample.return_pct, test: e.out_of_sample.return_pct,
      annual: e.out_of_sample.annual_return_pct, drawdown: e.out_of_sample.max_drawdown,
      sharpe: e.out_of_sample.sharpe, trades: e.out_of_sample.closed_trades,
      excess: e.out_of_sample.excess_return_pct, audit: status[e.audit.status]
    })), [['name', '策略'], ['train', '样本内收益%'], ['test', '样本外收益%'], ['annual', '样本外年化%'], ['drawdown', '样本外回撤%'], ['sharpe', '样本外夏普'], ['trades', '样本外平仓'], ['excess', '样本外超额百分点'], ['audit', '截断检查']]);
    $('auditInfo').textContent = data.evaluations.map(e => `${e.strategy.name}：${e.audit.checks.map(c => `${c.cutoff} / ${c.trades}笔成交 / ${c.matching ? '一致' : '不一致'}`).join('；') || '不足以检查'}`).join(' ｜ ');
    $('comparisonWarnings').textContent = data.warnings.join(' ');
    return;
  }
  const s = data.summary;
  $('stats').replaceChildren();
  const metric = value => value == null ? '—' : value;
  [['账户权益', `${s.equity} 元`], ['累计收益', `${s.return_pct}%`], ['年化收益', `${s.annual_return_pct}%`],
    ['最大回撤', `${s.max_drawdown}%`], ['夏普 / RF=0', metric(s.sharpe)], ['卡玛比率', metric(s.calmar)],
    ['盈利因子', metric(s.profit_factor)], ['平仓胜率', s.win_rate == null ? '无平仓' : `${s.win_rate}%`],
    ['相对股票池超额', `${s.excess_return_pct} 个百分点`], ['佣金及印花税', `${s.commission} 元`],
    ['滑点估算成本', `${s.slippage_cost} 元`], ['平均仓位', `${s.average_exposure_pct}%`],
    ['累计双边换手倍数', s.turnover], ['风控状态', s.halted ? '已熔断' : '正常']].forEach(([label, value]) => {
    const div = document.createElement('div'); div.className = 'card';
    const title = document.createElement('small'), number = document.createElement('div');
    title.textContent = label; number.className = 'value'; number.textContent = value;
    div.append(title, number); $('stats').append(div);
  });
  const title = document.createElement('small');
  title.textContent = `策略：${data.strategy.name} ｜ 短/长均线 ${data.strategy.params.fast}/${data.strategy.params.slow} ｜ 区间 ${data.period.start} — ${data.period.end}`;
  $('reportInfo').append(title);
  chart(data);
  table('signals', data.signals, [['symbol', '股票'], ['date', '行情日期'], ['action', '提醒'], ['close', '收盘'], ['score', '动量%'], ['ma_fast', '短均线'], ['ma_slow', '长均线']]);
  table('holdings', data.holdings, [['symbol', '股票'], ['qty', '数量'], ['entry', '买入价'], ['price', '最新价'], ['cost', '买入成本']]);
  table('monthly', data.monthly, [['month', '月份'], ['return_pct', '收益%']]);
  table('trades', [...data.trades].reverse(), [['date', '日期'], ['symbol', '股票'], ['side', '方向'], ['price', '成交价'], ['qty', '数量'], ['pnl', '已实现盈亏'], ['commission', '佣金及税'], ['slippage_cost', '滑点成本'], ['reason', '原因']]);
}

async function execute(path, value) {
  if (busy) return;
  setBusy(true); resetReport(); $('error').textContent = '';
  $('source').textContent = '正在获取数据并计算，请稍候…';
  try {
    const data = await request(path, value === undefined ? await payload() : value);
    render(data);
    // Restoring/replaying historical experiments never sends fresh trading alerts.
    if (path === '/api/run' && data.summary && 'Notification' in window && Notification.permission === 'granted') {
      const alerts = data.signals.filter(s => ['BUY', 'SELL'].includes(s.action));
      if (alerts.length) new Notification('量化观察台：待关注信号', { body: alerts.map(s => `${s.symbol} ${s.action}（${s.date}）`).join('，') });
    }
    await refreshHistory();
  } catch (error) {
    $('source').textContent = '本次运行失败，未生成报告'; $('error').textContent = error.message;
  } finally { setBusy(false); }
}

function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = name; link.click();
  URL.revokeObjectURL(url);
}

async function refreshHistory() {
  $('historyButton').disabled = true;
  try {
    const data = await request('/api/runs');
    table('history', data.runs, [['created_at', '运行时间'], ['kind', '类型'], ['source', '行情来源'], ['id', '编号'], ['actions', '操作']]);
    const rows = $('history').querySelectorAll('tbody tr');
    data.runs.forEach((run, index) => {
      const cell = rows[index].lastElementChild; cell.replaceChildren();
      [['查看', async () => {
        if (busy) return;
        setBusy(true); $('error').textContent = '';
        try { const record = await request(`/api/runs/${run.id}`); render(record.result); }
        catch (error) { $('error').textContent = error.message; }
        finally { setBusy(false); }
      }], ['复跑快照', () => execute(`/api/runs/${run.id}/replay`, {})], ['导出快照', async () => {
        try { download(await request(`/api/runs/${run.id}/snapshot`), `experiment-${run.id}.json`); }
        catch (error) { $('error').textContent = error.message; }
      }]].forEach(([label, action]) => {
        const button = document.createElement('button'); button.className = 'secondary';
        button.textContent = label; button.onclick = action; cell.append(button);
      });
    });
    $('historyStatus').textContent = `${data.runs.length} 条本地实验；“复跑快照”不联网拉取新行情，使用当前代码重新计算。`;
  } catch (error) { $('historyStatus').textContent = `记录读取失败：${error.message}`; }
  finally { $('historyButton').disabled = false; }
}

async function refreshQuotes() {
  if ($('quotesButton').disabled) return;
  $('quotesButton').disabled = true; $('quoteStatus').textContent = '正在获取报价…';
  try {
    const data = await request('/api/quotes?symbols=' + encodeURIComponent($('symbols').value));
    table('quotes', data.quotes, [['symbol', '代码'], ['name', '名称'], ['price', '最新价'], ['change_pct', '涨跌%'], ['volume_lots', '成交量(手)'], ['quote_time', '行情时间'], ['age_seconds', '距报价秒数']]);
    $('quoteStatus').textContent = `${data.source} ｜ 获取：${data.fetched_at}${data.cached ? '（30秒内缓存）' : ''}${data.missing.length ? ' ｜ 缺失：' + data.missing.join(',') : ''} ｜ 非交易时段为末次报价`;
  } catch (error) { $('quotes').replaceChildren(); $('quoteStatus').textContent = `报价获取失败：${error.message}`; }
  finally { $('quotesButton').disabled = false; }
}

$('run').onclick = () => execute('/api/run');
$('compare').onclick = () => execute('/api/compare');
$('export').onclick = () => { if (result) download(result, `report-${result.run_id}.json`); };
$('historyButton').onclick = refreshHistory;
$('quotesButton').onclick = refreshQuotes;
$('autoQuotes').onchange = () => {
  clearInterval(quoteTimer);
  if ($('autoQuotes').checked) {
    refreshQuotes();
    quoteTimer = setInterval(() => { if (!document.hidden) refreshQuotes(); }, 60000);
  }
};
$('notify').onclick = async () => {
  try {
    if (!('Notification' in window)) throw Error('当前浏览器不支持桌面通知');
    const permission = await Notification.requestPermission();
    $('error').textContent = permission === 'granted' ? '通知已开启，仅手动运行策略后提醒；历史记录不会发出通知' : '未获得通知权限';
  } catch (error) { $('error').textContent = error.message; }
};
$('stocksButton').onclick = async () => {
  $('stocksButton').disabled = true; $('stockStatus').textContent = '正在获取股票列表…';
  try {
    const data = await request('/api/stocks?q=' + encodeURIComponent($('stockQuery').value));
    table('stocks', data.stocks.slice(0, 100), [['symbol', '代码'], ['name', '名称'], ['market', '市场']]);
    $('stockStatus').textContent = `${data.source} ｜ 匹配 ${data.stocks.length} 只，展示前100只 ｜ 获取：${data.fetched_at}${data.cached ? '（缓存）' : ''}`;
  } catch (error) { $('stocks').replaceChildren(); $('stockStatus').textContent = `股票列表获取失败：${error.message}`; }
  finally { $('stocksButton').disabled = false; }
};
function strategyInfo() {
  $('strategyInfo').textContent = $('strategy').value === 'ma_cross'
    ? '双均线：短均线上穿长均线生成买入信号，短均线低于长均线退出；突破阈值不用于此策略，动量周期只影响候选排序。'
    : '趋势突破：收盘突破此前最高价、短均线高于长均线，且动量超过阈值；跌破短均线退出。两策略均受组合风控约束。';
}
$('strategy').onchange = strategyInfo;
strategyInfo();
refreshHistory();
if ($('mode').value === 'market') refreshQuotes();
// Running is explicit: loading a page must not create a new experiment or alert.
