# -*- coding: utf-8 -*-
"""
MISSAO 12 -- Painel Espelho (dashboard Streamlit) do BOB, SOMENTE LEITURA
pros dados de operacao + 1 UNICA acao de escrita controlada (o kill
switch, numa tabela nova e dedicada pra isso -- mission7_bot_control).
NAO importa nem chama run_one_epoch/run() nem qualquer parte do motor de
decisao -- este painel NUNCA decide nada de trading, so' EXIBE o que o
motor (rodando via _bob_cloud_live_step.py) ja decidiu, e permite pausar
NOVAS entradas via flag (ver docstring de _bob_cloud_live_step.py pra'
como a flag e' aplicada -- so' no sizing_fn, o motor de entrada/saida/
stop/disjuntor nunca e' tocado).
MISSAO 13 -- PORTATIL (roda local no PC OU no Streamlit Community Cloud,
mesmo arquivo, sem duplicar). Conecta SEMPRE no mesmo projeto Supabase
dedicado "bob-paper-live" (dutmxvqluuxfexbdfbxm) -- NUNCA outro host.
MISSAO 14 -- VISUAL/UX profissional: so' CAMADA DE APRESENTACAO (CSS,
layout em abas, graficos extras calculados a partir dos MESMOS dados ja
carregados pelas funcoes de leitura abaixo). Nenhuma query nova de
escrita, nenhuma tabela nova, nenhuma dependencia nova alem do que ja
estava em requirements.txt (plotly ja cobre donut/barra/area).
MISSAO 15 -- Estatisticas de trades (taxa de acerto, fator de lucro) lidas
de mission7_experiences (tabela ja existente, escrita pelo proprio motor
via mission7_run.insert_experiences/apply_resolved -- SOMENTE LEITURA
aqui, nenhuma escrita nova) + historico do monitor de edge por epoca
(mesmo jsonb de checkpoints ja usado na curva de capital). Enquanto nao
houver trades fechados/checkpoints suficientes na nuvem, o painel mostra
isso com clareza -- nunca inventa numero.
MISSAO 18 -- P&L NAO REALIZADO das posicoes abertas (aba Visao Geral).
Motivacao: com o Donchian sendo trend-following (so' sai no rompimento da
minima de 10d), posicoes ficam abertas por semanas enquanto a tendencia
segura -- e o painel so' mostrava trades FECHADOS (mission7_experiences,
Missao 15), que ainda estava vazio. Isso dava a impressao de "nada
mudando", quando na verdade as posicoes abertas estavam com ganho de
verdade (conferido manualmente: todas as 10 posicoes abertas em
2026-08-27 estavam no lucro, de +9% a +370%). A correcao NAO fecha nada
nem toca no motor -- so' compara entry_price (ja gravado no checkpoint)
com o preco mais recente (ja gravado em mission7_market_states) e mostra
a diferenca percentual. Deixa claro na tela que e' "nao realizado" (so'
vira ganho/perda de verdade quando a posicao fechar via saida Donchian/
stop ATR/disjuntor) -- nunca disfarça isso de trade fechado.
COMO A CONFIGURACAO E' CARREGADA (2 caminhos, auto-detectados):
  1. LOCAL (no PC do laboratorio): le' .env.cloud e usa o guard COMPLETO
     ja existente (environment_guard.assert_cloud_paper_live_safe) --
     REUSO direto, nao duplicado, mesma validacao de sempre.

  2. NUVEM (Streamlit Community Cloud): environment_guard.py NUNCA e'
     publicado num repositorio (ele contem uma lista de hosts de
     PRODUCAO REAL proibidos que jamais deveria ir pro GitHub, nem
     privado) -- por isso, quando rodando la', a configuracao vem de
     st.secrets (colada manualmente no painel do Streamlit Cloud, nunca
     commitada) e passa por uma checagem MINIMA e AUTOCONTIDA aqui mesmo
     (so' confere host==projeto autorizado e DRY_RUN=true) -- fail closed
     igual ao guard original, so' que sem carregar o arquivo sensivel.
"""
import sys
import os
import time
import json
from contextlib import contextmanager
from datetime import datetime, timezone
import streamlit as st  # noqa: E402
import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402
import psycopg2.extensions  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
# MISSAO 17 -- correcao de raiz do TypeError na aba Performance & Risco
# (apareceu so' agora que a epoca 1079 fechou e um 2o checkpoint passou a
# existir na nuvem, habilitando pela 1a vez o calculo de drawdown_pct).
psycopg2.extensions.register_type(
    psycopg2.extensions.new_type(
        (1700,), "NUMERIC_AS_FLOAT",
        lambda value, curs: float(value) if value is not None else None,
    )
)
N_ENTRY_HOURS = 480
N_EXIT_HOURS = 240
ALLOWED_CLOUD_HOSTS = {
    "db.dutmxvqluuxfexbdfbxm.supabase.co",    # conexao direta
    "aws-0-sa-east-1.pooler.supabase.com",    # Supavisor pooler (IPv4-compativel)
}
def _minimal_cloud_safety_check(cfg):
    """Checagem FAIL CLOSED, autocontida para o Streamlit Cloud."""
    host = str(cfg.get("DB_HOST", "")).strip().lower()
    if host not in ALLOWED_CLOUD_HOSTS:
        st.error(f"BLOQUEADO: DB_HOST='{host}' nao e' um host autorizado do projeto Supabase 'bob-paper-live' ({sorted(ALLOWED_CLOUD_HOSTS)}).")
        st.stop()
    if str(cfg.get("DRY_RUN", "")).strip().lower() != "true":
        st.error("BLOQUEADO: DRY_RUN precisa ser 'true' -- nunca deve rodar sem isso.")
        st.stop()
