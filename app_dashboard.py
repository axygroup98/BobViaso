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
    # Caminho local generico (funciona pra qualquer usuario/maquina, nao
    # so pra uma pasta pessoal especifica) -- pode ser sobrescrito via
    # env var BOB_LOCAL_CONFIG_DIR se o BOT_LAB estiver em outro lugar.
    # No Streamlit Cloud esse caminho simplesmente nao existe, entao cai
    # no fallback de st.secrets logo abaixo -- comportamento identico ao
    # de antes, so sem expor a estrutura de pasta local no repo publico.
    local_config_dir = os.environ.get(
        "BOB_LOCAL_CONFIG_DIR",
        os.path.join(os.path.expanduser("~"), "BOT_LAB", "config"),
    )
    local_env_path = os.path.join(local_config_dir, ".env.cloud")
    if os.path.exists(local_env_path):
        sys.path.insert(0, local_config_dir)
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
def load_risk_breaker_events(sim_id):
    """MISSAO 19 -- item critico #3: historico de disjuntores de risco,
    lido de mission7_risk_breaker_events (tabela ja existente, escrita
    pelo proprio motor congelado -- SOMENTE LEITURA aqui). Ate' agora
    invisivel no painel apesar de ja' existir 1 evento real (PEPEUSDT)."""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "select symbol, epoch_index, threshold_pct, actual_pct, event_sim_time, "
            "simulation_continued, drawdown_pct_at_event, pause_type, production_behavior "
            "from mission7_risk_breaker_events where simulation_id=%s order by event_sim_time desc",
            (sim_id,),
        )
        rows = cur.fetchall()
    cols = ["symbol", "epoch_index", "threshold_pct", "actual_pct", "event_sim_time",
            "simulation_continued", "drawdown_pct_at_event", "pause_type", "production_behavior"]
    return pd.DataFrame([dict(r) for r in rows], columns=cols)
def _price_history_df(state, symbol):
    """MISSAO 19 -- itens criticos #4/#5/#6: reconstroi a serie de precos
    usada de VERDADE pelo motor congelado para as decisoes de Donchian,
    lendo state->price_history->{symbol} (jah carregado 1x por pageview
    via load_latest_checkpoint_state -- zero query nova). Preferido a
    mission7_market_states porque essa tabela so' guarda o historico
    RECENTE pos-migracao pra nuvem (poucas dezenas de candles hoje),
    enquanto price_history no checkpoint tem a janela completa (485
    pontos, o suficiente pro canal de entrada de 480h) que o motor usa
    de fato. Cada elemento e' [open_time_ms, price]. Nunca preenche
    canais sem dados suficientes (min_periods = janela inteira) -- se
    nao houver historico suficiente ainda, a banda fica ausente (NaN),
    nunca fabricada."""
    hist = (state or {}).get("price_history", {}).get(symbol) or []
    if not hist:
        return pd.DataFrame(columns=["open_time", "price", "dt", "donchian_entry_upper", "donchian_exit_lower"])
    df = pd.DataFrame(hist, columns=["open_time", "price"]).sort_values("open_time").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["donchian_entry_upper"] = df["price"].rolling(N_ENTRY_HOURS, min_periods=N_ENTRY_HOURS).max()
    df["donchian_exit_lower"] = df["price"].rolling(N_EXIT_HOURS, min_periods=N_EXIT_HOURS).min()
    return df
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
# MISSAO 20 -- nova feature "Vender" (fechamento manual/discricionario de
# uma posicao aberta, a pedido explicito do usuario). O painel NAO tem
# acesso ao motor/checkpoint (so' roda app_dashboard.py -- ver
# _bob_cloud_live_step.py), entao NAO fecha nada aqui: so' ENFILEIRA um
# pedido pendente numa tabela NOVA e dedicada (mission7_manual_close_requests,
# aditiva, criada so' pra isso -- nenhuma tabela mission7_* original
# tocada, nenhum checkpoint escrito por este painel). O fechamento de
# verdade e' aplicado por _bob_cloud_live_step.py rodando no PC, na
# proxima vez que rodar, reaproveitando _close_position() -- a MESMA
# funcao que o motor congelado ja usa pras saidas tecnicas (Donchian/
# stop-ATR) -- ver docstring desse script pra' por que um checkpoint novo
# nao pode ser inserido direto por aqui (quebraria a relacao causal
# epoch_index<->tempo). Indice unico parcial na tabela evita pedido
# duplicado pro mesmo simbolo enquanto houver 1 pendente.
@st.cache_data(ttl=10)
def load_manual_close_requests(sim_id, limit=50):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "select id, symbol, status, requested_at, requested_by, executed_at, execution_note "
            "from mission7_manual_close_requests where simulation_id=%s "
            "order by requested_at desc limit %s",
            (sim_id, limit),
        )
        rows = cur.fetchall()
    cols = ["id", "symbol", "status", "requested_at", "requested_by", "executed_at", "execution_note"]
    return pd.DataFrame([dict(r) for r in rows], columns=cols)
