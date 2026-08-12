const COLORS = { SP500: '#6ab8ff', GOLD: '#e8b95d', LONG_TREASURY: '#5ed1bf', CASH: '#a990ff' };
const METHOD_COLORS = ['#6ab8ff', '#e8b95d', '#5ed1bf', '#a990ff', '#ee7e7e', '#86d995'];
const ASSETS = ['SP500', 'GOLD', 'LONG_TREASURY', 'CASH'];
const ASSET_LABELS = { SP500: 'S&P 500', GOLD: 'Gold', LONG_TREASURY: 'Long Treasury', CASH: 'Cash / T-Bills' };
const METHOD_LABELS = { equal_weight: 'Equal Weight', max_sharpe: 'Max Sharpe', min_volatility: 'Min Volatility', max_sortino: 'Max Sortino', max_calmar: 'Max Calmar', robust_quant: 'Robust Quant' };
let result = null;
const $ = (id) => document.getElementById(id);
const pct = (v, digits = 1) => v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : `${(Number(v) * 100).toFixed(digits)}%`;
const num = (v, digits = 2) => v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : Number(v).toFixed(digits);
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
const pathPoints = (points, width, height, pad, min = null, max = null) => {
  const clean = (points || []).filter(p => p.value !== null && Number.isFinite(Number(p.value)));
  if (!clean.length) return { path: '', points: [], min: 0, max: 1 };
  const lo = min ?? Math.min(...clean.map(p => Number(p.value))), hi = max ?? Math.max(...clean.map(p => Number(p.value)));
  const range = Math.max(1e-9, hi - lo);
  return { path: clean.map((p, i) => `${i ? 'L' : 'M'}${(pad + i * (width - 2 * pad) / Math.max(1, clean.length - 1)).toFixed(1)},${(height - pad - (Number(p.value) - lo) / range * (height - 2 * pad)).toFixed(1)}`).join(' '), points: clean, min: lo, max: hi };
};
const chartSvg = (series, opts = {}) => {
  const width = opts.width || 700, height = opts.height || 260, pad = 28;
  const vals = series.flatMap(s => (s.points || []).map(p => Number(p.value))).filter(Number.isFinite);
  const min = opts.min ?? (vals.length ? Math.min(...vals) : 0), max = opts.max ?? (vals.length ? Math.max(...vals) : 1), range = Math.max(1e-9, max - min);
  const grid = [0, .25, .5, .75, 1].map(t => { const y = height-pad-t*(height-2*pad); const value = max-t*range; return `<line x1="${pad}" x2="${width-pad}" y1="${y}" y2="${y}" stroke="rgba(194,211,227,.1)"/><text x="${pad-6}" y="${y+3}" text-anchor="end" fill="#718092" font-size="9">${opts.percent ? pct(value,0) : num(value,1)}</text>`; }).join('');
  const paths = series.map((s, i) => { const p = pathPoints(s.points, width, height, pad, min, max); return `<path d="${p.path}" fill="none" stroke="${s.color || METHOD_COLORS[i]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`; }).join('');
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" role="img"><g>${grid}</g>${paths}</svg>`;
};
function renderDonut(weights, score) {
  const svg = $('donut'), r = 79, cx = 110, cy = 110, circumference = 2 * Math.PI * r; let offset = 0;
  svg.innerHTML = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="#1d2a38" stroke-width="25"/>` + ASSETS.map(a => { const dash = (weights[a] || 0) * circumference; const out = `<circle cx="${cx}" cy="${cy}" r="${r}" fill="none" stroke="${COLORS[a]}" stroke-width="25" stroke-dasharray="${dash} ${circumference-dash}" stroke-dashoffset="${-offset}"/>`; offset += dash; return out; }).join('');
  $('donutScore').textContent = score === null ? '—' : num(score, 2);
}
function renderAllocation(result) {
  const w = result.weights; renderDonut(w, result.robustness.score);
  $('allocationLegend').innerHTML = ASSETS.map(a => `<div class="legend-item"><span class="swatch" style="background:${COLORS[a]}"></span><span>${ASSET_LABELS[a]}</span><strong>${pct(w[a])}</strong></div>`).join('');
  $('selectedMethod').textContent = result.optimizer_label; $('selectedWindow').textContent = result.research_window; $('selectedDates').textContent = `${result.start_date} → ${result.end_date}`;
}
function renderMetrics(result) {
  const m = result.in_sample, o = result.out_of_sample;
  $('isCagr').textContent = pct(m.cagr); $('isVol').textContent = pct(m.volatility); $('isSharpe').textContent = num(m.sharpe); $('isSortino').textContent = num(m.sortino); $('isCalmar').textContent = num(m.calmar); $('isDrawdown').textContent = pct(m.max_drawdown);
  $('oosSharpe').textContent = num(o.sharpe); $('oosDrawdown').textContent = pct(o.max_drawdown); $('oosSample').textContent = `${o.sample_size || 0} mo`;
  $('dataCoverage').textContent = `${result.sample_size} observations · ${result.start_date} → ${result.end_date}`;
  const staticSnapshot = Boolean(result.deployment?.static_snapshot);
  ['optimizer', 'research_window', 'frequency', 'rebalance_frequency', 'transaction_cost_bps'].forEach(id => { $(id).disabled = staticSnapshot; });
  $('runButton').disabled = staticSnapshot;
  $('deploymentNote').textContent = staticSnapshot
    ? 'Cloudflare edge snapshot · controls are locked to the deployed research result · run the Python engine locally to generate a new snapshot.'
    : 'Default primary window: 30Y · Optimization is monthly and constrained to long-only, fully invested weights · Values update from cached research results.';
}
function renderWindowAllocation(result) {
  const rows = result.allocation_across_windows.filter(r => r.available); const labels = rows.map(r => r.window);
  const width = 560, height = 220, left = 65, top = 25, rowH = 32, barW = width - left - 20;
  const svg = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${rows.map((r, i) => { let x=left; return `<text x="${left-10}" y="${top+i*rowH+16}" text-anchor="end" fill="#9aa7b5" font-size="10">${r.window}</text>` + ASSETS.map(a => { const w=r.weights[a]*barW; const out=`<rect x="${x}" y="${top+i*rowH+3}" width="${w}" height="22" fill="${COLORS[a]}" rx="2"/>`; x+=w; return out; }).join('') + `<text x="${left+barW+7}" y="${top+i*rowH+17}" fill="#9aa7b5" font-size="10">100%</text>`; }).join('')}</svg>`;
  $('windowAllocation').innerHTML = rows.length ? svg : `<div class="muted">Requested history is not available in the common four-asset panel.</div>`;
  const s = result.stability.summary; $('stabilityLabel').textContent = `${s.label || 'Unavailable'} stability`; $('stabilityFoot').innerHTML = `<span>Score ${s.score == null ? '—' : num(s.score)}</span><span>Weight dispersion is shown, not hidden</span>`;
}
function renderRolling(result) {
  const rows = result.rolling_allocations.filter(r => r.window === '30Y');
  if (!rows.length) { $('rollingAllocation').innerHTML = '<div class="muted">Not enough common history for a 30Y rolling allocation.</div>'; return; }
  const series = ASSETS.map(a => ({ points: rows.map(r => ({ date:r.date, value:r[a] })), color:COLORS[a] }));
  // Stacked area is rendered with normalized step polygons for true composition.
  const width=760,height=300,pad=28, xs=rows.map((_,i)=>pad+i*(width-2*pad)/Math.max(1,rows.length-1)); let cumulative = rows.map(()=>0); let polys='';
  ASSETS.forEach(a => { const top = rows.map((r,i)=>cumulative[i]+r[a]); const polygon = xs.map((x,i)=>`${x},${height-pad-top[i]*(height-2*pad)}`).join(' ')+' '+xs.slice().reverse().map((x,i)=>`${x},${height-pad-cumulative[rows.length-1-i]*(height-2*pad)}`).join(' '); polys += `<polygon points="${polygon}" fill="${COLORS[a]}" fill-opacity=".78"/>`; cumulative = top; });
  const labels = [0,.25,.5,.75,1].map(t=>`<line x1="${pad}" x2="${width-pad}" y1="${height-pad-t*(height-2*pad)}" y2="${height-pad-t*(height-2*pad)}" stroke="rgba(194,211,227,.1)"/><text x="${pad-6}" y="${height-pad-t*(height-2*pad)+3}" text-anchor="end" fill="#718092" font-size="9">${Math.round(t*100)}%</text>`).join('');
  $('rollingAllocation').innerHTML = `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${labels}${polys}</svg>`; $('rollingLegend').innerHTML = ASSETS.map(a=>`<span><i class="swatch" style="background:${COLORS[a]}"></i>${ASSET_LABELS[a]}</span>`).join('');
}
function renderCurves(result) {
  const methods = Object.keys(result.equity_curves); const series = methods.map((m,i)=>({points:result.equity_curves[m],color:METHOD_COLORS[i]})); $('equityCurves').innerHTML = chartSvg(series, {percent:false}); $('equityLegend').innerHTML = methods.map((m,i)=>`<span><i class="swatch" style="background:${METHOD_COLORS[i]}"></i>${METHOD_LABELS[m]}</span>`).join('');
  const ddSeries = methods.map((m,i)=>({points:result.drawdown_curves[m],color:METHOD_COLORS[i]})); $('drawdownCurves').innerHTML = chartSvg(ddSeries, {percent:true, min:-1, max:0});
  const sharpeSeries = methods.map((m,i)=>({points:result.rolling_sharpe[m],color:METHOD_COLORS[i]})); $('rollingSharpe').innerHTML = chartSvg(sharpeSeries, {min:-2, max:3});
}
function renderHeatmap(result) {
  const matrix = result.coupling.latest_correlation, labels = matrix.labels; const cells = ['<div></div>', ...labels.map(l=>`<div class="heat-label">${l === 'LONG_TREASURY' ? 'BOND' : l}</div>`)];
  labels.forEach((row,i)=>{ cells.push(`<div class="heat-label">${row === 'LONG_TREASURY' ? 'BOND' : row}</div>`); labels.forEach((_,j)=>{ const v=matrix.values[i][j] ?? 0; const alpha=Math.min(1,Math.abs(v)); const bg=v>=0?`rgba(94,209,191,${.15+.6*alpha})`:`rgba(238,126,126,${.15+.6*alpha})`; cells.push(`<div class="heat-cell" style="background:${bg}">${num(v,2)}</div>`); }); }); $('correlationHeatmap').innerHTML=cells.join('');
}
function renderPca(result) { const p=result.coupling.pca, max=Math.max(...p.variance_explained); $('pcaVariance').innerHTML = `<div style="display:grid;gap:11px;margin-top:24px">${p.components.map((c,i)=>`<div class="robust-row"><span>${c}</span><div class="bar-bg"><div class="bar-fill" style="width:${p.variance_explained[i]/max*100}%"></div></div><b>${pct(p.variance_explained[i])}</b></div>`).join('')}</div>`; const d=p.minimum_variance_eigen_direction; $('pcaFoot').textContent = `Minimum-variance eigen-direction: ${ASSETS.map(a=>`${a} ${pct(d[a])}`).join(' · ')}`; }
function renderRegimes(result) { const rows=result.regimes.performance || []; const methods=['equal_weight','max_sharpe','max_sortino','max_calmar','robust_quant']; $('regimePerformance').innerHTML = rows.length ? `<table><thead><tr><th>Regime</th>${methods.map(m=>`<th>${METHOD_LABELS[m]}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.regime)} <span class="muted">(${r.observations})</span></td>${methods.map(m=>`<td class="${(r[m]?.cagr||0)>=0?'positive':'negative'}">${pct(r[m]?.cagr)}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '<div class="muted">No labeled regime observations in selected history.</div>'; }
function renderValidation(result) { const m=result.in_sample,o=result.out_of_sample; const vals=[m.sharpe||0,o.sharpe||0,m.sortino||0,o.sortino||0]; $('isOos').innerHTML = `<div style="display:grid;gap:12px;margin-top:24px">${[['Sharpe',m.sharpe,o.sharpe],['Sortino',m.sortino,o.sortino],['Calmar',m.calmar,o.calmar],['CAGR',m.cagr,o.cagr]].map(([name,is,oos])=>`<div class="robust-row"><span>${name} <i style="color:var(--blue)">IS</i> / <i style="color:var(--gold)">OOS</i></span><div class="bar-bg" style="display:flex;background:linear-gradient(90deg,rgba(106,184,255,.8) ${Math.min(100,Math.max(0,(is||0)*25+25))}%, rgba(232,185,93,.8) ${Math.min(100,Math.max(0,(oos||0)*25+25))}%);height:10px"></div><b>${num(oos)}</b></div>`).join('')}</div><div class="validation-note">IS CAGR ${pct(m.cagr)} · OOS CAGR ${pct(o.cagr)} · OOS CVaR ${pct(o.cvar95)}</div>`; }
function renderTransaction(result) { const rows=result.transaction_sensitivity; $('transactionTable').innerHTML=`<table><thead><tr><th>Cost</th><th>Gross CAGR</th><th>Net CAGR</th><th>Gross Sharpe</th><th>Net Sharpe</th><th>Turnover</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${r.transaction_cost_bps} bps</td><td>${pct(r.gross_cagr)}</td><td class="${r.net_cagr>=0?'positive':'negative'}">${pct(r.net_cagr)}</td><td>${num(r.gross_sharpe)}</td><td>${num(r.net_sharpe)}</td><td>${pct(r.turnover)}</td></tr>`).join('')}</tbody></table>`; }
function renderCrisis(result) { const rows=result.crisis, methods=['equal_weight','max_sharpe','max_sortino','max_calmar','robust_quant']; $('crisisTable').innerHTML=`<table><thead><tr><th>Stress period</th><th>SPX</th><th>Gold</th><th>Bond</th><th>Robust</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(r.period)}</td><td class="${r.SP500>=0?'positive':'negative'}">${pct(r.SP500)}</td><td class="${r.GOLD>=0?'positive':'negative'}">${pct(r.GOLD)}</td><td class="${r.LONG_TREASURY>=0?'positive':'negative'}">${pct(r.LONG_TREASURY)}</td><td class="${r.robust_quant?.return>=0?'positive':'negative'}">${pct(r.robust_quant?.return)}</td></tr>`).join('')}</tbody></table>`; }
function renderRobustness(result) { const r=result.robustness; $('robustnessScore').textContent=num(r.score); $('robustnessTable').innerHTML=Object.entries(r.components).map(([k,v])=>`<div class="robust-row"><span>${k.replaceAll('_',' ')}</span><div class="bar-bg"><div class="bar-fill" style="width:${Math.max(0,Math.min(100,v*100))}%"></div></div><b>${num(v)}</b></div>`).join(''); $('robustnessFormula').textContent=r.formula; }
function renderProvenance(result) { const a=result.provenance.assets; $('provenance').innerHTML=ASSETS.map(asset=>{const x=a[asset]; return `<div class="provenance-row"><span>${ASSET_LABELS[asset]}</span><span>${esc(x.instrument)} · ${esc(x.source)}<br>${esc(x.return_type)} · ${esc(x.start_date)} → ${esc(x.end_date)}<br>${esc(x.proxy_status)} · ${esc(x.notes)}</span></div>`}).join(''); }
function renderAll() { $('dashboard').classList.remove('hidden'); $('errorState').classList.add('hidden'); renderAllocation(result); renderMetrics(result); renderWindowAllocation(result); renderRolling(result); renderCurves(result); renderHeatmap(result); renderPca(result); renderRegimes(result); renderValidation(result); renderTransaction(result); renderCrisis(result); renderRobustness(result); renderProvenance(result); }
async function loadResearch() { const button=$('runButton'); button.disabled=true; button.textContent='Running…'; $('statusText').textContent='Calculating'; const params=new URLSearchParams(); ['optimizer','research_window','frequency','rebalance_frequency','transaction_cost_bps'].forEach(id=>params.set(id,$(id).value)); try { const response=await fetch(`/api/research?${params}`); const body=await response.json(); if(!response.ok) throw new Error(body.error || 'Research request failed'); result=body; renderAll(); $('statusText').textContent='Research ready'; } catch(error) { $('errorState').classList.remove('hidden'); $('errorState').textContent=`${error.name}: ${error.message}`; $('statusText').textContent='Unavailable'; } finally { button.disabled=false; button.innerHTML='Run research <span>↗</span>'; } }
$('runButton').addEventListener('click', loadResearch); window.addEventListener('DOMContentLoaded', loadResearch);
