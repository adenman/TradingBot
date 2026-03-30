import { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine,
} from 'recharts';

const C = {
  bg: '#080b0f', surface: '#0e1117', card: '#12161e',
  border: '#1e2433', text: '#e2e8f0', muted: '#4a5568', subtle: '#2d3748',
  green: '#00d4a0', red: '#ff4d6a', blue: '#4488ff',
  yellow: '#f5a623', purple: '#a78bfa', orange: '#fb923c',
};

const fmt$ = (n, dec = 2) => n != null ? `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec })}` : '—';
const fmtPct = (n, dec = 1) => n != null ? `${Number(n).toFixed(dec)}%` : '—';
const clr = (n) => (n >= 0 ? C.green : C.red);

const card = { backgroundColor: C.card, borderRadius: '10px', border: `1px solid ${C.border}`, padding: '16px 18px' };
const cardTitle = { fontSize: '0.68rem', color: C.muted, textTransform: 'uppercase', letterSpacing: '1.2px', fontWeight: 700, marginBottom: '12px' };
const statLabel = { fontSize: '0.68rem', color: C.muted, textTransform: 'uppercase', letterSpacing: '0.6px', marginBottom: '2px' };

function Stat({ label, value, color }) {
  return (
    <div>
      <div style={statLabel}>{label}</div>
      <div className="stat-val" style={{ fontSize: '1rem', fontWeight: 700, color: color || C.text, lineHeight: 1.2 }}>{value ?? '—'}</div>
    </div>
  );
}

function Badge({ children, color, bg }) {
  return (
    <span style={{
      fontSize: '0.72rem', fontWeight: 700, padding: '3px 10px', borderRadius: '20px',
      backgroundColor: bg || (color + '18'), color: color || C.text,
      border: `1px solid ${(color || C.muted) + '40'}`,
    }}>{children}</span>
  );
}

function TrendBadge({ trend }) {
  const color = trend === 'BULLISH' ? C.green : trend === 'BEARISH' ? C.red : C.muted;
  return <Badge color={color}>{trend || 'NEUTRAL'}</Badge>;
}

function StrengthBar({ value, color }) {
  return (
    <div style={{ height: '3px', backgroundColor: C.subtle, borderRadius: '2px', marginTop: '5px', overflow: 'hidden' }}>
      <div style={{ height: '100%', width: `${Math.min((value || 0) * 100, 100)}%`, backgroundColor: color || C.blue, borderRadius: '2px', transition: 'width 0.4s' }} />
    </div>
  );
}

function Toggle({ label, value, onChange }) {
  return (
    <div onClick={() => onChange(!value)} style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', userSelect: 'none' }}>
      <div style={{ width: '34px', height: '18px', borderRadius: '9px', backgroundColor: value ? C.blue : C.subtle, position: 'relative', transition: 'background 0.2s', flexShrink: 0 }}>
        <div style={{ position: 'absolute', top: '2px', left: value ? '18px' : '2px', width: '14px', height: '14px', borderRadius: '50%', backgroundColor: '#fff', transition: 'left 0.2s' }} />
      </div>
      <span style={{ fontSize: '0.82rem', color: C.muted }}>{label}</span>
    </div>
  );
}

function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{ background: '#1a1f2e', border: `1px solid ${C.border}`, borderRadius: '6px', padding: '8px 12px', fontSize: '0.82rem' }}>
      <div style={{ color: C.muted, marginBottom: '4px' }}>{label}</div>
      {payload.filter(p => p.value != null).map((p, i) => (
        <div key={i} style={{ color: p.color || C.text, fontWeight: 600 }}>
          {p.name}: {typeof p.value === 'number' ? p.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : p.value}
        </div>
      ))}
    </div>
  );
}

function TradeDot(props) {
  const { cx, cy, payload } = props;
  if (!payload?.buy && !payload?.sell || cx == null || cy == null) return null;
  const isBuy = !!payload.buy;
  return (
    <g>
      <circle cx={cx} cy={cy} r={6} fill={isBuy ? C.green : C.red} stroke={C.bg} strokeWidth={1.5} />
      <text x={cx} y={cy + 1} textAnchor="middle" dominantBaseline="middle" fill="#fff" fontSize={7} fontWeight="bold">{isBuy ? 'B' : 'S'}</text>
    </g>
  );
}

const WINDOWS = [
  { label: '1hr', value: 60 }, { label: '3hr', value: 180 },
  { label: '6hr', value: 360 }, { label: '1d', value: 1440 },
  { label: '3d', value: 4320 }, { label: '1w', value: 10080 },
];