def request_manual_close(sim_id, symbol, requested_by="dashboard"):
    """Enfileira o pedido; devolve True se um pedido NOVO foi criado, False
    se ja' havia um pedido pendente pra este simbolo (evita duplicar)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            "insert into mission7_manual_close_requests (simulation_id, symbol, status, requested_by) "
            "values (%s, %s, 'pending', %s) "
            "on conflict (simulation_id, symbol) where status='pending' do nothing "
            "returning id",
            (sim_id, symbol, requested_by),
        )
        row = cur.fetchone()
        conn.commit()
    st.cache_data.clear()
    return row is not None
def _status_pill(is_paused):
    if is_paused:
        return "<span class='bob-pill bob-pill-paused'><span class='bob-pill-dot'></span>PAUSADO</span>"
    return "<span class='bob-pill bob-pill-live'><span class='bob-pill-dot'></span>ATIVO</span>"
def _edge_badge(current_state):
    state = (current_state or "—").upper()
    cls = {"NORMAL": "bob-badge-ok", "ATENCAO": "bob-badge-warn", "ATENÇÃO": "bob-badge-warn"}.get(state, "bob-badge-bad" if state not in ("—",) else "bob-badge-neutral")
    return f"<span class='bob-badge {cls}'>{state}</span>"
def _staleness_badge(simulated_time):
    """MISSAO 19 -- item critico #2: indicador de idade do dado. Compara
    o simulated_time do ultimo checkpoint (tempo real/causal, nao
    inventado) com o relogio real agora -- exatamente o sintoma que
    causou a investigacao da epoca 1079 travada (dado parado sem
    nenhum aviso visivel no painel)."""
    if simulated_time is None:
        return "<span class='bob-badge bob-badge-neutral'>🕒 idade do dado: sem checkpoint</span>", None
    st_dt = simulated_time if simulated_time.tzinfo else simulated_time.replace(tzinfo=timezone.utc)
    age_h = (datetime.now(timezone.utc) - st_dt).total_seconds() / 3600.0
    if age_h <= 2:
        cls = "bob-badge-ok"
    elif age_h <= 6:
        cls = "bob-badge-warn"
    else:
        cls = "bob-badge-bad"
    label = f"🕒 último checkpoint: há {age_h:.1f}h" if age_h >= 0 else "🕒 último checkpoint: agora"
    return f"<span class='bob-badge {cls}'>{label}</span>", age_h
EDGE_STATE_COLOR = {
    "NORMAL": "#2ecc71", "ATENCAO": "#f39c12", "ATENÇÃO": "#f39c12",
    "DEFENSIVO": "#e74c3c", "PAUSA": "#e74c3c", "DESCONHECIDO": "#95a5a6",
}
def _arrow(v):
    """MISSAO 20 -- item medio #10: simbolo (nao so' cor) pra ganho/perda,
    acessivel a quem nao distingue vermelho/verde. Usado em toda barra e
    tabela que mostra P&L."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "•"
    return "▲" if v >= 0 else "▼"
