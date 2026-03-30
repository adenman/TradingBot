import { useState, useEffect, useRef } from 'react';

const S = {
  root: { minHeight: '100vh', backgroundColor: '#0d0d0d', color: '#e0e0e0', fontFamily: 'system-ui, -apple-system, sans-serif', padding: '16px', boxSizing: 'border-box' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #2a2a2a', paddingBottom: '12px', marginBottom: '16px', flexWrap: 'wrap', gap: '8px' },
  headerLeft: { display: 'flex', alignItems: 'baseline', gap: '16px' },
  title: { fontSize: '0.85rem', color: '#555', textTransform: 'uppercase', letterSpacing: '1px', margin: 0 },
  bigPrice: { fontSize: '2.2rem', fontWeight: 'bold', color: '#fff', margin: 0 },
  badge: (color) => ({ fontSize: '0.75rem', padding: '3px 10px', borderRadius: '12px', backgroundColor: color + '22', color: color, border: `1px solid ${color}44` }),
  cbBanner: { backgroundColor: '#7f1d1d', border: '1px solid #ef4444', borderRadius: '6px', padding: '8px 14px', marginBottom: '12px', color: '#fca5a5', fontSize: '0.9rem', fontWeight: 600 },
  row: { display: 'grid', gap: '12px', marginBottom: '12px' },
  row3: { gridTemplateColumns: 'repeat(3, 1fr)' },
  row2: { gridTemplateColumns: 'repeat(2, 1fr)' },
  row1: { gridTemplateColumns: '1fr' },
  card: { backgroundColor: '#161616', borderRadius: '8px', padding: '16px', border: '1px solid #232323' },
  cardTitle: { fontSize: '0.75rem', color: '#555', textTransform: 'uppercase', letterSpacing: '1px', marginBottom: '12px', margin: '0 0 12px 0' },
  statGrid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' },
  statItem: { display: 'flex', flexDirection: 'column', gap: '2px' },
  statLabel: { fontSize: '0.7rem', color: '#555', textTransform: 'uppercase', letterSpacing: '0.5px' },
  statVal: (color) => ({ fontSize: '1rem', fontWeight: 600, color: color || '#e0e0e0' }),
  divider: { borderTop: '1px solid #232323', margin: '12px 0' },
  trendBadge: (trend) => ({
    display: 'inline-block', padding: '2px 10px', borderRadius: '4px', fontSize: '0.85rem', fontWeight: 700,
    backgroundColor: trend === 'BULLISH' ? '#14532d' : trend === 'BEARISH' ? '#7f1d1d' : '#1c1c1c',
    color: trend === 'BULLISH' ? '#4ade80' : trend === 'BEARISH' ? '#f87171' : '#888',
  }),
  strengthBar: (pct, color) => ({
    height: '4px', borderRadius: '2px', backgroundColor: '#222',
    position: 'relative', marginTop: '4px', overflow: 'hidden',
  }),
  strengthFill: (pct, color) => ({
    position: 'absolute', top: 0, left: 0, height: '100%',
    width: `${Math.min(pct * 100, 100)}%`,
    backgroundColor: color || '#3b82f6', borderRadius: '2px',
    transition: 'width 0.3s ease',
  }),
  input: { width: '100%', padding: '7px 10px', borderRadius: '4px', border: '1px solid #333', backgroundColor: '#1e1e1e', color: '#fff', boxSizing: 'border-box', fontSize: '0.9rem' },
  label: { fontSize: '0.72rem', color: '#666', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px', display: 'block' },
  saveBtn: { padding: '8px 20px', backgroundColor: '#2563eb', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontWeight: 700, fontSize: '0.9rem' },
  toggle: { display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' },
  toggleBox: (on) => ({ width: '32px', height: '18px', borderRadius: '9px', backgroundColor: on ? '#2563eb' : '#333', position: 'relative', transition: 'background 0.2s', flexShrink: 0 }),
  toggleKnob: (on) => ({ position: 'absolute', top: '2px', left: on ? '16px' : '2px', width: '14px', height: '14px', borderRadius: '50%', backgroundColor: '#fff', transition: 'left 0.2s' }),
  toggleLabel: { fontSize: '0.82rem', color: '#aaa' },
  logBox: { backgroundColor: '#0a0a0a', borderRadius: '6px', padding: '10px', height: '220px', overflowY: 'auto', fontFamily: 'monospace', fontSize: '0.82rem', border: '1px solid #1e1e1e' },
  logEntry: (color) => ({ padding: '3px 0', borderBottom: '1px solid #111', color: color || '#aaa', wordBreak: 'break-all' }),
};

function Stat({ label, value, color }) {
  return (
    <div style={S.statItem}>
      <span style={S.statLabel}>{label}</span>
      <span style={S.statVal(color)}>{value}</span>
    </div>
  );
}

function TrendRow({ label, trend, strength }) {
  const color = trend === 'BULLISH' ? '#4ade80' : trend === 'BEARISH' ? '#f87171' : '#888';
  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={S.statLabel}>{label}</span>
        <span style={S.trendBadge(trend)}>{trend}</span>
      </div>
      <div style={S.strengthBar(strength, color)}>
        <div style={S.strengthFill(strength, color)} />
      </div>
      <div style={{ fontSize: '0.7rem', color: '#444', marginTop: '2px' }}>{((strength || 0) * 100).toFixed(1)}% strength</div>
    </div>
  );
}

function Toggle({ label, value, onChange }) {
  return (
    <div style={S.toggle} onClick={() => onChange(!value)}>
      <div style={S.toggleBox(value)}>
        <div style={S.toggleKnob(value)} />
      </div>
      <span style={S.toggleLabel}>{label}</span>
    </div>
  );
}

function logColor(log) {
  if (log.includes('CIRCUIT BREAKER')) return '#fb923c';
  if (log.includes('BUY')) return '#4ade80';
  if (log.includes('SELL')) return '#f87171';
  if (log.includes('Trailing TP')) return '#facc15';
  if (log.includes('Settings')) return '#60a5fa';
  if (log.includes('rebalanc') || log.includes('Grid init')) return '#a78bfa';
  return '#666';
}

export default function App() {
  const [bot, setBot] = useState(null);
  const [connStatus, setConnStatus] = useState('Connecting...');
  const wsRef = useRef(null);

  // Settings form state
  const [form, setForm] = useState({});
  const [toggles, setToggles] = useState({});

  useEffect(() => {
    function connect() {
      const ws = new WebSocket('ws://localhost:8000/ws');
      wsRef.current = ws;
      ws.onopen = () => setConnStatus('Connected');
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);
          setBot(data);
          // Seed form defaults from settings on first load
          setToggles(prev => Object.keys(prev).length ? prev : {
            volume_filter: data.settings?.volume_filter ?? true,
            mtf_trend_filter: data.settings?.mtf_trend_filter ?? true,
            trend_filter: data.settings?.trend_filter ?? true,
            volatility_scaling: data.settings?.volatility_scaling ?? true,
            auto_range: data.settings?.auto_range ?? true,
          });
        } catch {}
      };
      ws.onclose = () => {
        setConnStatus('Disconnected');
        setTimeout(connect, 3000);
      };
      ws.onerror = () => setConnStatus('Error');
    }
    connect();
    return () => wsRef.current?.close();
  }, []);

  const sendSettings = (e) => {
    e.preventDefault();
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    const payload = { type: 'UPDATE_SETTINGS', ...toggles };
    if (form.cash) payload.cash = parseFloat(form.cash);
    if (form.trade_size_usd) payload.trade_size_usd = parseFloat(form.trade_size_usd);
    if (form.grid_levels) payload.grid_levels = parseInt(form.grid_levels);
    if (form.grid_upper) payload.grid_upper = parseFloat(form.grid_upper);
    if (form.grid_lower) payload.grid_lower = parseFloat(form.grid_lower);
    wsRef.current.send(JSON.stringify(payload));
    setForm({});
  };

  if (!bot) {
    return (
      <div style={{ ...S.root, display: 'flex', alignItems: 'center', justifyContent: 'center', flexDirection: 'column' }}>
        <p style={{ color: '#555' }}>{connStatus}</p>
        <p style={{ color: '#333', fontSize: '0.85rem' }}>Waiting for backend on port 8000…</p>
      </div>
    );
  }

  const { prices, portfolio, indicators, macro, logs, settings, stats, grid_state, circuit_breaker } = bot;
  const btcPrice = prices?.['BTC/USD'] || 0;
  const ind = indicators?.['BTC/USD'] || {};
  const btcMacro = macro?.['BTC/USD'] || {};
  const cb = circuit_breaker || {};
  const gs = grid_state || {};
  const st = stats || {};
  const pf = portfolio || {};

  const connColor = connStatus === 'Connected' ? '#4ade80' : connStatus === 'Disconnected' ? '#f87171' : '#fb923c';

  return (
    <div style={S.root}>
      {/* Header */}
      <header style={S.header}>
        <div style={S.headerLeft}>
          <p style={S.title}>Algo Terminal</p>
          <p style={S.bigPrice}>${btcPrice.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</p>
          {btcMacro.trend_pct != null && (
            <span style={S.badge(btcMacro.trend_pct >= 0 ? '#4ade80' : '#f87171')}>
              90d {btcMacro.trend_pct >= 0 ? '+' : ''}{btcMacro.trend_pct?.toFixed(1)}%
            </span>
          )}
        </div>
        <span style={S.badge(connColor)}>{connStatus}</span>
      </header>

      {/* Circuit Breaker Banner */}
      {cb.active && (
        <div style={S.cbBanner}>⚡ CIRCUIT BREAKER ACTIVE — {cb.reason}</div>
      )}

      {/* Row 1: Portfolio / Grid State / Trade Stats */}
      <div style={{ ...S.row, ...S.row3 }}>
        <div style={S.card}>
          <p style={S.cardTitle}>Portfolio</p>
          <div style={S.statGrid}>
            <Stat label="Total Value" value={`$${pf.total_value?.toFixed(2)}`} />
            <Stat label="Total P&L" value={`${pf.total_profit >= 0 ? '+' : ''}$${pf.total_profit?.toFixed(2)}`} color={pf.total_profit >= 0 ? '#4ade80' : '#f87171'} />
            <Stat label="Cash" value={`$${pf.cash?.toFixed(2)}`} />
            <Stat label="BTC Holdings" value={`${pf.holdings?.['BTC/USD']?.toFixed(6)} BTC`} />
            <Stat label="Unrealized P&L" value={`${pf.unrealized_pnl >= 0 ? '+' : ''}$${pf.unrealized_pnl?.toFixed(2)}`} color={pf.unrealized_pnl >= 0 ? '#4ade80' : '#f87171'} />
            <Stat label="Realized P&L" value={`${pf.realized_pnl >= 0 ? '+' : ''}$${pf.realized_pnl?.toFixed(2)}`} color={pf.realized_pnl >= 0 ? '#4ade80' : '#f87171'} />
            <Stat label="Total Fees" value={`$${pf.total_fees?.toFixed(4)}`} color="#fb923c" />
            <Stat label="Cost Basis" value={pf.cost_basis?.['BTC/USD'] > 0 ? `$${pf.cost_basis?.['BTC/USD']?.toFixed(0)}` : '—'} />
          </div>
        </div>

        <div style={S.card}>
          <p style={S.cardTitle}>Grid State</p>
          <div style={S.statGrid}>
            <Stat label="Status" value={gs.initialized ? '✅ Active' : '⏳ Init'} color={gs.initialized ? '#4ade80' : '#fb923c'} />
            <Stat label="Open Positions" value={`${gs.open_positions} / ${settings?.max_open_positions}`} color={gs.open_positions > 0 ? '#60a5fa' : '#888'} />
            <Stat label="Range Low" value={gs.range_low > 0 ? `$${gs.range_low?.toFixed(0)}` : '—'} />
            <Stat label="Range High" value={gs.range_high > 0 ? `$${gs.range_high?.toFixed(0)}` : '—'} />
            <Stat label="Grid Levels" value={gs.levels_count || settings?.grid_levels || '—'} />
            <Stat label="Current Index" value={gs.current_index >= 0 ? gs.current_index : '—'} />
          </div>
          {gs.range_low > 0 && gs.range_high > 0 && (
            <div style={{ marginTop: '12px' }}>
              <div style={{ ...S.strengthBar(), height: '6px' }}>
                <div style={{
                  ...S.strengthFill(
                    (btcPrice - gs.range_low) / (gs.range_high - gs.range_low),
                    btcPrice > (gs.range_low + gs.range_high) / 2 ? '#4ade80' : '#60a5fa'
                  )
                }} />
              </div>
              <div style={{ fontSize: '0.68rem', color: '#444', marginTop: '3px', display: 'flex', justifyContent: 'space-between' }}>
                <span>${gs.range_low?.toFixed(0)}</span><span>position in range</span><span>${gs.range_high?.toFixed(0)}</span>
              </div>
            </div>
          )}
        </div>

        <div style={S.card}>
          <p style={S.cardTitle}>Trade Stats</p>
          <div style={S.statGrid}>
            <Stat label="Win Rate" value={`${st.win_rate?.toFixed(1)}%`} color={st.win_rate >= 50 ? '#4ade80' : '#f87171'} />
            <Stat label="Total Trades" value={st.total_trades} />
            <Stat label="Profit Factor" value={st.profit_factor > 0 ? st.profit_factor?.toFixed(2) : '—'} color={st.profit_factor >= 1 ? '#4ade80' : '#f87171'} />
            <Stat label="Max Drawdown" value={`${st.max_drawdown?.toFixed(1)}%`} color={st.max_drawdown > 10 ? '#f87171' : '#e0e0e0'} />
            <Stat label="Largest Win" value={st.largest_win > 0 ? `+$${st.largest_win?.toFixed(3)}` : '—'} color="#4ade80" />
            <Stat label="Largest Loss" value={st.largest_loss < 0 ? `$${st.largest_loss?.toFixed(3)}` : '—'} color="#f87171" />
            <Stat label="Avg Win" value={st.avg_win > 0 ? `+$${st.avg_win?.toFixed(3)}` : '—'} color="#4ade80" />
            <Stat label="Avg Loss" value={st.avg_loss < 0 ? `$${st.avg_loss?.toFixed(3)}` : '—'} color="#f87171" />
          </div>
        </div>
      </div>

      {/* Row 2: Market Signals / Trend Analysis */}
      <div style={{ ...S.row, ...S.row2 }}>
        <div style={S.card}>
          <p style={S.cardTitle}>Market Signals</p>
          <div style={S.statGrid}>
            <Stat label="RSI (14)" value={ind.rsi?.toFixed(1)} color={ind.rsi > 70 ? '#f87171' : ind.rsi < 30 ? '#4ade80' : '#e0e0e0'} />
            <Stat label="Stoch RSI" value={ind.stoch_rsi?.toFixed(1)} color={ind.stoch_rsi > 80 ? '#f87171' : ind.stoch_rsi < 20 ? '#4ade80' : '#e0e0e0'} />
            <Stat label="MACD" value={ind.macd_histogram > 0 ? '↗ Bullish' : '↘ Bearish'} color={ind.macd_histogram > 0 ? '#4ade80' : '#f87171'} />
            <Stat label="MACD Hist" value={ind.macd_histogram?.toFixed(2)} color={ind.macd_histogram > 0 ? '#4ade80' : '#f87171'} />
            <Stat label="ATR" value={ind.atr > 0 ? `$${ind.atr?.toFixed(2)}` : '—'} />
            <Stat label="Volatility" value={ind.volatility > 0 ? `$${ind.volatility?.toFixed(2)}` : '—'} />
            <Stat label="Volume Ratio" value={ind.volume_ratio?.toFixed(2) ?? '—'} color={ind.volume_ratio < 0.6 ? '#f87171' : ind.volume_ratio > 1.5 ? '#4ade80' : '#e0e0e0'} />
            <Stat label="BB Width" value={ind.bb_upper > 0 ? `$${(ind.bb_upper - ind.bb_lower)?.toFixed(0)}` : '—'} />
          </div>
          <div style={S.divider} />
          <div style={{ display: 'flex', gap: '12px', fontSize: '0.78rem', color: '#555' }}>
            <span>BB Upper: <span style={{ color: btcPrice >= ind.bb_upper ? '#f87171' : '#666' }}>${ind.bb_upper?.toFixed(0)}</span></span>
            <span>Mid: ${ind.bb_mid?.toFixed(0)}</span>
            <span>Lower: <span style={{ color: btcPrice <= ind.bb_lower ? '#4ade80' : '#666' }}>${ind.bb_lower?.toFixed(0)}</span></span>
          </div>
        </div>

        <div style={S.card}>
          <p style={S.cardTitle}>Trend Analysis</p>
          <TrendRow label="1-Minute Trend" trend={ind.trend} strength={ind.trend_strength} />
          <TrendRow label="5-Minute Trend" trend={ind.trend_5m} strength={ind.trend_strength_5m} />
          <div style={S.divider} />
          <div style={S.statGrid}>
            <Stat
              label="MTF Agreement"
              value={ind.mtf_agreement ? '✅ Aligned' : '❌ Diverged'}
              color={ind.mtf_agreement ? '#4ade80' : '#fb923c'}
            />
            <Stat label="EMA Fast (9)" value={ind.ema_fast > 0 ? `$${ind.ema_fast?.toFixed(0)}` : '—'} />
            <Stat label="EMA Slow (21)" value={ind.ema_slow > 0 ? `$${ind.ema_slow?.toFixed(0)}` : '—'} />
            <Stat
              label="EMA Cross"
              value={ind.ema_fast > ind.ema_slow ? '↗ Fast > Slow' : '↘ Fast < Slow'}
              color={ind.ema_fast > ind.ema_slow ? '#4ade80' : '#f87171'}
            />
          </div>
        </div>
      </div>

      {/* Row 3: Settings */}
      <div style={{ ...S.row, ...S.row1 }}>
        <div style={S.card}>
          <p style={S.cardTitle}>Settings</p>
          <form onSubmit={sendSettings}>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '12px', marginBottom: '16px' }}>
              {[
                { key: 'cash', label: 'Cash ($)', placeholder: pf.cash?.toFixed(2) },
                { key: 'trade_size_usd', label: 'Trade Size ($)', placeholder: settings?.trade_size_usd },
                { key: 'grid_levels', label: 'Grid Levels', placeholder: settings?.grid_levels },
                { key: 'grid_lower', label: 'Grid Lower ($)', placeholder: settings?.grid_lower > 0 ? settings.grid_lower : 'auto' },
                { key: 'grid_upper', label: 'Grid Upper ($)', placeholder: settings?.grid_upper > 0 ? settings.grid_upper : 'auto' },
              ].map(({ key, label, placeholder }) => (
                <div key={key}>
                  <label style={S.label}>{label}</label>
                  <input
                    type="number" step="any"
                    placeholder={placeholder}
                    value={form[key] || ''}
                    onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
                    style={S.input}
                  />
                </div>
              ))}
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '12px', marginBottom: '16px' }}>
              {[
                { key: 'volume_filter', label: 'Volume Filter' },
                { key: 'mtf_trend_filter', label: 'MTF Filter' },
                { key: 'trend_filter', label: 'Trend Filter' },
                { key: 'volatility_scaling', label: 'Vol. Scaling' },
                { key: 'auto_range', label: 'Auto Range' },
              ].map(({ key, label }) => (
                <Toggle
                  key={key}
                  label={label}
                  value={toggles[key] ?? settings?.[key] ?? true}
                  onChange={v => setToggles(t => ({ ...t, [key]: v }))}
                />
              ))}
            </div>
            <button type="submit" style={S.saveBtn}>Save Settings</button>
          </form>
        </div>
      </div>

      {/* Row 4: Logs */}
      <div style={{ ...S.row, ...S.row1 }}>
        <div style={S.card}>
          <p style={S.cardTitle}>Agent Logs</p>
          <div style={S.logBox}>
            {[...logs].map((log, i) => (
              <div key={i} style={S.logEntry(logColor(log))}>{log}</div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
