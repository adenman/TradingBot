import { useState, useEffect, useRef, useCallback } from 'react';
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  ReferenceLine, Dot,
} from 'recharts';

// ── Palette ──────────────────────────────────────────────────────────────────
const C = {
  bg: '#080b0f',
  surface: '#0e1117',
  card: '#12161e',
  border: '#1e2433',
  borderHover: '#2a3347',
  text: '#e2e8f0',
  muted: '#4a5568',
  subtle: '#2d3748',
  green: '#00d4a0',
  red: '#ff4d6a',
  blue: '#4488ff',
  yellow: '#f5a623',
  purple: '#a78bfa',
  orange: '#fb923c',
};

// ── Helpers ───────────────────────────────────────────────────────────────────
const fmt$ = (n, dec = 2) => n != null ? `$${Number(n).toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec })}` : '—';
const fmtPct = (n, dec = 1) => n != null ? `${Number(n).toFixed(dec)}%` : '—';
const clr = (n) => n >= 0 ? C.green : C.red;

// ── Shared styles ─────────────────────────────────────────────────────────────
const card = {
  backgroundColor: C.card,
  borderRadius: '10px',
  border: `1px solid ${C.border}`,
  padding: '18px 20px',
};
const cardTitle = {
  fontSize: '0.68rem',
  color: C.muted,
  textTransform: 'uppercase',
  letterSpacing: '1.2px',
  fontWeight: 700,
  marginBottom: '14px',
};
const statLabel = {
  fontSize: '0.68rem',
  color: C.muted,
  textTransform: 'uppercase',
  letterSpacing: '0.6px',
  marginBottom: '3px',
};
const statVal = (color) => ({
  fontSize: '1.05rem',
  fontWeight: 700,
  color: color || C.text,
  lineHeight: 1.2,
});

// ── Sub-components ────────────────────────────────────────────────────────────
function Stat({ label, value, color, small }) {
  return (
    <div>
      <div style={statLabel}>{label}</div>
      <div style={{ ...statVal(color), fontSize: small ? '0.9rem' : '1.05rem' }}>{value ?? '—'}</div>
    </div>
  );
}

function Badge({ children, color, bg }) {
  return (
    <span style={{
      fontSize: '0.72rem', fontWeight: 700, padding: '3px 10px',
      borderRadius: '20px', letterSpacing: '0.5px',
      backgroundColor: bg || (color + '18'),
      color: color || C.text,
      border: `1px solid ${(color || C.muted) + '40'}`,
    }}>{children}</span>
  );
}

function TrendBadge({ trend }) {
  const color = trend === 'BULLISH' ? C.green : trend === 'BEARISH' ? C.red : C.muted;
  return <Badge color={color}>{trend || 'NEUTRAL'}</Badge>;
}

function StrengthBar({ value, color }) {
  const pct = Math.min((value || 0) * 100, 100);
  return (
    <div style={{ height: '3px', backgroundColor: C.subtle, borderRadius: '2px', marginTop: '5px', overflow: 'hidden' }}>
      <div style={{ height: '100%', width: `${pct}%`, backgroundColor: color || C.blue, borderRadius: '2px', transition: 'width 0.4s ease' }} />
    </div>
  );
}

function Toggle({ label, value, onChange }) {
  return (
    <div onClick={() => onChange(!value)} style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', userSelect: 'none' }}>
      <div style={{
        width: '34px', height: '18px', borderRadius: '9px',
        backgroundColor: value ? C.blue : C.subtle,
        position: 'relative', transition: 'background 0.2s', flexShrink: 0,
      }}>
        <div style={{
          position: 'absolute', top: '2px', left: value ? '18px' : '2px',
          width: '14px', height: '14px', borderRadius: '50%',
          backgroundColor: '#fff', transition: 'left 0.2s',
          boxShadow: '0 1px 3px #0006',
        }} />
      </div>
      <span style={{ fontSize: '0.82rem', color: C.muted }}>{label}</span>
    </div>
  );
}