def _highlight_metric_html(label, value_html, sub=None, color="#2ecc71"):
    """MISSAO 20 -- item medio #11: hierarquia visual mais forte pros
    numeros mais criticos pra decisao (capital total, drawdown atual) --
    reusa a mesma linguagem visual de cartao ja' usada em bob-badge/
    bob-pill, so' com destaque maior (borda colorida + fonte maior)."""
    sub_html = f"<div style='color:#8b93a7; font-size:0.78rem; margin-top:4px;'>{sub}</div>" if sub else ""
    return (
        f"<div style='background:linear-gradient(180deg, rgba(255,255,255,0.05), rgba(255,255,255,0.015));"
        f"border:1px solid rgba(255,255,255,0.08); border-left:4px solid {color};"
        f"border-radius:14px; padding:16px 20px; box-shadow:0 4px 18px rgba(0,0,0,0.25);'>"
        f"<div style='font-size:0.76rem; text-transform:uppercase; letter-spacing:.07em; color:#8b93a7;'>{label}</div>"
        f"<div style='font-family:\"JetBrains Mono\", monospace; font-weight:800; font-size:2.1rem; color:#f2f4f8; margin-top:2px;'>{value_html}</div>"
        f"{sub_html}</div>"
    )
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
sizing_state = cap.get("sizing_state", {}) if state else {}
# MISSAO 19 -- item critico #7: fonte UNICA de capital total pras duas
# abas (Visao Geral e Performance & Risco) em vez de cada aba calcular
# o proprio numero a partir de queries/campos diferentes. eq_df (que a
# aba Performance ja precisava) e' carregado 1x aqui e reusado nas duas
# -- estruturalmente elimina a divergencia em vez de so' detecta-la.
# Guarda de reserva: se por algum motivo (corrida entre 2 queries em
# cache com TTLs vencendo em momentos diferentes) a epoca do checkpoint
# "state" nao bater com a ultima epoca de eq_df, avisa explicitamente
# em vez de mostrar 2 numeros diferentes calados.
eq_df = load_equity_history(LIVE_SIM_ID)
capital_mismatch = None
if not eq_df.empty:
    last_row = eq_df.iloc[-1]
    equity_total = float(last_row["equity_total"])
    if epoch_index is not None and int(last_row["epoch_index"]) != int(epoch_index):
        capital_mismatch = (int(epoch_index), int(last_row["epoch_index"]))
else:
    equity_total = (cap.get("available", 0) or 0) + (cap.get("allocated", 0) or 0) + (cap.get("reserved", 0) or 0)
st.markdown(_status_pill(is_paused), unsafe_allow_html=True)
staleness_html, staleness_age_h = _staleness_badge(simulated_time)
st.markdown(staleness_html, unsafe_allow_html=True)
if capital_mismatch:
    st.warning(
        f"⚠️ Época do checkpoint (nº {capital_mismatch[0]}) diverge da última época do histórico de capital "
        f"(nº {capital_mismatch[1]}) — provavelmente um novo checkpoint chegou entre as duas consultas. "
        f"Clique em '🔄 Atualizar agora' na barra lateral antes de confiar nos números desta tela."
    )