def load_cloud_config():
    local_env_path = r"C:\Users\junio\BOT_LAB\config\.env.cloud"
    if os.path.exists(local_env_path):
        sys.path.insert(0, r"C:\Users\junio\BOT_LAB\config")
        from environment_guard import load_env_file, assert_cloud_paper_live_safe  # noqa: E402
        cfg = load_env_file(local_env_path)
        assert_cloud_paper_live_safe(cfg)
        return cfg
    try:
        cfg = dict(st.secrets)
    except Exception:
        st.error("Nenhuma configuracao encontrada: nem .env.cloud local, nem st.secrets no Streamlit Cloud.")
        st.stop()
        return {}
    _minimal_cloud_safety_check(cfg)
    return cfg
cloud_cfg = load_cloud_config()
SIM_IDS = {
    "MISSION10_BOB_PAPER_LIVE_20USD_001": "Missao 10 -- Paper trading AO VIVO (ativo)",
    "MISSION9_BOB_BOB_EDGE_MILD_RECAL_20USD_001": "Missao 9 -- Congelada (referencia)",
}
LIVE_SIM_ID = "MISSION10_BOB_PAPER_LIVE_20USD_001"
st.set_page_config(page_title="BOB -- Painel Espelho", page_icon="📈", layout="wide")
# ---------------------------------------------------------------------------
# MISSAO 14 -- Estilo Visual Profissional (CSS Avançado)
# ---------------------------------------------------------------------------
def _inject_custom_css():
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@500;600;700&display=swap');
    html, body, [class*="css"], .stApp { font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
    .stApp {
        background: radial-gradient(circle at 12% 0%, #131a2b 0%, #0b0f1a 45%, #07080d 100%);
    }
    section[data-testid="stSidebar"] {
        background: #0d1120;
        border-right: 1px solid rgba(255,255,255,0.06);
    }
    section[data-testid="stSidebar"] * { font-family: 'Inter', sans-serif; }
    #MainMenu, footer, header[data-testid="stHeader"] { background: transparent; }
    div[data-testid="stMetric"] {
        background: linear-gradient(180deg, rgba(255,255,255,0.045), rgba(255,255,255,0.015));
        border: 1px solid rgba(255,255,255,0.08);
        border-radius: 14px;
        padding: 14px 18px 10px 18px;
        box-shadow: 0 4px 18px rgba(0,0,0,0.25);
    }
    div[data-testid="stMetricLabel"] { font-size: 0.74rem; text-transform: uppercase; letter-spacing: .07em; color: #8b93a7 !important; }
    div[data-testid="stMetricValue"] { font-family: 'JetBrains Mono', monospace; font-weight: 700; font-size: 1.5rem !important; }
    h1, h2, h3 { font-weight: 800 !important; letter-spacing: -0.02em; }
    hr, div[data-testid="stDivider"] { border-color: rgba(255,255,255,0.08) !important; }
    .bob-pill {
        display: inline-flex; align-items: center; gap: 9px;
        padding: 7px 16px; border-radius: 999px; font-weight: 700; font-size: 0.88rem;
        letter-spacing: .02em;
    }
    .bob-pill-dot { width: 9px; height: 9px; border-radius: 50%; flex: none; }
    .bob-pill-live { background: rgba(46, 204, 113, 0.12); color: #2ecc71; border: 1px solid rgba(46,204,113,0.35); }
    .bob-pill-live .bob-pill-dot { background: #2ecc71; box-shadow: 0 0 8px #2ecc71; animation: bobpulse 1.6s infinite; }
    .bob-pill-paused { background: rgba(231, 76, 60, 0.12); color: #e74c3c; border: 1px solid rgba(231,76,60,0.35); }
    .bob-pill-paused .bob-pill-dot { background: #e74c3c; }
    @keyframes bobpulse { 0% {opacity:1;} 50% {opacity:.35;} 100% {opacity:1;} }
    .bob-badge { display:inline-block; padding: 3px 11px; border-radius: 999px; font-size: 0.78rem; font-weight: 700; letter-spacing:.02em; }
    .bob-badge-ok { background: rgba(46,204,113,0.14); color:#2ecc71; border:1px solid rgba(46,204,113,0.3); }
    .bob-badge-warn { background: rgba(243,156,18,0.14); color:#f39c12; border:1px solid rgba(243,156,18,0.3); }
    .bob-badge-bad { background: rgba(231,76,60,0.14); color:#e74c3c; border:1px solid rgba(231,76,60,0.3); }
    .bob-badge-neutral { background: rgba(149,165,166,0.14); color:#95a5a6; border:1px solid rgba(149,165,166,0.3); }
    .bob-header { display:flex; align-items:center; gap:16px; margin-bottom: 6px; }
    .bob-header-title { font-size: 1.65rem; font-weight: 800; letter-spacing:-0.02em; color:#f2f4f8; }
    .bob-header-sub { color:#8b93a7; font-size: 0.85rem; margin-top: 2px; }
    div[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; border: 1px solid rgba(255,255,255,0.08); }
    .stButton>button { border-radius: 10px; font-weight: 700; border: none; }
    button[data-baseweb="tab"] { font-weight: 700; font-size: 0.92rem; }
    div[data-baseweb="tab-highlight"] { background-color: #2ecc71 !important; }
    .stCaption, [data-testid="stCaptionContainer"] { color: #6f7891 !important; }
    </style>
    """, unsafe_allow_html=True)
_inject_custom_css()
def _require_passcode():
    expected = cloud_cfg.get("DASHBOARD_PASSCODE")
    if not expected:
        return
    if st.session_state.get("authed"):
        return
    _, mid, _ = st.columns([1, 1.1, 1])
    with mid:
        st.markdown("<div style='height:12vh'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='text-align:center; font-size:2.4rem;'>🔒</div>"
            "<div style='text-align:center; font-size:1.4rem; font-weight:800; margin-bottom:4px;'>BOB -- Painel Espelho</div>"
            "<div style='text-align:center; color:#8b93a7; font-size:0.85rem; margin-bottom:18px;'>Acesso restrito -- paper trading ao vivo</div>",
            unsafe_allow_html=True,
        )
        pw = st.text_input("Senha de acesso", type="password", label_visibility="collapsed", placeholder="Senha de acesso")
        if st.button("Entrar", type="primary", use_container_width=True):
            if pw == expected:
                st.session_state["authed"] = True
                st.rerun()
            else:
                st.error("Senha incorreta.")
    st.stop()
_require_passcode()
@contextmanager
def get_conn():
    conn = psycopg2.connect(
        host=cloud_cfg["DB_HOST"], port=cloud_cfg.get("DB_PORT", 5432),
        user=cloud_cfg["DB_USER"], password=cloud_cfg["DB_PASSWORD"], dbname=cloud_cfg["DB_NAME"],
    )
    try:
        yield conn
    finally:
        conn.close()
@st.cache_data(ttl=15)
def load_simulations():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "select simulation_id, status, current_epoch_index, current_sim_time, warmup_end, "
            "decision_cutoff, updated_at from mission7_simulations where simulation_id = any(%s)",
            (list(SIM_IDS.keys()),),
        )
        return {r["simulation_id"]: dict(r) for r in cur.fetchall()}
@st.cache_data(ttl=15)
def load_latest_checkpoint_state(sim_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select epoch_index, simulated_time, state from mission7_checkpoints "
            "where simulation_id=%s order by epoch_index desc limit 1",
            (sim_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None, None, None
        epoch_index, simulated_time, state = row
        if isinstance(state, str):
            state = json.loads(state)
        return epoch_index, simulated_time, state
@st.cache_data(ttl=15)
def load_equity_history(sim_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select epoch_index, simulated_time, "
            "(state->'capital'->>'available')::numeric as available, "
            "(state->'capital'->>'allocated')::numeric as allocated, "
            "(state->'capital'->>'reserved')::numeric as reserved, "
            "(state->'capital'->>'realized_pnl')::numeric as realized_pnl, "
            "(state->'capital'->>'peak_equity')::numeric as peak_equity "
            "from mission7_checkpoints where simulation_id=%s order by epoch_index asc",
            (sim_id,),
        )
        cols = ["epoch_index", "simulated_time", "available", "allocated", "reserved", "realized_pnl", "peak_equity"]
        rows = cur.fetchall()
        df = pd.DataFrame(rows, columns=cols)
        if not df.empty:
            df["equity_total"] = df["available"] + df["allocated"] + df["reserved"]
        return df
@st.cache_data(ttl=15)
def load_market_states(sim_id, symbol, limit=1000):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select open_time, ts, price from mission7_market_states "
            "where simulation_id=%s and symbol=%s order by open_time desc limit %s",
            (sim_id, symbol, limit),
        )
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["open_time", "ts", "price"]).sort_values("open_time")
    return df
@st.cache_data(ttl=15)
def load_current_prices(sim_id, symbols):
    """MISSAO 18 -- Preço mais recente para cálculo do P&L não realizado."""
    if not symbols:
        return {}
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select distinct on (symbol) symbol, price from mission7_market_states "
            "where simulation_id=%s and symbol = any(%s) order by symbol, open_time desc",
            (sim_id, list(symbols)),
        )
        rows = cur.fetchall()
    return {sym: price for sym, price in rows}
@st.cache_data(ttl=15)
def load_trade_stats(sim_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "select symbol, epoch_index, entry_price, exit_price, pnl_percent, holding_time_hours, "
            "resolution_sim_time from mission7_experiences "
            "where simulation_id=%s and resolved=true order by resolution_sim_time desc",
            (sim_id,),
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["symbol", "epoch_index", "entry_price", "exit_price", "pnl_percent", "holding_time_hours", "resolution_sim_time"])
@st.cache_data(ttl=15)
def load_edge_monitor_history(sim_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "select epoch_index, simulated_time, "
            "coalesce(state->'capital'->'sizing_state'->>'current_state', 'DESCONHECIDO') as edge_state "
            "from mission7_checkpoints where simulation_id=%s order by epoch_index asc",
            (sim_id,),
        )
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=["epoch_index", "simulated_time", "edge_state"])
@st.cache_data(ttl=15)
def load_bot_control(sim_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("select * from mission7_bot_control where simulation_id=%s", (sim_id,))
        row = cur.fetchone()
        return dict(row) if row else {"simulation_id": sim_id, "paused": False, "paused_reason": None, "updated_at": None, "updated_by": None}
def set_bot_control(sim_id, paused, reason, updated_by):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into mission7_bot_control (simulation_id, paused, paused_reason, updated_at, updated_by) "
            "values (%s,%s,%s, now(), %s) "
            "on conflict (simulation_id) do update set paused=excluded.paused, paused_reason=excluded.paused_reason, "
            "updated_at=now(), updated_by=excluded.updated_by",
            (sim_id, paused, reason, updated_by),
        )
        conn.commit()
    st.cache_data.clear()
def _status_pill(is_paused):
    if is_paused:
        return "<span class='bob-pill bob-pill-paused'><span class='bob-pill-dot'></span>PAUSADO</span>"
    return "<span class='bob-pill bob-pill-live'><span class='bob-pill-dot'></span>ATIVO</span>"
def _edge_badge(current_state):
    state = (current_state or "—").upper()
    cls = {"NORMAL": "bob-badge-ok", "ATENCAO": "bob-badge-warn", "ATENÇÃO": "bob-badge-warn"}.get(state, "bob-badge-bad" if state not in ("—",) else "bob-badge-neutral")
    return f"<span class='bob-badge {cls}'>{state}</span>"
EDGE_STATE_COLOR = {
    "NORMAL": "#2ecc71", "ATENCAO": "#f39c12", "ATENÇÃO": "#f39c12",
    "DEFENSIVO": "#e74c3c", "PAUSA": "#e74c3c", "DESCONHECIDO": "#95a5a6",
}
PLOTLY_DARK_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#c7cedd", family="Inter, sans-serif"),
    legend=dict(bgcolor="rgba(0,0,0,0)"),
    xaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False),
    yaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False),
)
# ---------------------------------------------------------------------------
# Sidebar -- Kill Switch & Controles
# ---------------------------------------------------------------------------
st.sidebar.markdown(
    "<div style='display:flex;align-items:center;gap:10px;margin-bottom:2px;'>"
    "<span style='font-size:1.5rem;'>⚙️</span>"
    "<span style='font-size:1.25rem;font-weight:800;'>Controle</span></div>",
    unsafe_allow_html=True,
)
st.sidebar.caption("Projeto Supabase: bob-paper-live (dutmxvqluuxfexbdfbxm)")
st.sidebar.divider()
ctrl = load_bot_control(LIVE_SIM_ID)
is_paused = bool(ctrl.get("paused"))
if is_paused:
    st.sidebar.error(f"🛑 PAUSADO desde {ctrl.get('updated_at')}\nMotivo: {ctrl.get('paused_reason') or '(nao informado)'}")
    if st.sidebar.button("✅ RETOMAR novas entradas", use_container_width=True):
        set_bot_control(LIVE_SIM_ID, False, None, "dashboard")
        st.rerun()
else:
    st.sidebar.success("🟢 BOB ativo -- operando normalmente")
    if "armed" not in st.session_state:
        st.session_state.armed = False
    if not st.session_state.armed:
        if st.sidebar.button("🛑 PARAR NOVAS ENTRADAS", type="primary", use_container_width=True):
            st.session_state.armed = True
            st.rerun()
    else:
        st.sidebar.warning("Tem certeza? Isso bloqueia NOVAS entradas a partir do proximo step. "
                           "Posicoes ja abertas continuam sendo geridas normalmente.")
        reason = st.sidebar.text_input("Motivo (opcional)", key="pause_reason")
        c1, c2 = st.sidebar.columns(2)
        if c1.button("CONFIRMAR PAUSA", type="primary", use_container_width=True):
            set_bot_control(LIVE_SIM_ID, True, reason or "acionado via dashboard", "dashboard")
            st.session_state.armed = False
            st.rerun()
        if c2.button("Cancelar", use_container_width=True):
            st.session_state.armed = False
            st.rerun()
st.sidebar.divider()
if st.sidebar.button("🔄 Atualizar agora", use_container_width=True):
    st.cache_data.clear()
    st.rerun()
auto = st.sidebar.checkbox("Auto-atualizar a cada 30s")
# ---------------------------------------------------------------------------
# Corpo Principal
# ---------------------------------------------------------------------------
st.markdown(
    "<div class='bob-header'>"
    "<div style='font-size:2.1rem;'>📈</div>"
    "<div><div class='bob-header-title'>BOB — Painel Espelho</div>"
    "<div class='bob-header-sub'>Paper trading ao vivo · Missão 10 · dados em tempo real do Supabase</div></div>"
    "</div>",
    unsafe_allow_html=True,
)
sims = load_simulations()
live = sims.get(LIVE_SIM_ID)
if live is None:
    st.error(f"Simulacao {LIVE_SIM_ID} nao encontrada no Supabase -- rode a migracao (Missao 11) primeiro.")
    st.stop()
epoch_index, simulated_time, state = load_latest_checkpoint_state(LIVE_SIM_ID)
cap = state.get("capital", {}) if state else {}
equity_total = (cap.get("available", 0) or 0) + (cap.get("allocated", 0) or 0) + (cap.get("reserved", 0) or 0)
sizing_state = cap.get("sizing_state", {}) if state else {}
st.markdown(_status_pill(is_paused), unsafe_allow_html=True)
st.write("")
tab_overview, tab_performance, tab_trades, tab_market = st.tabs(
    ["📊 Visão Geral", "📈 Performance & Risco", "📒 Trades", "💹 Mercado & Donchian"]
)
# ---------------------------------------------------------------------------
# ABA 1 -- Visão Geral
# ---------------------------------------------------------------------------
with tab_overview:
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Capital total", f"${equity_total:,.2f}")
    col2.metric("PnL realizado", f"${cap.get('realized_pnl', 0):,.2f}")
    col3.metric("Última época", epoch_index if epoch_index is not None else "—")
    col4.metric("Última sincronização", str(simulated_time)[:19] if simulated_time else "—")
    col5.markdown(
        f"<div style='padding-top:6px;'><div style='font-size:0.74rem; text-transform:uppercase; letter-spacing:.07em; color:#8b93a7; margin-bottom:8px;'>Monitor de edge</div>{_edge_badge(sizing_state.get('current_state'))}</div>",
        unsafe_allow_html=True,
    )
    st.write("")
    left, right = st.columns([1, 1.4])
    with left:
        st.subheader("Composição do capital")
        available, allocated, reserved = cap.get("available", 0) or 0, cap.get("allocated", 0) or 0, cap.get("reserved", 0) or 0
        if equity_total > 0:
            donut = go.Figure(go.Pie(
                labels=["Disponível", "Alocado em posições", "Reservado"],
                values=[available, allocated, reserved],
                hole=0.62,
                marker=dict(colors=["#2ecc71", "#3498db", "#f39c12"], line=dict(color="#0b0f1a", width=2)),
                textinfo="percent",
                hovertemplate="%{label}: $%{value:,.2f}<extra></extra>",
            ))
            donut.update_layout(**PLOTLY_DARK_LAYOUT)
            donut.update_layout(
                height=300, margin=dict(l=10, r=10, t=10, b=10),
                showlegend=True, legend=dict(orientation="h", y=-0.1, bgcolor="rgba(0,0,0,0)"),
                annotations=[dict(text=f"${equity_total:,.0f}", x=0.5, y=0.5, font_size=18, font_color="#f2f4f8", showarrow=False)],
            )
            st.plotly_chart(donut, use_container_width=True)
        else:
            st.info("Sem dados de capital ainda.")
    with right:
        st.subheader("Posições em andamento & P&L Não Realizado")
        positions = state.get("positions", {}) if state else {}
        open_positions = {sym: p for sym, p in positions.items() if p.get("in_position")}
        universe_size = len(state.get("price_history", {})) if state else 0
        st.caption(f"Universo: {universe_size} símbolos · Posições abertas: {len(open_positions)}")
        if open_positions:
            pos_df = pd.DataFrame([
                {"símbolo": sym, "preço_entrada": p.get("entry_price"), "capital_alocado_usd": p.get("allocated_capital_usd"),
                 "atr_pct_na_entrada": p.get("atr_pct_at_entry")}
                for sym, p in open_positions.items()
            ]).sort_values("capital_alocado_usd", ascending=False)

            if equity_total > 0:
                pos_df["% do capital total"] = (pos_df["capital_alocado_usd"] / equity_total * 100).round(2)
            current_prices = load_current_prices(LIVE_SIM_ID, pos_df["símbolo"].tolist())
            pos_df["preço_atual"] = pos_df["símbolo"].map(current_prices)
            entry_safe = pos_df["preço_entrada"].replace(0, float("nan"))
            pos_df["p&l_não_realizado_%"] = (
                (pos_df["preço_atual"] - pos_df["preço_entrada"]) / entry_safe * 100.0
            ).round(2)
            pos_df = pos_df.sort_values("p&l_não_realizado_%", ascending=False, na_position="last")
            pnl_valid = pos_df["p&l_não_realizado_%"].dropna()
            if not pnl_valid.empty:
                m1, m2, m3 = st.columns(3)
                m1.metric("P&L não realizado médio", f"{pnl_valid.mean():+.1f}%")
                best_row = pos_df.loc[pnl_valid.idxmax()]
                worst_row = pos_df.loc[pnl_valid.idxmin()]
                m2.metric("Melhor posição", best_row["símbolo"], f"{best_row['p&l_não_realizado_%']:+.1f}%")
                m3.metric("Pior posição", worst_row["símbolo"], f"{worst_row['p&l_não_realizado_%']:+.1f}%")
                st.caption("P&L não realizado = variação % entre o preço de entrada e o preço mais recente na nuvem, "
                           "SEM taxas -- só vira lucro/prejuízo de verdade quando a posição fechar de fato.")
                pnl_bar = go.Figure(go.Bar(
                    x=pos_df["p&l_não_realizado_%"], y=pos_df["símbolo"], orientation="h",
                    marker=dict(color=["#2ecc71" if v >= 0 else "#e74c3c" for v in pos_df["p&l_não_realizado_%"].fillna(0)]),
                    hovertemplate="%{y}: %{x:+.2f}%<extra></extra>",
                ))
                pnl_bar.update_layout(**PLOTLY_DARK_LAYOUT)
                pnl_bar.update_layout(
                    height=280, margin=dict(l=10, r=10, t=10, b=10),
                    yaxis=dict(autorange="reversed", gridcolor="rgba(255,255,255,0.06)"),
                    xaxis=dict(title="P&L não realizado (%)", gridcolor="rgba(255,255,255,0.06)", zeroline=True, zerolinecolor="rgba(255,255,255,0.25)"),
                )
                st.plotly_chart(pnl_bar, use_container_width=True)
            bar = go.Figure(go.Bar(
                x=pos_df["capital_alocado_usd"], y=pos_df["símbolo"], orientation="h",
                marker=dict(color="#3498db"),
                hovertemplate="%{y}: $%{x:,.2f}<extra></extra>",
            ))
            bar.update_layout(**PLOTLY_DARK_LAYOUT)
            bar.update_layout(
                height=260, margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(autorange="reversed", gridcolor="rgba(255,255,255,0.06)"),
                xaxis=dict(title="Capital alocado (USD)", gridcolor="rgba(255,255,255,0.06)"),
            )
            st.plotly_chart(bar, use_container_width=True)
        else:
            st.info("Nenhuma posição aberta no momento.")
# ---------------------------------------------------------------------------
# ABA 2 -- Performance & Risco
# ---------------------------------------------------------------------------
with tab_performance:
    st.subheader("Curva de Capital e Drawdown")
    eq_df = load_equity_history(LIVE_SIM_ID)
    if not eq_df.empty and len(eq_df) > 0:
        # CORRECAO: peak_equity JA vem certo da query acima (o pico de
        # verdade rastreado internamente pelo motor congelado, dentro de
        # state->capital->peak_equity) -- NAO recalcular via cummax()
        # sobre so' as poucas linhas de checkpoint que existem na nuvem
        # ate' agora. Um cummax() local so' enxerga o que ja' foi migrado
        # pra' cloud (hoje so' 2 checkpoints), entao ele SUBESTIMA o pico
        # real sempre que o pico verdadeiro veio de antes da migracao --
        # e' exatamente o caso agora: pico real ~$117.46, mas as 2 linhas
        # disponiveis sao ambas ~$108.13, o que faria o cummax() mostrar
        # 0% de drawdown quando o real e' -7.95% (a so' 2 pontos do
        # disjuntor de -10%). Usar sempre o peak_equity do motor.
        eq_df["drawdown_pct"] = (eq_df["equity_total"] - eq_df["peak_equity"]) / eq_df["peak_equity"] * 100.0
        fig_eq = go.Figure()
        fig_eq.add_trace(go.Scatter(
            x=eq_df["simulated_time"], y=eq_df["equity_total"],
            mode="lines+markers", name="Capital Total ($)",
            line=dict(color="#2ecc71", width=2.5),
            marker=dict(size=5),
        ))
        fig_eq.update_layout(**PLOTLY_DARK_LAYOUT)
        fig_eq.update_layout(
            height=350, margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(title="USD"), xaxis=dict(title="Tempo Simulado"),
        )
        st.plotly_chart(fig_eq, use_container_width=True)
        st.subheader("Histórico do Monitor de Edge")
        edge_df = load_edge_monitor_history(LIVE_SIM_ID)
        if not edge_df.empty:
            colors = edge_df["edge_state"].map(lambda s: EDGE_STATE_COLOR.get(s.upper(), "#95a5a6"))
            fig_edge = go.Figure(go.Bar(
                x=edge_df["simulated_time"], y=[1] * len(edge_df),
                marker=dict(color=colors),
                customdata=edge_df["edge_state"],
                hovertemplate="Tempo: %{x}<br>Estado: %{customdata}<extra></extra>",
            ))
            fig_edge.update_layout(**PLOTLY_DARK_LAYOUT)
            fig_edge.update_layout(
                height=140, margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(showticklabels=False, showgrid=False, zeroline=False),
                xaxis=dict(title="Épocas / Tempo Simulado"),
            )
            st.plotly_chart(fig_edge, use_container_width=True)
    else:
        st.info("Aguardando mais checkpoints na nuvem para renderizar a curva de capital.")
# ---------------------------------------------------------------------------
# ABA 3 -- Trades Fechados
# ---------------------------------------------------------------------------
with tab_trades:
    st.subheader("Histórico de Trades Fechados")
    trades_df = load_trade_stats(LIVE_SIM_ID)
    if not trades_df.empty:
        wins = trades_df[trades_df["pnl_percent"] > 0]
        win_rate = len(wins) / len(trades_df) * 100.0 if len(trades_df) > 0 else 0

        tc1, tc2, tc3 = st.columns(3)
        tc1.metric("Total de trades fechados", len(trades_df))
        tc2.metric("Taxa de acerto (Win Rate)", f"{win_rate:.1f}%")
        tc3.metric("PnL médio por trade", f"{trades_df['pnl_percent'].mean():+.2f}%")
        st.dataframe(trades_df, use_container_width=True)
    else:
        st.info("Nenhum trade fechado registrado até o momento. Como o BOB utiliza Donchian (seguidor de tendência), "
                "as posições vencedoras permanecem abertas enquanto a tendência de alta se mantiver firme.")
# ---------------------------------------------------------------------------
# ABA 4 -- Mercado & Donchian
# ---------------------------------------------------------------------------
with tab_market:
    st.subheader("Visão de Mercado & Canais Donchian")
    universe = list(state.get("price_history", {}).keys()) if state else []
    if universe:
        selected_sym = st.selectbox("Selecione o ativo", sorted(universe))
        m_df = load_market_states(LIVE_SIM_ID, selected_sym, limit=500)
        if not m_df.empty:
            fig_m = go.Figure()
            fig_m.add_trace(go.Scatter(
                x=m_df["open_time"], y=m_df["price"],
                mode="lines", name=selected_sym,
                line=dict(color="#3498db", width=2),
            ))
            fig_m.update_layout(**PLOTLY_DARK_LAYOUT)
            fig_m.update_layout(
                height=400, margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(title="Preço (USDT)"), xaxis=dict(title="Data / Hora (UTC)"),
            )
            st.plotly_chart(fig_m, use_container_width=True)
        else:
            st.info(f"Sem dados de mercado gravados para {selected_sym}.")
    else:
        st.info("Universo de ativos indisponível no checkpoint atual.")
# Auto-refresh logic se ativado
if auto:
    time.sleep(30)
    st.rerun()