// ── Chart tooltip ─────────────────────────────────────────────────────────────
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={{
      background: '#1a1f2e', border: `1px solid ${C.border}`,
      borderRadius: '6px', padding: '8px 12px', fontSize: '0.82rem',
      boxShadow: '0 8px 24px #0008',
    }}>
      <div style={{ color: C.muted, marginBottom: '4px' }}>{label}</div>
      {payload.filter(p => p.value != null).map((p, i) => (
        <div key={i} style={{ color: p.color || C.text, fontWeight: 600 }}>
          {p.name}: {typeof p.value === 'number' && p.name?.includes('$')
            ? fmt$(p.value) : typeof p.value === 'number'
            ? p.value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
            : p.value}
        </div>
      ))}
    </div>
  );
}

// Custom dot for buy/sell markers on price chart
function TradeDot(props) {
  const { cx, cy, payload } = props;
  if (!payload?.buy && !payload?.sell) return null;
  if (cx == null || cy == null) return null;
  const isBuy = !!payload.buy;
  return (
    <g>
      <circle cx={cx} cy={cy} r={6} fill={isBuy ? C.green : C.red} stroke={C.bg} strokeWidth={1.5} />
      <text x={cx} y={cy + 1} textAnchor="middle" dominantBaseline="middle" fill="#fff" fontSize={7} fontWeight="bold">
        {isBuy ? 'B' : 'S'}
      </text>
    </g>
  );
}