st.write("")
tab_overview, tab_performance, tab_trades, tab_market, tab_sell = st.tabs(
    ["📊 Visão Geral", "📈 Performance & Risco", "📒 Trades", "💹 Mercado & Donchian", "🔴 Vender"]
)
# ---------------------------------------------------------------------------
# ABA 1 -- Visão Geral
# ---------------------------------------------------------------------------
with tab_overview:
    col1, col2, col3, col4, col5 = st.columns(5)
    # MISSAO 20 -- item medio #11: capital total e' o numero mais critico
    # da tela (base de tudo: drawdown, disjuntor, posicoes) -- ganha
    # destaque visual (borda + fonte maior) em vez de ficar do mesmo
    # tamanho que "Ultima sincronizacao".
    with col1:
        st.markdown(_highlight_metric_html("Capital total", f"${equity_total:,.2f}", color="#2ecc71"), unsafe_allow_html=True)
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
                # MISSAO 20 -- item medio #8: tabela detalhada de posições
                # (preço entrada/atual, ATR%, capital) -- regressão sinalizada
                # desde a Missão 17, agora restaurada com formatação de
                # coluna (dólar/percentual) em vez de números crus.
                detail_cols = ["símbolo", "preço_entrada", "preço_atual", "p&l_não_realizado_%", "capital_alocado_usd", "atr_pct_na_entrada"]
                col_config = {
                    "símbolo": st.column_config.TextColumn("Símbolo"),
                    "preço_entrada": st.column_config.NumberColumn("Preço entrada", format="%.6g"),
                    "preço_atual": st.column_config.NumberColumn("Preço atual", format="%.6g"),
                    "p&l_não_realizado_%": st.column_config.NumberColumn("P&L não realizado (%)", format="%+.2f%%", help="Sem taxas -- só realiza quando a posição fechar."),
                    "capital_alocado_usd": st.column_config.NumberColumn("Capital alocado", format="dollar"),
                    "atr_pct_na_entrada": st.column_config.NumberColumn("ATR% na entrada", format="%.2f%%"),
                }
                if "% do capital total" in pos_df.columns:
                    detail_cols.append("% do capital total")
                    col_config["% do capital total"] = st.column_config.NumberColumn("% do capital total", format="%.2f%%")
                st.dataframe(pos_df[detail_cols], use_container_width=True, hide_index=True, column_config=col_config)
                # MISSAO 20 -- item medio #10: simbolo ▲/▼ no rótulo de cada
                # barra, não só a cor -- acessível a quem não distingue
                # vermelho/verde.
                pnl_bar = go.Figure(go.Bar(
                    x=pos_df["p&l_não_realizado_%"], y=pos_df["símbolo"], orientation="h",
                    marker=dict(color=["#2ecc71" if v >= 0 else "#e74c3c" for v in pos_df["p&l_não_realizado_%"].fillna(0)]),
                    text=[("• sem dado" if pd.isna(v) else f"{_arrow(v)} {v:+.1f}%") for v in pos_df["p&l_não_realizado_%"]],
                    textposition="outside",
                    hovertemplate="%{y}: %{x:+.2f}%<extra></extra>",
                ))
                pnl_bar.update_layout(**PLOTLY_DARK_LAYOUT)
                pnl_bar.update_layout(
                    height=280, margin=dict(l=10, r=10, t=10, b=10),
                    yaxis=dict(autorange="reversed", gridcolor="rgba(255,255,255,0.06)"),
                    xaxis=dict(title="P&L não realizado (%)", gridcolor="rgba(255,255,255,0.06)", zeroline=True, zerolinecolor="rgba(255,255,255,0.25)"),
                )
                st.plotly_chart(pnl_bar, use_container_width=True)
            # MISSAO 19 -- item critico #6: distancia ate' o canal de
            # saida Donchian (10d/240h) por posicao aberta -- usa a
            # mesma serie price_history do checkpoint (_price_history_df,
            # ja' documentada acima) que o motor congelado usa de fato
            # pra decidir a saida. So' mostra a posicao se houver 240h
            # completas de historico -- nunca fabrica um canal parcial.
            dist_rows = []
            for sym in pos_df["símbolo"]:
                sym_df = _price_history_df(state, sym)
                if sym_df.empty or pd.isna(sym_df["donchian_exit_lower"].iloc[-1]):
                    continue
                last_price = sym_df["price"].iloc[-1]
                exit_low = sym_df["donchian_exit_lower"].iloc[-1]
                if last_price:
                    dist_rows.append({"símbolo": sym, "distância_até_saída_%": round((last_price - exit_low) / last_price * 100.0, 2)})
            if dist_rows:
                dist_df = pd.DataFrame(dist_rows).sort_values("distância_até_saída_%")
                st.markdown("**Distância até o canal de saída Donchian (10d)**")
                near_exit = dist_df[dist_df["distância_até_saída_%"] <= 5.0]
                if not near_exit.empty:
                    st.caption(f"⚠️ {len(near_exit)} posição(ões) a ≤5% do canal de saída (10d): {', '.join(near_exit['símbolo'])}.")
                dist_bar = go.Figure(go.Bar(
                    x=dist_df["distância_até_saída_%"], y=dist_df["símbolo"], orientation="h",
                    marker=dict(color=["#e74c3c" if v <= 5 else ("#f39c12" if v <= 15 else "#2ecc71") for v in dist_df["distância_até_saída_%"]]),
                    hovertemplate="%{y}: %{x:.2f}% até o canal de saída (10d)<extra></extra>",
                ))
                dist_bar.update_layout(**PLOTLY_DARK_LAYOUT)
                dist_bar.update_layout(
                    height=260, margin=dict(l=10, r=10, t=10, b=10),
                    yaxis=dict(autorange="reversed", gridcolor="rgba(255,255,255,0.06)"),
                    xaxis=dict(title="Distância até o canal de saída (%)", gridcolor="rgba(255,255,255,0.06)"),
                )
                st.plotly_chart(dist_bar, use_container_width=True)
            else:
                st.caption("Ainda sem 240h de histórico de preço suficiente pra calcular o canal de saída de nenhuma posição aberta.")
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
    # eq_df ja' foi carregado 1x acima (fonte unica de capital, item
    # critico #7) -- reusado aqui, nao recarregado de novo.
    if not eq_df.empty and len(eq_df) > 0:
        # CORRECAO (Missao 17): peak_equity JA vem certo da query acima (o
        # pico de verdade rastreado internamente pelo motor congelado,
        # dentro de state->capital->peak_equity) -- NAO recalcular via
        # cummax() sobre so' as poucas linhas de checkpoint que existem na
        # nuvem ate' agora. Um cummax() local so' enxerga o que ja' foi
        # migrado pra' cloud, entao ele SUBESTIMA o pico real sempre que o
        # pico verdadeiro veio de antes da migracao. Usar sempre o
        # peak_equity do motor.
        eq_df["drawdown_pct"] = (eq_df["equity_total"] - eq_df["peak_equity"]) / eq_df["peak_equity"] * 100.0
        current_dd = float(eq_df["drawdown_pct"].iloc[-1])
        BREAKER_PCT = -10.0  # MAX_CUM_LOSS_PAUSE_PCT em risk_engine.py -- so' leitura/exibicao, nunca alterar aqui.
        dd1, dd2, dd3 = st.columns(3)
        dd1.metric("Capital total (fonte única)", f"${equity_total:,.2f}")
        dd1.caption(f"Época {epoch_index} · pico histórico ${float(eq_df['peak_equity'].iloc[-1]):,.2f}")
        dd_color = "🟢" if current_dd > -5 else ("🟠" if current_dd > -8 else "🔴")
        dd_hex = "#2ecc71" if current_dd > -5 else ("#f39c12" if current_dd > -8 else "#e74c3c")
        # MISSAO 20 -- item medio #11: drawdown atual e' o numero que mais
        # importa pra seguranca (o mais perto do disjuntor de -10%) --
        # mesmo destaque visual dado ao capital total na Visao Geral.
        with dd2:
            st.markdown(_highlight_metric_html(f"{dd_color} Drawdown atual", f"{_arrow(current_dd)} {current_dd:+.2f}%", color=dd_hex), unsafe_allow_html=True)
        dd2.caption(f"Disjuntor de segurança em {BREAKER_PCT:.0f}%")
        dd3.metric("Distância até o disjuntor", f"{current_dd - BREAKER_PCT:+.2f} p.p.")
        # MISSAO 19 -- item critico #1: o drawdown_pct sempre foi
        # calculado aqui em cima, mas nunca era de fato desenhado em
        # lugar nenhum da tela -- ficava "morto" no dataframe. Este e' o
        # grafico de verdade, com a linha do disjuntor de -10% marcada.
        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=eq_df["simulated_time"], y=eq_df["drawdown_pct"],
            mode="lines+markers", name="Drawdown (%)",
            line=dict(color="#e74c3c", width=2.5),
            marker=dict(size=5),
            fill="tozeroy", fillcolor="rgba(231,76,60,0.12)",
            hovertemplate="%{x}<br>Drawdown: %{y:.2f}%<extra></extra>",
        ))
        fig_dd.add_hline(
            y=BREAKER_PCT, line_dash="dash", line_color="#e74c3c",
            annotation_text=f"Disjuntor de segurança ({BREAKER_PCT:.0f}%)",
            annotation_position="bottom right", annotation_font_color="#e74c3c",
        )
        fig_dd.update_layout(**PLOTLY_DARK_LAYOUT)
        fig_dd.update_layout(
            height=280, margin=dict(l=10, r=10, t=10, b=10),
            yaxis=dict(title="Drawdown (%)"), xaxis=dict(title="Tempo Simulado"),
        )
        st.plotly_chart(fig_dd, use_container_width=True)
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
    # MISSAO 19 -- item critico #3: historico de disjuntores de risco
    # (mission7_risk_breaker_events), ate' agora existente na tabela mas
    # invisivel no painel -- inclusive o evento real do PEPEUSDT na
    # epoca 1079 nunca apareceu em lugar nenhum da UI.
    st.subheader("Histórico de Disjuntores de Risco")
    breaker_df = load_risk_breaker_events(LIVE_SIM_ID)
    if not breaker_df.empty:
        st.warning(f"⚠️ {len(breaker_df)} evento(s) de disjuntor de risco registrado(s) no motor.")
        show_df = breaker_df.rename(columns={
            "symbol": "símbolo", "epoch_index": "época", "threshold_pct": "limite_%",
            "actual_pct": "atual_%", "event_sim_time": "quando (sim.)",
            "simulation_continued": "simulação continuou", "drawdown_pct_at_event": "drawdown_no_evento_%",
            "pause_type": "tipo_de_pausa", "production_behavior": "comportamento_em_produção",
        })
        st.dataframe(show_df, use_container_width=True)
        st.caption(
            "Em produção real (fora do laboratório DRY_RUN), o comportamento configurado é "
            "'production_behavior' -- aqui em paper trading o motor segue 'research_behavior' "
            "(isola o ativo e continua o universo), nunca decisão deste painel."
        )
    else:
        st.success("✅ Nenhum disjuntor de risco acionado até o momento.")
