import { useState, useEffect, useRef } from 'react';

export default function App() {
  const [botState, setBotState] = useState(null);
  const [connectionStatus, setConnectionStatus] = useState('Connecting...');
  
  // Local state for the settings form
  const [editCash, setEditCash] = useState('');
  const [editRisk, setEditRisk] = useState('');
  
  const wsRef = useRef(null);
  const logsEndRef = useRef(null);

  useEffect(() => {
    wsRef.current = new WebSocket('ws://localhost:8000/ws');
    
    wsRef.current.onopen = () => setConnectionStatus('Connected 🟢');
    
    wsRef.current.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        setBotState(data);
      } catch (err) {
        console.error("Error parsing websocket data", err);
      }
    };
    
    wsRef.current.onclose = () => setConnectionStatus('Disconnected 🔴');
    wsRef.current.onerror = () => setConnectionStatus('Error ⚠️');
    
    return () => {
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [botState?.logs]);

  // Handle saving new settings to the backend
  const handleSaveSettings = (e) => {
    e.preventDefault();
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;

    const payload = { type: 'UPDATE_SETTINGS' };
    
    if (editCash !== '') payload.cash = parseFloat(editCash);
    if (editRisk !== '') payload.risk_pct = parseFloat(editRisk) / 100; // Convert 30 to 0.30

    wsRef.current.send(JSON.stringify(payload));
    
    // Clear inputs after sending
    setEditCash('');
    setEditRisk('');
  };

  if (!botState) {
    return (
      <div style={styles.loadingContainer}>
        <h2>{connectionStatus}</h2>
        <p>Ensure your Python backend is running on port 8000.</p>
      </div>
    );
  }

  const { prices, portfolio, indicators, macro, logs, settings } = botState;
  const btcPrice = prices['BTC/USD'] || 0;
  const ind = indicators['BTC/USD'] || {};
  const btcMacro = macro['BTC/USD'] || {};
  const macdPositive = ind.macd > ind.macd_signal;

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <h1>Algo Trading Terminal</h1>
        <span style={styles.status}>{connectionStatus}</span>
      </header>

      <div style={styles.grid}>
        <div style={styles.leftCol}>
          
          <div style={styles.card}>
            <h3>BTC/USD Market</h3>
            <div style={styles.priceRow}>
              <span style={styles.bigPrice}>${btcPrice.toFixed(2)}</span>
            </div>
            <div style={styles.statsRow}>
              <div>
                <span style={styles.label}>RSI (14)</span>
                <span style={{...styles.val, color: ind.rsi > 55 ? 'red' : ind.rsi < 45 ? '#2ecc71' : 'white'}}>
                  {ind.rsi?.toFixed(1)}
                </span>
              </div>
              <div>
                <span style={styles.label}>Avg Volatility</span>
                <span style={styles.val}>${ind.volatility?.toFixed(2)} / tick</span>
              </div>
              <div>
                <span style={styles.label}>3-Mo Trend</span>
                <span style={{...styles.val, color: btcMacro.trend_pct >= 0 ? '#2ecc71' : 'red'}}>
                  {btcMacro.trend_pct ? `${btcMacro.trend_pct.toFixed(2)}%` : 'Loading...'}
                </span>
              </div>
            </div>
          </div>

          <div style={styles.card}>
            <h3>Technical Indicators</h3>
            <div style={styles.statsRow}>
              <div>
                <span style={styles.label}>BB Upper (Sell Zone)</span>
                <span style={{...styles.val, color: btcPrice >= ind.bb_upper ? 'red' : 'white'}}>
                  ${ind.bb_upper?.toFixed(2)}
                </span>
              </div>
              <div>
                <span style={styles.label}>BB Lower (Buy Zone)</span>
                <span style={{...styles.val, color: btcPrice <= ind.bb_lower ? '#2ecc71' : 'white'}}>
                  ${ind.bb_lower?.toFixed(2)}
                </span>
              </div>
            </div>
            
            <div style={{...styles.statsRow, marginTop: '20px', borderTop: '1px solid #333', paddingTop: '15px'}}>
              <div>
                <span style={styles.label}>MACD</span>
                <span style={{...styles.val, color: macdPositive ? '#2ecc71' : 'red'}}>
                  {ind.macd?.toFixed(2)}
                </span>
              </div>
              <div>
                <span style={styles.label}>MACD Signal</span>
                <span style={styles.val}>{ind.macd_signal?.toFixed(2)}</span>
              </div>
              <div>
                <span style={styles.label}>Momentum</span>
                <span style={{...styles.val, color: macdPositive ? '#2ecc71' : 'red'}}>
                  {macdPositive ? 'Bullish ↗' : 'Bearish ↘'}
                </span>
              </div>
            </div>
          </div>

          <div style={styles.card}>
            <h3>Portfolio Status</h3>
            <div style={styles.statsRow}>
              <div>
                <span style={styles.label}>Total Value</span>
                <span style={styles.val}>${portfolio.total_value.toFixed(2)}</span>
              </div>
              <div>
                <span style={styles.label}>Total Profit</span>
                <span style={{...styles.val, color: portfolio.total_profit >= 0 ? '#2ecc71' : 'red'}}>
                  ${portfolio.total_profit.toFixed(2)}
                </span>
              </div>
            </div>
            <div style={styles.statsRow}>
              <div>
                <span style={styles.label}>Cash Available</span>
                <span style={styles.val}>${portfolio.cash.toFixed(2)}</span>
              </div>
              <div>
                <span style={styles.label}>BTC Holdings</span>
                <span style={styles.val}>{portfolio.holdings['BTC/USD'].toFixed(6)} BTC</span>
              </div>
            </div>
          </div>

          {/* NEW: Bot Settings Panel */}
          <div style={styles.card}>
            <h3>Bot Settings</h3>
            <form onSubmit={handleSaveSettings} style={styles.formGrid}>
              <div style={styles.inputGroup}>
                <label style={styles.label}>Available Cash ($)</label>
                <input 
                  type="number" 
                  step="0.01"
                  placeholder={portfolio.cash.toFixed(2)}
                  value={editCash}
                  onChange={(e) => setEditCash(e.target.value)}
                  style={styles.input}
                />
              </div>
              <div style={styles.inputGroup}>
                <label style={styles.label}>Risk Per Trade (%)</label>
                <input 
                  type="number" 
                  step="1"
                  min="1"
                  max="100"
                  placeholder={(settings.risk_pct * 100).toFixed(0)}
                  value={editRisk}
                  onChange={(e) => setEditRisk(e.target.value)}
                  style={styles.input}
                />
              </div>
              <button type="submit" style={styles.saveButton}>Save Settings</button>
            </form>
          </div>

        </div>

        <div style={styles.rightCol}>
          <div style={{...styles.card, height: '100%', display: 'flex', flexDirection: 'column'}}>
            <h3>Agent Logs</h3>
            <div style={styles.logContainer}>
              {[...logs].reverse().map((log, index) => {
                let color = '#ccc';
                if (log.includes('BUY')) color = '#2ecc71';
                if (log.includes('SELL')) color = '#e74c3c';
                if (log.includes('HOLD')) color = '#f39c12';
                if (log.includes('Settings Updated')) color = '#3498db';

                return (
                  <div key={index} style={{...styles.logEntry, color}}>
                    {log}
                  </div>
                );
              })}
              <div ref={logsEndRef} />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const styles = {
  loadingContainer: { display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100vh', backgroundColor: '#121212', color: '#fff', fontFamily: 'sans-serif' },
  container: { minHeight: '100vh', backgroundColor: '#121212', color: '#e0e0e0', fontFamily: 'system-ui, -apple-system, sans-serif', padding: '20px', boxSizing: 'border-box' },
  header: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: '1px solid #333', paddingBottom: '15px', marginBottom: '20px' },
  status: { fontSize: '0.9rem', backgroundColor: '#222', padding: '5px 10px', borderRadius: '15px' },
  grid: { display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px', height: 'calc(100vh - 100px)' },
  leftCol: { display: 'flex', flexDirection: 'column', gap: '20px', overflowY: 'auto', paddingRight: '10px' },
  rightCol: { display: 'flex', flexDirection: 'column', height: '100%' },
  card: { backgroundColor: '#1e1e1e', borderRadius: '8px', padding: '20px', boxShadow: '0 4px 6px rgba(0,0,0,0.3)', border: '1px solid #2a2a2a' },
  priceRow: { margin: '15px 0' },
  bigPrice: { fontSize: '2.5rem', fontWeight: 'bold', color: '#fff' },
  statsRow: { display: 'flex', justifyContent: 'space-between', marginTop: '10px' },
  label: { display: 'block', fontSize: '0.8rem', color: '#888', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '4px' },
  val: { fontSize: '1.2rem', fontWeight: '500' },
  logContainer: { flex: 1, overflowY: 'auto', backgroundColor: '#121212', padding: '10px', borderRadius: '5px', marginTop: '10px', fontFamily: 'monospace', border: '1px solid #333' },
  logEntry: { padding: '5px 0', borderBottom: '1px solid #222', fontSize: '0.9rem', wordBreak: 'break-all' },
  formGrid: { display: 'flex', gap: '15px', alignItems: 'flex-end', marginTop: '15px' },
  inputGroup: { flex: 1 },
  input: { width: '100%', padding: '8px', borderRadius: '4px', border: '1px solid #444', backgroundColor: '#2a2a2a', color: '#fff', boxSizing: 'border-box' },
  saveButton: { padding: '9px 15px', backgroundColor: '#3498db', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer', fontWeight: 'bold' }
};