// ── Main App ──────────────────────────────────────────────────────────────────
export default function App() {
  const [bot, setBot] = useState(null);
  const [conn, setConn] = useState('Connecting...');
  const [chartTab, setChartTab] = useState('price');
  const [form, setForm] = useState({});
  const [toggles, setToggles] = useState({});
  const wsRef = useRef(null);
  const reconnRef = useRef(null);
  const logBoxRef = useRef(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    const ws = new WebSocket('ws://localhost:8000/ws');
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
    const near = box.scrollHeight - box.scrollTop - box.clientHeight < 60;
    if (near) box.scrollTop = box.scrollHeight;
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
        <div style={{ color: C.muted, fontSize: '0.9rem' }}>{conn} — waiting for backend on :8000</div>
      </div>
    );
  }

  const { prices, portfolio: pf, indicators, macro, logs, settings, stats: st, grid_state: gs, circuit_breaker: cb, price_chart, equity_history, trade_markers } = bot;
  const btcPrice = prices?.['BTC/USD'] || 0;
  const ind = indicators?.['BTC/USD'] || {};
  const btcMacro = macro?.['BTC/USD'] || {};

  // Build price chart data with trade markers overlaid
  const mkrMap = {};
  (trade_markers || []).forEach(m => {
    if (!mkrMap[m.time]) mkrMap[m.time] = {};
    if (m.action === 'BUY') mkrMap[m.time].buy = m.price;
    else mkrMap[m.time].sell = m.price;
  });
  const priceData = (price_chart || []).map(p => ({
    ...p,
    buy: mkrMap[p.time]?.buy ?? null,
    sell: mkrMap[p.time]?.sell ?? null,
  }));

  const connColor = conn === 'Connected' ? C.green : conn === 'Disconnected' ? C.red : C.yellow;
  const trendColor = ind.trend === 'BULLISH' ? C.green : ind.trend === 'BEARISH' ? C.red : C.muted;

  const inputStyle = {
    width: '100%', padding: '8px 10px', borderRadius: '6px',
    border: `1px solid ${C.border}`, backgroundColor: '#0e1117',
    color: C.text, boxSizing: 'border-box', fontSize: '0.88rem',
    outline: 'none',
  };

  return (
    <div style={{ minHeight: '100vh', backgroundColor: C.bg, color: C.text, fontFamily: 'system-ui, -apple-system, sans-serif', padding: '16px 20px', boxSizing: 'border-box' }}>

      {/* ── Header ── */}
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px', flexWrap: 'wrap', gap: '10px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '16px', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.78rem', color: C.muted, fontWeight: 700, letterSpacing: '1.5px', textTransform: 'uppercase' }}>⚡ Adaptive Grid</span>
          <span style={{ fontSize: '2.4rem', fontWeight: 800, color: '#fff', letterSpacing: '-1px' }}>
            {fmt$(btcPrice, 2)}
          </span>
          {btcMacro.trend_pct != null && (
            <Badge color={btcMacro.trend_pct >= 0 ? C.green : C.red}>
              90d {btcMacro.trend_pct >= 0 ? '+' : ''}{btcMacro.trend_pct?.toFixed(1)}%
            </Badge>
          )}
          <TrendBadge trend={ind.trend} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {cb?.active && (
            <Badge color={C.orange} bg="#7f1d1d33">⚡ CIRCUIT BREAKER</Badge>
          )}
          <Badge color={connColor}>{conn}</Badge>
        </div>
      </header>

      {/* Circuit breaker detail */}
      {cb?.active && (
        <div style={{ backgroundColor: '#7f1d1d22', border: `1px solid ${C.red}44`, borderRadius: '8px', padding: '10px 16px', marginBottom: '14px', color: '#fca5a5', fontSize: '0.88rem' }}>
          ⚡ <strong>Circuit Breaker:</strong> {cb.reason}
        </div>
      )}

      {/* ── Row 1: Stats ── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px', marginBottom: '12px' }}>

        {/* Portfolio */}
        <div style={card}>
          <div style={cardTitle}>Portfolio</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <Stat label="Total Value" value={fmt$(pf?.total_value)} />
            <Stat label="Total P&L" value={`${pf?.total_profit >= 0 ? '+' : ''}${fmt$(pf?.total_profit)}`} color={clr(pf?.total_profit)} />
            <Stat label="Cash" value={fmt$(pf?.cash)} />
            <Stat label="BTC" value={`${pf?.holdings?.['BTC/USD']?.toFixed(6)}`} />
            <Stat label="Unrealized" value={`${pf?.unrealized_pnl >= 0 ? '+' : ''}${fmt$(pf?.unrealized_pnl)}`} color={clr(pf?.unrealized_pnl)} />
            <Stat label="Realized" value={`${pf?.realized_pnl >= 0 ? '+' : ''}${fmt$(pf?.realized_pnl)}`} color={clr(pf?.realized_pnl)} />
            <Stat label="Fees Paid" value={fmt$(pf?.total_fees, 4)} color={C.orange} />
            <Stat label="Cost Basis" value={pf?.cost_basis?.['BTC/USD'] > 0 ? fmt$(pf.cost_basis['BTC/USD'], 0) : '—'} />
          </div>
        </div>

        {/* Grid State */}
        <div style={card}>
          <div style={cardTitle}>Grid State</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <Stat label="Status" value={gs?.initialized ? '✅ Active' : '⏳ Init'} color={gs?.initialized ? C.green : C.yellow} />
            <Stat label="Open Positions" value={`${gs?.open_positions} / ${settings?.max_open_positions}`} color={gs?.open_positions > 0 ? C.blue : C.muted} />
            <Stat label="Range Low" value={gs?.range_low > 0 ? fmt$(gs.range_low, 0) : '—'} />
            <Stat label="Range High" value={gs?.range_high > 0 ? fmt$(gs.range_high, 0) : '—'} />
            <Stat label="Levels" value={gs?.levels_count || settings?.grid_levels} />
            <Stat label="Index" value={gs?.current_index >= 0 ? gs.current_index : '—'} />
          </div>
          {gs?.range_low > 0 && gs?.range_high > 0 && (
            <div style={{ marginTop: '14px' }}>
              <div style={{ height: '5px', backgroundColor: C.subtle, borderRadius: '3px', overflow: 'hidden' }}>
                <div style={{
                  height: '100%',
                  width: `${Math.max(0, Math.min(100, (btcPrice - gs.range_low) / (gs.range_high - gs.range_low) * 100))}%`,
                  backgroundColor: C.blue, borderRadius: '3px', transition: 'width 0.3s',
                }} />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.68rem', color: C.muted, marginTop: '4px' }}>
                <span>{fmt$(gs.range_low, 0)}</span>
                <span style={{ color: C.text }}>{fmt$(btcPrice, 0)}</span>
                <span>{fmt$(gs.range_high, 0)}</span>
              </div>
            </div>
          )}
        </div>

        {/* Trade Stats */}
        <div style={card}>
          <div style={cardTitle}>Trade Stats</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <Stat label="Win Rate" value={fmtPct(st?.win_rate)} color={st?.win_rate >= 50 ? C.green : C.red} />
            <Stat label="Total Trades" value={st?.total_trades} />
            <Stat label="Profit Factor" value={st?.profit_factor > 0 ? st.profit_factor?.toFixed(2) : '—'} color={st?.profit_factor >= 1 ? C.green : C.red} />
            <Stat label="Max Drawdown" value={fmtPct(st?.max_drawdown)} color={st?.max_drawdown > 10 ? C.red : C.text} />
            <Stat label="Largest Win" value={st?.largest_win > 0 ? `+${fmt$(st.largest_win, 3)}` : '—'} color={C.green} />
            <Stat label="Largest Loss" value={st?.largest_loss < 0 ? fmt$(st.largest_loss, 3) : '—'} color={C.red} />
            <Stat label="Avg Win" value={st?.avg_win > 0 ? `+${fmt$(st.avg_win, 3)}` : '—'} color={C.green} />
            <Stat label="Avg Loss" value={st?.avg_loss < 0 ? fmt$(st.avg_loss, 3) : '—'} color={C.red} />
          </div>
        </div>
      </div>

      {/* ── Row 2: Chart ── */}
      <div style={{ ...card, marginBottom: '12px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <div style={cardTitle}>Chart</div>
          <div style={{ display: 'flex', gap: '6px' }}>
            {['price', 'equity'].map(t => (
              <button key={t} onClick={() => setChartTab(t)} style={{
                padding: '4px 14px', borderRadius: '6px', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer', border: 'none',
                backgroundColor: chartTab === t ? C.blue : C.subtle,
                color: chartTab === t ? '#fff' : C.muted,
                transition: 'all 0.15s',
              }}>{t === 'price' ? 'BTC Price' : 'Equity'}</button>
            ))}
          </div>
        </div>

        {chartTab === 'price' && (
          priceData.length > 1 ? (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={priceData} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="priceGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={C.blue} stopOpacity={0.25} />
                    <stop offset="95%" stopColor={C.blue} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                <XAxis dataKey="time" tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={40} />
                <YAxis domain={['auto', 'auto']} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={v => `$${v.toLocaleString()}`} width={72} />
                <Tooltip content={<ChartTooltip />} />
                {gs?.range_high > 0 && <ReferenceLine y={gs.range_high} stroke={C.red} strokeDasharray="4 3" strokeOpacity={0.5} label={{ value: 'Grid High', fill: C.red, fontSize: 10, position: 'right' }} />}
                {gs?.range_low > 0 && <ReferenceLine y={gs.range_low} stroke={C.green} strokeDasharray="4 3" strokeOpacity={0.5} label={{ value: 'Grid Low', fill: C.green, fontSize: 10, position: 'right' }} />}
                <Area type="monotone" dataKey="price" stroke={C.blue} strokeWidth={2} fill="url(#priceGrad)" name="BTC Price" dot={<TradeDot />} activeDot={{ r: 4, fill: C.blue }} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <div style={{ height: '280px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted, fontSize: '0.88rem' }}>
              Warming up — price data incoming…
            </div>
          )
        )}

        {chartTab === 'equity' && (
          (equity_history || []).length > 1 ? (
            <>
              <ResponsiveContainer width="100%" height={200}>
                <AreaChart data={equity_history} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor={C.green} stopOpacity={0.25} />
                      <stop offset="95%" stopColor={C.green} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke={C.border} vertical={false} />
                  <XAxis dataKey="time" tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} axisLine={false} minTickGap={40} />
                  <YAxis domain={['auto', 'auto']} tick={{ fill: C.muted, fontSize: 10 }} tickLine={false} axisLine={false} tickFormatter={v => `$${v}`} width={56} />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine y={pf?.initial_balance} stroke={C.yellow} strokeDasharray="6 3" strokeOpacity={0.5} />
                  <Area type="monotone" dataKey="value" stroke={C.green} strokeWidth={2} fill="url(#eqGrad)" name="Equity $" dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
              <div style={{ marginTop: '12px' }}>
                <ResponsiveContainer width="100%" height={80}>
                  <BarChart data={equity_history} margin={{ top: 0, right: 8, left: 0, bottom: 0 }}>
                    <XAxis dataKey="time" hide />
                    <YAxis tick={{ fill: C.muted, fontSize: 9 }} tickLine={false} axisLine={false} tickFormatter={v => `$${v}`} width={48} />
                    <Tooltip content={<ChartTooltip />} />
                    <Bar dataKey="pnl" name="P&L $" fill={C.blue} radius={[2, 2, 0, 0]} isAnimationActive={false}
                      label={false}
                      cell={equity_history.map((e, i) => <cell key={i} fill={e.pnl >= 0 ? C.green : C.red} />)}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </>
          ) : (
            <div style={{ height: '280px', display: 'flex', alignItems: 'center', justifyContent: 'center', color: C.muted, fontSize: '0.88rem' }}>
              Equity history builds after the first candle closes…
            </div>
          )
        )}
      </div>

      {/* ── Row 3: Indicators ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '12px' }}>

        {/* Market Signals */}
        <div style={card}>
          <div style={cardTitle}>Market Signals</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
            <Stat label="RSI (14)" value={ind.rsi?.toFixed(1)} color={ind.rsi > 70 ? C.red : ind.rsi < 30 ? C.green : C.text} />
            <Stat label="Stoch RSI" value={ind.stoch_rsi?.toFixed(1)} color={ind.stoch_rsi > 80 ? C.red : ind.stoch_rsi < 20 ? C.green : C.text} />
            <Stat label="MACD" value={ind.macd_histogram > 0 ? '↗ Bullish' : '↘ Bearish'} color={ind.macd_histogram > 0 ? C.green : C.red} />
            <Stat label="MACD Hist" value={ind.macd_histogram?.toFixed(2)} color={ind.macd_histogram > 0 ? C.green : C.red} />
            <Stat label="ATR" value={ind.atr > 0 ? fmt$(ind.atr) : '—'} />
            <Stat label="Volatility" value={ind.volatility > 0 ? fmt$(ind.volatility) : '—'} />
            <Stat label="Vol. Ratio" value={ind.volume_ratio?.toFixed(2)} color={ind.volume_ratio < 0.6 ? C.red : ind.volume_ratio > 1.5 ? C.green : C.text} />
            <Stat label="BB Width" value={ind.bb_upper > 0 ? fmt$(ind.bb_upper - ind.bb_lower, 0) : '—'} />
          </div>
          <div style={{ marginTop: '14px', paddingTop: '12px', borderTop: `1px solid ${C.border}`, display: 'flex', gap: '16px', fontSize: '0.75rem' }}>
            <span style={{ color: C.muted }}>BB <span style={{ color: btcPrice >= ind.bb_upper ? C.red : C.subtle }}>{fmt$(ind.bb_upper, 0)}</span></span>
            <span style={{ color: C.muted }}>Mid <span style={{ color: C.text }}>{fmt$(ind.bb_mid, 0)}</span></span>
            <span style={{ color: C.muted }}>Low <span style={{ color: btcPrice <= ind.bb_lower ? C.green : C.subtle }}>{fmt$(ind.bb_lower, 0)}</span></span>
          </div>
        </div>

        {/* Trend Analysis */}
        <div style={card}>
          <div style={cardTitle}>Trend Analysis</div>
          <div style={{ marginBottom: '12px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
              <span style={statLabel}>1-Minute Trend</span>
              <TrendBadge trend={ind.trend} />
            </div>
            <StrengthBar value={ind.trend_strength} color={ind.trend === 'BULLISH' ? C.green : ind.trend === 'BEARISH' ? C.red : C.muted} />
            <div style={{ fontSize: '0.68rem', color: C.muted, marginTop: '3px' }}>{((ind.trend_strength || 0) * 100).toFixed(1)}% strength</div>
          </div>
          <div style={{ marginBottom: '14px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
              <span style={statLabel}>5-Minute Trend</span>
              <TrendBadge trend={ind.trend_5m} />
            </div>
            <StrengthBar value={ind.trend_strength_5m} color={ind.trend_5m === 'BULLISH' ? C.green : ind.trend_5m === 'BEARISH' ? C.red : C.muted} />
            <div style={{ fontSize: '0.68rem', color: C.muted, marginTop: '3px' }}>{((ind.trend_strength_5m || 0) * 100).toFixed(1)}% strength</div>
          </div>
          <div style={{ paddingTop: '12px', borderTop: `1px solid ${C.border}` }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
              <Stat label="MTF Agreement" value={ind.mtf_agreement ? '✅ Aligned' : '❌ Diverged'} color={ind.mtf_agreement ? C.green : C.yellow} />
              <Stat label="EMA Cross" value={ind.ema_fast > ind.ema_slow ? '↗ Fast > Slow' : '↘ Fast < Slow'} color={ind.ema_fast > ind.ema_slow ? C.green : C.red} />
              <Stat label="EMA Fast (9)" value={ind.ema_fast > 0 ? fmt$(ind.ema_fast, 0) : '—'} small />
              <Stat label="EMA Slow (21)" value={ind.ema_slow > 0 ? fmt$(ind.ema_slow, 0) : '—'} small />
            </div>
          </div>
        </div>
      </div>

      {/* ── Row 4: Settings ── */}
      <div style={{ ...card, marginBottom: '12px' }}>
        <div style={cardTitle}>Settings</div>
        <form onSubmit={saveSettings}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '12px', marginBottom: '16px' }}>
            {[
              { key: 'cash', label: 'Cash ($)', ph: pf?.cash?.toFixed(2) },
              { key: 'trade_size_usd', label: 'Trade Size ($)', ph: settings?.trade_size_usd },
              { key: 'grid_levels', label: 'Grid Levels', ph: settings?.grid_levels },
              { key: 'grid_lower', label: 'Grid Lower ($)', ph: settings?.grid_lower > 0 ? settings.grid_lower : 'auto' },
              { key: 'grid_upper', label: 'Grid Upper ($)', ph: settings?.grid_upper > 0 ? settings.grid_upper : 'auto' },
            ].map(({ key, label, ph }) => (
              <div key={key}>
                <label style={{ ...statLabel, display: 'block', marginBottom: '5px' }}>{label}</label>
                <input
                  type="number" step="any" placeholder={ph}
                  value={form[key] || ''}
                  onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                  style={inputStyle}
                />
              </div>
            ))}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '12px', marginBottom: '18px' }}>
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
          <button type="submit" style={{
            padding: '9px 24px', backgroundColor: C.blue, color: '#fff',
            border: 'none', borderRadius: '6px', cursor: 'pointer',
            fontWeight: 700, fontSize: '0.88rem', letterSpacing: '0.3px',
          }}>Save Settings</button>
        </form>
      </div>

      {/* ── Row 5: Logs ── */}
      <div style={card}>
        <div style={cardTitle}>Agent Logs</div>
        <div ref={logBoxRef} style={{
          backgroundColor: '#080b0f', borderRadius: '6px', padding: '10px 12px',
          height: '200px', overflowY: 'auto', fontFamily: 'monospace',
          fontSize: '0.82rem', border: `1px solid ${C.border}`,
        }}>
          {[...logs].map((log, i) => {
            let color = C.muted;
            if (log.includes('BUY')) color = C.green;
            else if (log.includes('SELL')) color = C.red;
            else if (log.includes('CIRCUIT BREAKER')) color = C.orange;
            else if (log.includes('Trailing TP')) color = C.yellow;
            else if (log.includes('rebalanc') || log.includes('Grid init') || log.includes('Warmup') || log.includes('WebSocket')) color = C.purple;
            else if (log.includes('Settings')) color = C.blue;
            return (
              <div key={i} style={{ padding: '3px 0', borderBottom: `1px solid ${C.border}`, color, wordBreak: 'break-all' }}>
                {log}
              </div>
            );
          })}
        </div>
      </div>

    </div>
  );
}