# ---------------------------------------------------------------------------
# ABA 3 -- Trades Fechados
# ---------------------------------------------------------------------------
with tab_trades:
    st.subheader("Histórico de Trades Fechados")
    trades_df = load_trade_stats(LIVE_SIM_ID)
    if not trades_df.empty:
        wins = trades_df[trades_df["pnl_percent"] > 0]
        win_rate = len(wins) / len(trades_df) * 100.0 if len(trades_df) > 0 else 0

        # MISSAO 20 -- item medio #9: Fator de lucro = soma dos % positivos
        # / abs(soma dos % negativos) -- formula padrao da industria
        # (gross profit / gross loss), conferida contra a definicao usada
        # por CrossTrade/TradeZella. CAVEAT explicito: mission7_experiences
        # nao grava o capital alocado por trade fechado, so' pnl_percent --
        # entao aqui e' um Fator de lucro em BASE %, nao em dolares. Se as
        # posicoes tiverem tamanhos muito diferentes, pode divergir do
        # fator em $ de verdade. Nunca escondido -- caption abaixo deixa
        # isso explicito.
        gross_profit = trades_df.loc[trades_df["pnl_percent"] > 0, "pnl_percent"].sum()
        gross_loss = trades_df.loc[trades_df["pnl_percent"] < 0, "pnl_percent"].sum()  # já negativo
        if gross_loss < 0:
            profit_factor_label = f"{gross_profit / abs(gross_loss):.2f}"
        elif gross_profit > 0:
            profit_factor_label = "∞ (sem perdas)"
        else:
            profit_factor_label = "—"
        tc1, tc2, tc3, tc4 = st.columns(4)
        tc1.metric("Total de trades fechados", len(trades_df))
        tc2.metric("Taxa de acerto (Win Rate)", f"{win_rate:.1f}%")
        tc3.metric("PnL médio por trade", f"{_arrow(trades_df['pnl_percent'].mean())} {trades_df['pnl_percent'].mean():+.2f}%")
        tc4.metric("Fator de lucro", profit_factor_label, help="Soma dos ganhos % / soma das perdas % (base percentual, não $ -- ver observação abaixo).")
        st.caption(
            "Fator de lucro calculado em base **percentual** (soma dos % de trades vencedores dividida pela soma "
            "absoluta dos % de trades perdedores), porque mission7_experiences não grava o capital alocado por "
            "trade fechado -- pode divergir do fator de lucro em dólares se as posições tiverem tamanhos muito "
            "diferentes entre si. Acima de 1.0 = estratégia lucrativa; abaixo de 1.0 = perdedora."
        )
        show_trades = trades_df.copy()
        show_trades.insert(0, "resultado", show_trades["pnl_percent"].map(_arrow))
        st.dataframe(
            show_trades, use_container_width=True, hide_index=True,
            column_config={
                "resultado": st.column_config.TextColumn("​", width="small"),
                "pnl_percent": st.column_config.NumberColumn("PnL (%)", format="%+.2f%%"),
                "entry_price": st.column_config.NumberColumn("Preço entrada", format="%.6g"),
                "exit_price": st.column_config.NumberColumn("Preço saída", format="%.6g"),
                "holding_time_hours": st.column_config.NumberColumn("Horas em posição", format="%.1f"),
            },
        )
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
        # MISSAO 19 -- itens criticos #4 e #5: trocado de
        # load_market_states (so' tem o historico curto pos-migracao pra
        # nuvem, ~1 dia hoje) pra' _price_history_df, que le' a MESMA
        # serie que o motor congelado usa de fato pra decidir entrada
        # (canal de 480h/20d) e saida (canal de 240h/10d) -- ja' carregada
        # 1x no checkpoint, sem query nova. De quebra corrige o eixo X:
        # antes usava open_time bruto (milissegundos epoch, ilegivel);
        # agora usa a coluna 'dt', ja' convertida pra datetime de verdade.
        m_df = _price_history_df(state, selected_sym)
        if not m_df.empty:
            fig_m = go.Figure()
            if m_df["donchian_entry_upper"].notna().any():
                fig_m.add_trace(go.Scatter(
                    x=m_df["dt"], y=m_df["donchian_entry_upper"],
                    mode="lines", name=f"Canal de entrada ({N_ENTRY_HOURS}h / 20d)",
                    line=dict(color="#2ecc71", width=1.3, dash="dot"),
                    hovertemplate="Canal de entrada: %{y:.8f}<extra></extra>",
                ))
            if m_df["donchian_exit_lower"].notna().any():
                fig_m.add_trace(go.Scatter(
                    x=m_df["dt"], y=m_df["donchian_exit_lower"],
                    mode="lines", name=f"Canal de saída ({N_EXIT_HOURS}h / 10d)",
                    line=dict(color="#e74c3c", width=1.3, dash="dot"),
                    hovertemplate="Canal de saída: %{y:.8f}<extra></extra>",
                ))
            fig_m.add_trace(go.Scatter(
                x=m_df["dt"], y=m_df["price"],
                mode="lines", name=selected_sym,
                line=dict(color="#3498db", width=2),
                hovertemplate="%{x}<br>Preço: %{y:.8f}<extra></extra>",
            ))
            fig_m.update_layout(**PLOTLY_DARK_LAYOUT)
            fig_m.update_layout(
                height=420, margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(title="Preço (USDT)"), xaxis=dict(title="Data / Hora (UTC)"),
            )
            st.plotly_chart(fig_m, use_container_width=True)
            if not m_df["donchian_entry_upper"].notna().any() and not m_df["donchian_exit_lower"].notna().any():
                st.caption(
                    f"Ainda sem histórico suficiente ({len(m_df)}h disponíveis) pra desenhar os canais Donchian "
                    f"completos ({N_EXIT_HOURS}h de saída / {N_ENTRY_HOURS}h de entrada) -- os canais aparecem "
                    "assim que houver dados suficientes, nunca são estimados com dado parcial."
                )
        else:
            st.info(f"Sem dados de mercado gravados para {selected_sym}.")
    else:
        st.info("Universo de ativos indisponível no checkpoint atual.")