export default function App() {
  const [bot, setBot] = useState(null);
  const [conn, setConn] = useState('Connecting...');
  const [chartTab, setChartTab] = useState('price');
  const [priceWindow, setPriceWindow] = useState(60);
  const [eqWindow, setEqWindow] = useState(60);
  const [form, setForm] = useState({});
  const [toggles, setToggles] = useState({});
  const wsRef = useRef(null);
  const reconnRef = useRef(null);
  const logBoxRef = useRef(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket('wss://eloy-precedentless-nonprohibitorily.ngrok-free.dev/ws');
    wsRef.current = ws;
    ws.onopen = () => { setConn('Connected'); clearTimeout(reconnRef.current); };
    ws.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        setBot(data);
        setToggles(prev => Object.keys(prev).length ? prev : {
          volume_filter: data.settings?.volume_filter ?? true,
          mtf_trend_filter: data.settings?.mtf_trend_filter ?? true,
          trend_filter: data.settings?.trend_filter ?? true,
          volatility_scaling: data.settings?.volatility_scaling ?? true,
          auto_range: data.settings?.auto_range ?? true,
        });
      } catch {}
    };
    ws.onclose = () => { setConn('Disconnected'); reconnRef.current = setTimeout(connect, 3000); };
    ws.onerror = () => setConn('Error');
  }, []);

  useEffect(() => { connect(); return () => { wsRef.current?.close(); clearTimeout(reconnRef.current); }; }, [connect]);

  useEffect(() => {
    const box = logBoxRef.current;
    if (!box) return;
    if (box.scrollHeight - box.scrollTop - box.clientHeight < 60) box.scrollTop = box.scrollHeight;
  }, [bot?.logs]);

  const saveSettings = (e) => {
    e.preventDefault();
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const p = { type: 'UPDATE_SETTINGS', ...toggles };
    if (form.cash) p.cash = parseFloat(form.cash);
    if (form.trade_size_usd) p.trade_size_usd = parseFloat(form.trade_size_usd);
    if (form.grid_levels) p.grid_levels = parseInt(form.grid_levels);
    if (form.grid_upper) p.grid_upper = parseFloat(form.grid_upper);
    if (form.grid_lower) p.grid_lower = parseFloat(form.grid_lower);
    wsRef.current.send(JSON.stringify(p));
    setForm({});
  };

  if (!bot) {
    return (
      <div style={{ minHeight: '100vh', backgroundColor: C.bg, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '12px' }}>
        <div style={{ fontSize: '1.5rem' }}>⚡</div>
        <div style={{ color: C.muted, fontSize: '0.9rem' }}>{conn} — waiting for backend</div>
      </div>
    );
  }

  const { prices, portfolio: pf, indicators, macro, logs, settings, stats: st, grid_state: gs, circuit_breaker: cb, price_chart, equity_history, trade_markers } = bot;
  const btcPrice = prices?.['BTC/USD'] || 0;
  const ind = indicators?.['BTC/USD'] || {};
  const btcMacro = macro?.['BTC/USD'] || {};

  const mkrMap = {};
  (trade_markers || []).forEach(m => {
    if (!mkrMap[m.time]) mkrMap[m.time] = {};
    if (m.action === 'BUY') mkrMap[m.time].buy = m.price;
    else mkrMap[m.time].sell = m.price;
  });
  const priceData = (price_chart || []).map(p => ({ ...p, buy: mkrMap[p.time]?.buy ?? null, sell: mkrMap[p.time]?.sell ?? null }));
  const slicedPriceData = priceData.slice(-priceWindow);
  const slicedEqData = (equity_history || []).slice(-eqWindow);
  const connColor = conn === 'Connected' ? C.green : conn === 'Disconnected' ? C.red : C.yellow;

  const inputStyle = { width: '100%', padding: '9px 10px', borderRadius: '6px', border: `1px solid ${C.border}`, backgroundColor: '#0e1117', color: C.text, fontSize: '0.88rem', outline: 'none' };
  const btnStyle = (active, color) => ({
    padding: '5px 11px', borderRadius: '5px', fontSize: '0.72rem', fontWeight: 600,
    cursor: 'pointer', border: 'none', backgroundColor: active ? color : C.subtle,
    color: active ? '#fff' : C.muted,
  });

  return (
    <div className="tb-root" style={{ minHeight: '100vh', backgroundColor: C.bg, color: C.text, padding: '16px 20px' }}>

      {/* Header */}
      <header className="tb-header">
        <div className="tb-header-left">
          <span style={{ fontSize: '0.75rem', color: C.muted, fontWeight: 700, letterSpacing: '1.5px', textTransform: 'uppercase' }}>⚡ Adaptive Grid</span>
          <span className="tb-price">{fmt$(btcPrice, 2)}</span>
          {btcMacro.trend_pct != null && <Badge color={btcMacro.trend_pct >= 0 ? C.green : C.red}>90d {btcMacro.trend_pct >= 0 ? '+' : ''}{btcMacro.trend_pct?.toFixed(1)}%</Badge>}
          <TrendBadge trend={ind.trend} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          {cb?.active && <Badge color={C.orange} bg="#7f1d1d33">⚡ CB ACTIVE</Badge>}
          <Badge color={connColor}>{conn}</Badge>
        </div>
      </header>

      {cb?.active && (
        <div style={{ backgroundColor: '#7f1d1d22', border: `1px solid ${C.red}44`, borderRadius: '8px', padding: '10px 16px', marginBottom: '12px', color: '#fca5a5', fontSize: '0.85rem' }}>
          ⚡ <strong>Circuit Breaker:</strong> {cb.reason}
        </div>
      )}

      {/* Row 1: Stats */}
      <div className="grid-3">
        <div style={card}>
          <div style={cardTitle}>Portfolio</div>
          <div className="stat-grid">
            <Stat label="Total Value" value={fmt$(pf?.total_value)} />
            <Stat label="Total P&L" value={`${pf?.total_profit >= 0 ? '+' : ''}${fmt$(pf?.total_profit)}`} color={clr(pf?.total_profit)} />
            <Stat label="Cash" value={fmt$(pf?.cash)} />
            <Stat label="BTC" value={pf?.holdings?.['BTC/USD']?.toFixed(6)} />
            <Stat label="Unrealized" value={`${pf?.unrealized_pnl >= 0 ? '+' : ''}${fmt$(pf?.unrealized_pnl)}`} color={clr(pf?.unrealized_pnl)} />
            <Stat label="Realized" value={`${pf?.realized_pnl >= 0 ? '+' : ''}${fmt$(pf?.realized_pnl)}`} color={clr(pf?.realized_pnl)} />
            <Stat label="Fees Paid" value={fmt$(pf?.total_fees, 4)} color={C.orange} />
            <Stat label="Cost Basis" value={pf?.cost_basis?.['BTC/USD'] > 0 ? fmt$(pf.cost_basis['BTC/USD'], 0) : '—'} />
          </div>
        </div>

        <div style={card}>
          <div style={cardTitle}>Grid State</div>
          <div className="stat-grid">
            <Stat label="Status" value={gs?.initialized ? '✅ Active' : '⏳ Init'} color={gs?.initialized ? C.green : C.yellow} />
            <Stat label="Positions" value={`${gs?.open_positions} / ${settings?.max_open_positions}`} color={gs?.open_positions > 0 ? C.blue : C.muted} />
            <Stat label="Range Low" value={gs?.range_low > 0 ? fmt$(gs.range_low, 0) : '—'} />
            <Stat label="Range High" value={gs?.range_high > 0 ? fmt$(gs.range_high, 0) : '—'} />
            <Stat label="Levels" value={gs?.levels_count || settings?.grid_levels} />
            <Stat label="Index" value={gs?.current_index >= 0 ? gs.current_index : '—'} />
          </div>
          {gs?.range_low > 0 && gs?.range_high > 0 && (
            <div style={{ marginTop: '12px' }}>
              <div style={{ height: '5px', backgroundColor: C.subtle, borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${Math.max(0, Math.min(100, (btcPrice - gs.range_low) / (gs.range_high - gs.range_low) * 100))}%`, backgroundColor: C.blue, borderRadius: '3px', transition: 'width 0.3s' }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.65rem', color: C.muted, marginTop: '3px' }}>
                <span>{fmt$(gs.range_low, 0)}</span>
                <span style={{ color: C.text }}>{fmt$(btcPrice, 0)}</span>
                <span>{fmt$(gs.range_high, 0)}</span>
              </div>
            </div>
          )}
        </div>

        <div style={card}>
          <div style={cardTitle}>Trade Stats</div>
          <div className="stat-grid">
            <Stat label="Win Rate" value={fmtPct(st?.win_rate)} color={st?.win_rate >= 50 ? C.green : C.red} />
            <Stat label="Trades" value={st?.total_trades} />
            <Stat label="Profit Factor" value={st?.profit_factor > 0 ? st.profit_factor?.toFixed(2) : '—'} color={st?.profit_factor >= 1 ? C.green : C.red} />
            <Stat label="Max Drawdown" value={fmtPct(st?.max_drawdown)} color={st?.max_drawdown > 10 ? C.red : C.text} />
            <Stat label="Largest Win" value={st?.largest_win > 0 ? `+${fmt$(st.largest_win, 3)}` : '—'} color={C.green} />
            <Stat label="Largest Loss" value={st?.largest_loss < 0 ? fmt$(st.largest_loss, 3) : '—'} color={C.red} />
            <Stat label="Avg Win" value={st?.avg_win > 0 ? `+${fmt$(st.avg_win, 3)}` : '—'} color={C.green} />
            <Stat label="Avg Loss" value={st?.avg_loss < 0 ? fmt$(st.avg_loss, 3) : '—'} color={C.red} />
          </div>
        </div>
      </div>

      {/* Row 2: Chart */}
      <div style={{ ...card, marginBottom: '12px' }}>
        <div className="chart-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
          <div style={cardTitle}>Chart</div>
          <div className="chart-btns">
            {chartTab === 'price' && WINDOWS.map(w => <button key={w.value} onClick={() => setPriceWindow(w.value)} style={btnStyle(priceWindow === w.value, C.blue)}>{w.label}</button>)}
            {chartTab === 'equity' && WINDOWS.map(w => <button key={w.value} onClick={() => setEqWindow(w.value)} style={btnStyle(eqWindow === w.value, C.green)}>{w.label}</button>)}
            <div style={{ width: '1px', height: '16px', backgroundColor: C.border, margin: '0 2px' }} />
            {['price', 'equity'].map(t => <button key={t} onClick={() => setChartTab(t)} style={{ ...btnStyle(chartTab === t, C.blue), padding: '5px 14px' }}>{t === 'price' ? 'Price' : 'Equity'}</button>)}
          </div>
        </div>

        {chartTab === 'price' && (
          priceData.length > 1 ? (
            <ResponsiveContainer width="100%" height={240}>
              <AreaChart data={slicedPriceData} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.blue} stopOpacity={0.25} />
                    <stop offset="95%" stopColor={C.blue} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="time" tick={{ fill: C.muted, fontSize: 9 }} tickLine={false} axisLine={false} minTickGap={40} />
                <YAxis domain={['auto', 'auto']} tick={{ fill: C.muted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `$${v.toLocaleString()}`} width={68} />
                <Tooltip content={<ChartTooltip />} />
                {gs?.range_high > 0 && <ReferenceLine y={gs.range_high} stroke={C.red} strokeDasharray="4 3" strokeOpacity={0.5} />}
                {gs?.range_low > 0 && <ReferenceLine y={gs.range_low} stroke={C.green} strokeDasharray="4 3" strokeOpacity={0.5} />}
                <Area type="monotone" dataKey="price" stroke={C.blue} strokeWidth={2} fill="url(#priceGrad)" name="BTC Price" dot={<TradeDot />} activeDot={{ r: 4, fill: C.blue }} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: '240px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted, fontSize: '0.88rem' }}>Warming up — price data incoming…</div>
          )
        )}

        {chartTab === 'equity' && (
          (equity_history || []).length > 1 ? (
            <>
              <ResponsiveContainer width="100%" height={180}>
                <AreaChart data={slicedEqData} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={C.green} stopOpacity={0.25} />
                      <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                  <XAxis dataKey="time" tick={{ fill: C.muted, fontSize: 9 }} tickLine={false} axisLine={false} minTickGap={40} />
                  <YAxis domain={['auto', 'auto']} tick={{ fill: C.muted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `$${v}`} width={52} />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine y={pf?.initial_balance} stroke={C.yellow} strokeDasharray="6 3" strokeOpacity={0.5} />
                  <Area type="monotone" dataKey="value" stroke={C.green} strokeWidth={2} fill="url(#eqGrad)" name="Equity $" dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
              <ResponsiveContainer width="100%" height={70} style={{ marginTop: '8px' }}>
                <BarChart data={slicedEqData} margin={{ top: 0, right: 6, left: 0, bottom: 0 }}>
                  <XAxis dataKey="time" hide />
                  <YAxis tick={{ fill: C.muted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `$${v}`} width={48} />
                  <Tooltip content={<ChartTooltip />} />
                  <Bar dataKey="pnl" name="P&L $" radius={[2, 2, 0, 0]} isAnimationActive={false}
                    fill={C.blue}
                    cell={slicedEqData.map((e, i) => <cell key={i} fill={e.pnl >= 0 ? C.green : C.red} />)}
                  />
                </BarChart>
              </ResponsiveContainer>
            </>
          ) : (
            <div style={{ height: '240px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted, fontSize: '0.88rem' }}>Equity history builds after the first candle closes…</div>
          )
        )}
      </div>

      {/* Row 3: Indicators */}
      <div className="grid-2">
        <div style={card}>
          <div style={cardTitle}>Market Signals</div>
          <div className="stat-grid">
            <Stat label="RSI (14)" value={ind.rsi?.toFixed(1)} color={ind.rsi > 70 ? C.red : ind.rsi < 30 ? C.green : C.text} />
            <Stat label="Stoch RSI" value={ind.stoch_rsi?.toFixed(1)} color={ind.stoch_rsi > 80 ? C.red : ind.stoch_rsi < 20 ? C.green : C.text} />
            <Stat label="MACD" value={ind.macd_histogram > 0 ? '↗ Bullish' : '↘ Bearish'} color={ind.macd_histogram > 0 ? C.green : C.red} />
            <Stat label="MACD Hist" value={ind.macd_histogram?.toFixed(2)} color={ind.macd_histogram > 0 ? C.green : C.red} />
            <Stat label="ATR" value={ind.atr > 0 ? fmt$(ind.atr) : '—'} />
            <Stat label="Volatility" value={ind.volatility > 0 ? fmt$(ind.volatility) : '—'} />
            <Stat label="Vol. Ratio" value={ind.volume_ratio?.toFixed(2)} color={ind.volume_ratio < 0.6 ? C.red : ind.volume_ratio > 1.5 ? C.green : C.text} />
            <Stat label="BB Width" value={ind.bb_upper > 0 ? fmt$(ind.bb_upper - ind.bb_lower, 0) : '—'} />
          </div>
          <div style={{ marginTop: '12px', paddingTop: '10px', borderTop: `1px solid ${C.border}`, display: 'flex', gap: '14px', fontSize: '0.73rem', flexWrap: 'wrap' }}>
            <span style={{ color: C.muted }}>BB <span style={{ color: btcPrice >= ind.bb_upper ? C.red : C.subtle }}>{fmt$(ind.bb_upper, 0)}</span></span>
            <span style={{ color: C.muted }}>Mid <span style={{ color: C.text }}>{fmt$(ind.bb_mid, 0)}</span></span>
            <span style={{ color: C.muted }}>Low <span style={{ color: btcPrice <= ind.bb_lower ? C.green : C.subtle }}>{fmt$(ind.bb_lower, 0)}</span></span>
          </div>
        </div>

        <div style={card}>
          <div style={cardTitle}>Trend Analysis</div>
          {[['1-Min Trend', ind.trend, ind.trend_strength], ['5-Min Trend', ind.trend_5m, ind.trend_strength_5m]].map(([label, trend, strength]) => (
            <div key={label} style={{ marginBottom: '12px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '3px' }}>
                <span style={statLabel}>{label}</span>
                <TrendBadge trend={trend} />
              </div>
              <StrengthBar value={strength} color={trend === 'BULLISH' ? C.green : trend === 'BEARISH' ? C.red : C.muted} />
              <div style={{ fontSize: '0.65rem', color: C.muted, marginTop: '2px' }}>{((strength || 0) * 100).toFixed(1)}% strength</div>
            </div>
          ))}
          <div style={{ paddingTop: '10px', borderTop: `1px solid ${C.border}` }}>
            <div className="stat-grid">
              <Stat label="MTF Agreement" value={ind.mtf_agreement ? '✅ Aligned' : '❌ Diverged'} color={ind.mtf_agreement ? C.green : C.yellow} />
              <Stat label="EMA Cross" value={ind.ema_fast > ind.ema_slow ? '↗ F > S' : '↘ F < S'} color={ind.ema_fast > ind.ema_slow ? C.green : C.red} />
              <Stat label="EMA Fast (9)" value={ind.ema_fast > 0 ? fmt$(ind.ema_fast, 0) : '—'} />
              <Stat label="EMA Slow (21)" value={ind.ema_slow > 0 ? fmt$(ind.ema_slow, 0) : '—'} />
            </div>
          </div>
        </div>
      </div>

      {/* Row 4: Settings */}
      <div style={{ ...card, marginBottom: '12px' }}>
        <div style={cardTitle}>Settings</div>
        <form onSubmit={saveSettings}>
          <div className="settings-inputs">
            {[
              { key: 'cash', label: 'Cash ($)', ph: pf?.cash?.toFixed(2) },
              { key: 'trade_size_usd', label: 'Trade Size ($)', ph: settings?.trade_size_usd },
              { key: 'grid_levels', label: 'Grid Levels', ph: settings?.grid_levels },
              { key: 'grid_lower', label: 'Grid Lower ($)', ph: settings?.grid_lower > 0 ? settings.grid_lower : 'auto' },
              { key: 'grid_upper', label: 'Grid Upper ($)', ph: settings?.grid_upper > 0 ? settings.grid_upper : 'auto' },
            ].map(({ key, label, ph }) => (
              <div key={key}>
                <label style={{ ...statLabel, display: 'block', marginBottom: '5px' }}>{label}</label>
                <input type="number" step="any" placeholder={ph} value={form[key] || ''} onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))} style={inputStyle} />
              </div>
            ))}
          </div>
          <div className="settings-toggles">
            {[
              { key: 'volume_filter', label: 'Volume Filter' },
              { key: 'mtf_trend_filter', label: 'MTF Filter' },
              { key: 'trend_filter', label: 'Trend Filter' },
              { key: 'volatility_scaling', label: 'Vol. Scaling' },
              { key: 'auto_range', label: 'Auto Range' },
            ].map(({ key, label }) => (
              <Toggle key={key} label={label} value={toggles[key] ?? settings?.[key] ?? true} onChange={v => setToggles(t => ({ ...t, [key]: v }))} />
            ))}
          </div>

          {/* Live Trading Toggle — separate section with warning */}
          <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: '14px', marginBottom: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                <Toggle
                  label=""
                  value={toggles['live_trading'] ?? settings?.live_trading ?? false}
                  onChange={v => {
                    if (v && !window.confirm('⚠️ ENABLE LIVE TRADING?\n\nThis will place REAL orders with REAL money on Coinbase.\n\nMake sure you understand the risks. Continue?')) return;
                    setToggles(t => ({ ...t, live_trading: v }));
                  }}
                />
                <span style={{ fontWeight: 700, color: (toggles['live_trading'] ?? settings?.live_trading) ? C.red : C.muted, fontSize: '0.9rem' }}>
                  {(toggles['live_trading'] ?? settings?.live_trading) ? '🔴 LIVE TRADING — REAL MONEY' : '⚪ Paper Trading'}
                </span>
              </div>
              {(toggles['live_trading'] ?? settings?.live_trading) && (
                <Badge color={C.red}>Daily Loss Limit: ${settings?.daily_loss_limit_usd ?? 20}</Badge>
              )}
            </div>
            {(toggles['live_trading'] ?? settings?.live_trading) && (
              <div style={{ marginTop: '8px', fontSize: '0.78rem', color: C.orange, backgroundColor: '#7f1d1d22', border: `1px solid ${C.red}33`, borderRadius: '6px', padding: '8px 12px' }}>
                ⚠️ Live mode active — bot is placing real orders on Coinbase. Monitor closely.
              </div>
            )}
          </div>
          <button type="submit" style={{ padding: '10px 24px', backgroundColor: C.blue, color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 700, fontSize: '0.9rem' }}>
            Save Settings
          </button>
        </form>
      </div>

      {/* Row 5: Logs */}
      <div style={card}>
        <div style={cardTitle}>Agent Logs</div>
        <div ref={logBoxRef} className="log-box">
          {[...logs].map((log, i) => {
            let color = C.muted;
            if (log.includes('BUY')) color = C.green;
            else if (log.includes('SELL')) color = C.red;
            else if (log.includes('CIRCUIT BREAKER')) color = C.orange;
            else if (log.includes('Trailing TP')) color = C.yellow;
            else if (log.includes('rebalanc') || log.includes('Grid init') || log.includes('Warmup') || log.includes('WebSocket')) color = C.purple;
            else if (log.includes('Settings')) color = C.blue;
            return <div key={i} style={{ padding: '3px 0', borderBottom: `1px solid ${C.border}`, color, wordBreak: 'break-all' }}>{log}</div>;
          })}
        </div>
      </div>
    </div>
  );
}