# ---------------------------------------------------------------------------
# ABA 5 -- Vender (fechamento manual/discricionário)
# ---------------------------------------------------------------------------
with tab_sell:
    st.subheader("🔴 Venda manual (fechamento discricionário)")
    st.caption(
        "Fecha uma posição aberta ANTES do critério técnico do motor (saída Donchian de 10d ou stop de 2×ATR). "
        "Isso quebra a disciplina 100% sistemática do BOB para aquele símbolo -- use com cautela (posições "
        "vencedoras às vezes continuam subindo por muito tempo antes de sair pelo critério técnico). O pedido "
        "fica pendente aqui e é executado pelo robô local (_bob_cloud_live_step.py) na próxima vez que ele "
        "rodar -- o motor congelado (entrada/saída/stop/disjuntor) nunca é alterado, ele só reaproveita a "
        "mesma função de fechamento (_close_position) que já usa pras saídas técnicas."
    )
    positions_now = state.get("positions", {}) if state else {}
    open_now = {sym: p for sym, p in positions_now.items() if p.get("in_position")}
    if not open_now:
        st.info("Nenhuma posição aberta no momento pra vender.")
    else:
        current_prices_sell = load_current_prices(LIVE_SIM_ID, list(open_now.keys()))
        sell_rows = []
        for sym, p in open_now.items():
            entry = p.get("entry_price")
            atual = current_prices_sell.get(sym)
            pnl = ((atual - entry) / entry * 100.0) if (entry and atual is not None) else None
            sell_rows.append({
                "símbolo": sym, "preço_entrada": entry, "preço_atual": atual,
                "p&l_não_realizado_%": round(pnl, 2) if pnl is not None else None,
                "capital_alocado_usd": p.get("allocated_capital_usd"),
            })
        sell_df = pd.DataFrame(sell_rows).sort_values("símbolo").reset_index(drop=True)
        st.dataframe(
            sell_df, use_container_width=True, hide_index=True,
            column_config={
                "símbolo": st.column_config.TextColumn("Símbolo"),
                "preço_entrada": st.column_config.NumberColumn("Preço entrada", format="%.6g"),
                "preço_atual": st.column_config.NumberColumn("Preço atual", format="%.6g"),
                "p&l_não_realizado_%": st.column_config.NumberColumn("P&L não realizado (%)", format="%+.2f%%"),
                "capital_alocado_usd": st.column_config.NumberColumn("Capital alocado", format="dollar"),
            },
        )
        pending_df_now = load_manual_close_requests(LIVE_SIM_ID)
        pending_symbols = set(pending_df_now.loc[pending_df_now["status"] == "pending", "symbol"]) if not pending_df_now.empty else set()
        sellable_symbols = [s for s in sell_df["símbolo"] if s not in pending_symbols]
        if pending_symbols:
            st.caption(f"Já com pedido pendente (não aparecem na lista pra vender abaixo): {', '.join(sorted(pending_symbols))}.")
        st.write("")
        if "sell_armed_symbol" not in st.session_state:
            st.session_state.sell_armed_symbol = None
        if not sellable_symbols:
            st.caption("Todas as posições abertas já têm um pedido de venda manual pendente.")
        else:
            chosen = st.selectbox("Símbolo para vender", options=sellable_symbols)
            if st.session_state.sell_armed_symbol != chosen:
                if st.button(f"🔴 Vender {chosen} agora", type="primary"):
                    st.session_state.sell_armed_symbol = chosen
                    st.rerun()
            else:
                row_sel = sell_df[sell_df["símbolo"] == chosen].iloc[0]
                pnl_val = row_sel["p&l_não_realizado_%"]
                pnl_label = "sem dado" if pd.isna(pnl_val) else f"{pnl_val:+.2f}%"
                st.warning(
                    f"Confirma o fechamento MANUAL de {chosen}? Preço de entrada ${row_sel['preço_entrada']:.6g}, "
                    f"preço atual ${row_sel['preço_atual']:.6g} ({pnl_label}). Isso encerra a posição na próxima "
                    "execução do robô local, fora do critério técnico do motor."
                )
                c1, c2 = st.columns(2)
                if c1.button("CONFIRMAR VENDA", type="primary", use_container_width=True):
                    created = request_manual_close(LIVE_SIM_ID, chosen, requested_by="dashboard")
                    st.session_state.sell_armed_symbol = None
                    if created:
                        st.success(f"Pedido de venda manual de {chosen} enfileirado -- será executado na próxima rodada do robô local.")
                    else:
                        st.info(f"Já existia um pedido pendente pra {chosen} -- nenhum pedido duplicado foi criado.")
                    st.rerun()
                if c2.button("Cancelar", use_container_width=True):
                    st.session_state.sell_armed_symbol = None
                    st.rerun()
    st.divider()
    st.markdown("**Histórico de pedidos de venda manual**")
    hist_df = load_manual_close_requests(LIVE_SIM_ID, limit=50)
    if hist_df.empty:
        st.caption("Nenhum pedido de venda manual foi feito ainda.")
    else:
        st.dataframe(
            hist_df, use_container_width=True, hide_index=True,
            column_config={
                "id": None,
                "symbol": st.column_config.TextColumn("Símbolo"),
                "status": st.column_config.TextColumn("Status"),
                "requested_at": st.column_config.DatetimeColumn("Pedido em", format="YYYY-MM-DD HH:mm:ss"),
                "requested_by": st.column_config.TextColumn("Pedido por"),
                "executed_at": st.column_config.DatetimeColumn("Executado em", format="YYYY-MM-DD HH:mm:ss"),
                "execution_note": st.column_config.TextColumn("Nota de execução"),
            },
        )
# ---------------------------------------------------------------------------
# Rodapé -- MISSAO 20 item medio #12: horário de quando o painel foi
# renderizado (relógio real do servidor, não o simulated_time do bot) --
# ajuda a distinguir "o painel travou" de "o painel atualizou mas o bot
# não tem checkpoint novo ainda" (esse 2o caso já fica claro pelo badge
# de idade do dado no topo, Missão 19 item crítico #2).
# ---------------------------------------------------------------------------
st.divider()
st.caption(f"Última atualização do painel: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
# Auto-refresh logic se ativado
if auto:
    time.sleep(30)
    st.rerun()
