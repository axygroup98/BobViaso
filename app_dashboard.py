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

Uso local:
  pip install streamlit psycopg2-binary pandas plotly
  streamlit run app_dashboard.py

Deploy: ver instrucoes de Missao 13 (GitHub + Streamlit Community Cloud).
Host de nuvem: aceita tanto o host direto do Supabase quanto o pooler
Supavisor (ver ALLOWED_CLOUD_HOSTS) -- o pooler e' o que de fato funciona
a partir do Streamlit Community Cloud (rede sem saida IPv6).
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
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402

# espelha donchian_engine.py (N_ENTRY_HOURS=480/20d, N_EXIT_HOURS=240/10d)
# -- valores HARDCODED de proposito (nao importados) pra este dashboard
# nao depender de nenhum arquivo do motor -- o repositorio de deploy fica
# so' com este arquivo + requirements.txt, nada do "lab/engine" precisa
# ser publicado. Sao so' constantes de VISUALIZACAO (o motor de verdade,
# que decide de fato, roda no PC/via _bob_cloud_live_step.py e nunca le'
# nada daqui). Se donchian_engine.py mudar essas constantes um dia (o que
# quebraria o hash congelado -- exigiria decisao explicita), atualizar
# aqui tambem.
N_ENTRY_HOURS = 480
N_EXIT_HOURS = 240

ALLOWED_CLOUD_HOSTS = {
    "db.dutmxvqluuxfexbdfbxm.supabase.co",   # conexao direta (IPv6 -- pode falhar em redes sem saida IPv6, ex. Streamlit Cloud)
    "aws-0-sa-east-1.pooler.supabase.com",   # Supavisor pooler (IPv4-compativel), mesmo projeto "bob-paper-live"
}  # ambos SEMPRE do mesmo projeto dedicado "bob-paper-live" (dutmxvqluuxfexbdfbxm) -- nunca outro projeto/host


def _minimal_cloud_safety_check(cfg):
    """Checagem FAIL CLOSED, autocontida (sem importar environment_guard.py
    -- ver docstring do topo). So' usada no caminho Streamlit Cloud."""
    host = str(cfg.get("DB_HOST", "")).strip().lower()
    if host not in ALLOWED_CLOUD_HOSTS:
        st.error(f"BLOQUEADO: DB_HOST='{host}' nao e' um host autorizado do projeto Supabase 'bob-paper-live' ({sorted(ALLOWED_CLOUD_HOSTS)}).")
        st.stop()
    if str(cfg.get("DRY_RUN", "")).strip().lower() != "true":
        st.error("BLOQUEADO: DRY_RUN precisa ser 'true' -- nunca deve rodar sem isso.")
        st.stop()


def load_cloud_config():
    """Caminho 1 (local): .env.cloud + guard completo, reusado sem
    alteracao. Caminho 2 (Streamlit Cloud): st.secrets + checagem minima
    acima. Detecta automaticamente qual esta' disponivel."""
    local_env_path = r"C:\Users\junio\BOT_LAB\config\.env.cloud"
    if os.path.exists(local_env_path):
        sys.path.insert(0, r"C:\Users\junio\BOT_LAB\config")
        from environment_guard import load_env_file, assert_cloud_paper_live_safe  # noqa: E402
        cfg = load_env_file(local_env_path)
        assert_cloud_paper_live_safe(cfg)  # guard completo, mesmo de sempre
        return cfg
    # sem o arquivo local -> assume Streamlit Community Cloud (st.secrets)
    try:
        cfg = dict(st.secrets)
    except Exception:
        st.error("Nenhuma configuracao encontrada: nem .env.cloud local, nem st.secrets no Streamlit Cloud.")
        st.stop()
        return {}
    _minimal_cloud_safety_check(cfg)
    return cfg


cloud_cfg = load_cloud_config()  # PRIMEIRA validacao real, antes de qualquer conexao.

SIM_IDS = {
    "MISSION10_BOB_PAPER_LIVE_20USD_001": "Missao 10 -- Paper trading AO VIVO (ativo)",
    "MISSION9_BOB_BOB_EDGE_MILD_RECAL_20USD_001": "Missao 9 -- Congelada (referencia)",
}
LIVE_SIM_ID = "MISSION10_BOB_PAPER_LIVE_20USD_001"

st.set_page_config(page_title="BOB -- Painel Espelho", page_icon="📈", layout="wide")


# ---------------------------------------------------------------------------
# MISSAO 14 -- estilo visual (so' CSS/HTML, nao mexe em nenhum dado/logica)
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

    /* cards de metrica */
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

    /* pilula de status */
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

    /* badge pequeno (estado do monitor de edge, etc.) */
    .bob-badge { display:inline-block; padding: 3px 11px; border-radius: 999px; font-size: 0.78rem; font-weight: 700; letter-spacing:.02em; }
    .bob-badge-ok { background: rgba(46,204,113,0.14); color:#2ecc71; border:1px solid rgba(46,204,113,0.3); }
    .bob-badge-warn { background: rgba(243,156,18,0.14); color:#f39c12; border:1px solid rgba(243,156,18,0.3); }
    .bob-badge-bad { background: rgba(231,76,60,0.14); color:#e74c3c; border:1px solid rgba(231,76,60,0.3); }
    .bob-badge-neutral { background: rgba(149,165,166,0.14); color:#95a5a6; border:1px solid rgba(149,165,166,0.3); }

    /* cabecalho */
    .bob-header { display:flex; align-items:center; gap:16px; margin-bottom: 6px; }
    .bob-header-title { font-size: 1.65rem; font-weight: 800; letter-spacing:-0.02em; color:#f2f4f8; }
    .bob-header-sub { color:#8b93a7; font-size: 0.85rem; margin-top: 2px; }

    /* tabelas */
    div[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; border: 1px solid rgba(255,255,255,0.08); }

    /* botoes */
    .stButton>button { border-radius: 10px; font-weight: 700; border: none; }

    /* abas */
    button[data-baseweb="tab"] { font-weight: 700; font-size: 0.92rem; }
    div[data-baseweb="tab-highlight"] { background-color: #2ecc71 !important; }

    /* caption */
    .stCaption, [data-testid="stCaptionContainer"] { color: #6f7891 !important; }
    </style>
    """, unsafe_allow_html=True)


_inject_custom_css()


def _require_passcode():
    """MISSAO 13 -- gate de senha. O Streamlit Community Cloud gratuito so'
    oferece deploy PUBLICO (privado exige trial pago via Snowflake) -- ou
    seja, qualquer pessoa com o link acessaria o kill switch se nada
    barrasse isso. So' entra em vigor quando DASHBOARD_PASSCODE existe na
    config (ou seja, so' na nuvem, definido nas Secrets do Streamlit Cloud
    -- o uso LOCAL no PC continua sem pedir nada, sem fricção)."""
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
    """Conexao NOVA por chamada (mais simples/robusto num painel Streamlit
    do que manter 1 conexao viva entre reruns -- evita conexao expirada
    apos idle), SEMPRE fechada no final (with ... as conn so' cuida da
    transacao, nao fecha sozinho -- por isso o try/finally explicito
    aqui). Sempre pro projeto de nuvem ja validado acima."""
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
def load_trade_stats(sim_id):
    """MISSAO 15 -- estatisticas de trades RESOLVIDOS (ja fechados), lidas
    direto de mission7_experiences (tabela ja existente, escrita pelo
    proprio motor via mission7_run.insert_experiences/apply_resolved --
    NENHUMA query nova de escrita, so' leitura). Enquanto nao houver trades
    fechados na nuvem (normal logo apos uma migracao enxuta, ou quando
    ainda nao passou tempo real suficiente pra' fechar uma epoca), volta
    vazio -- o painel mostra isso com clareza, nunca inventa numero."""
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
    """Historico do estado do monitor de edge (NORMAL/ATENCAO/...) por
    epoca, extraido do mesmo jsonb de checkpoints ja usado em
    load_equity_history -- so' mais um campo do mesmo state, sem query
    nova de escrita."""
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


# ---------------------------------------------------------------------------
# MISSAO 14 -- pequenos helpers de apresentacao (HTML/CSS puro, sem logica de negocio)
# ---------------------------------------------------------------------------
def _status_pill(is_paused):
    if is_paused:
        return "<span class='bob-pill bob-pill-paused'><span class='bob-pill-dot'></span>PAUSADO</span>"
    return "<span class='bob-pill bob-pill-live'><span class='bob-pill-dot'></span>ATIVO</span>"


def _edge_badge(current_state):
    state = (current_state or "—").upper()
    cls = {"NORMAL": "bob-badge-ok", "ATENCAO": "bob-badge-warn", "ATENÇÃO": "bob-badge-warn"}.get(state, "bob-badge-bad" if state not in ("—",) else "bob-badge-neutral")
    return f"<span class='bob-badge {cls}'>{state}</span>"


EDGE_STATE_COLOR = {"NORMAL": "#2ecc71", "ATENCAO": "#f39c12", "DEFENSIVO": "#e74c3c", "PAUSA": "#e74c3c", "DESCONHECIDO": "#95a5a6"}

PLOTLY_DARK_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color="#c7cedd", family="Inter, sans-serif"),
    legend=dict(bgcolor="rgba(0,0,0,0)"),
    xaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False),
    yaxis=dict(gridcolor="rgba(255,255,255,0.06)", zeroline=False),
)

# ---------------------------------------------------------------------------
# Sidebar -- kill switch + controles
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
                            "Posicoes ja abertas continuam sendo geridas normalmente (saida/stop/disjuntor intocados).")
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
# Corpo principal
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
        st.subheader("Posições em andamento")
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

            bar = go.Figure(go.Bar(
                x=pos_df["capital_alocado_usd"], y=pos_df["símbolo"], orientation="h",
                marker=dict(color="#3498db"),
                hovertemplate="%{y}: $%{x:,.2f}<extra></extra>",
            ))
            bar.update_layout(**PLOTLY_DARK_LAYOUT)
            bar.update_layout(
                height=300, margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(autorange="reversed", gridcolor="rgba(255,255,255,0.06)"),
                xaxis=dict(title="USD alocado", gridcolor="rgba(255,255,255,0.06)"),
            )
            st.plotly_chart(bar, use_container_width=True)
            st.dataframe(pos_df, use_container_width=True, hide_index=True)
        else:
            st.caption("Nenhuma posição aberta no momento.")

# ---------------------------------------------------------------------------
# ABA 2 -- Performance & Risco
# ---------------------------------------------------------------------------
with tab_performance:
    eq_df = load_equity_history(LIVE_SIM_ID)
    if eq_df.empty or len(eq_df) < 2:
        st.info("Só existe 1 checkpoint na nuvem até agora (migração enxuta trouxe só o último). "
                "A curva cresce a cada época real processada por _bob_cloud_live_step.py -- volte depois de alguns dias.")
        if not eq_df.empty:
            st.dataframe(eq_df, use_container_width=True, hide_index=True)
    else:
        eq_df = eq_df.copy()
        eq_df["drawdown_pct"] = (eq_df["equity_total"] - eq_df["peak_equity"]) / eq_df["peak_equity"] * 100.0

        st.subheader("Curva de capital ao longo das épocas")
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=eq_df["epoch_index"], y=eq_df["equity_total"], name="Capital total",
            line=dict(color="#2ecc71", width=2), fill="tozeroy", fillcolor="rgba(46,204,113,0.08)",
            customdata=eq_df["simulated_time"].astype(str),
            hovertemplate="Época %{x} (%{customdata})<br>Capital: $%{y:,.2f}<extra></extra>",
        ))
        fig.add_trace(go.Scatter(
            x=eq_df["epoch_index"], y=eq_df["peak_equity"], name="Pico histórico",
            line=dict(color="#95a5a6", dash="dot", width=1.5),
            hovertemplate="Pico: $%{y:,.2f}<extra></extra>",
        ))
        fig.update_layout(**PLOTLY_DARK_LAYOUT)
        fig.update_layout(xaxis_title="Época", yaxis_title="USD", height=380, margin=dict(l=10, r=10, t=20, b=10), hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Drawdown (distância do pico histórico)")
        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=eq_df["epoch_index"], y=eq_df["drawdown_pct"], name="Drawdown %",
            line=dict(color="#e74c3c", width=1.5), fill="tozeroy", fillcolor="rgba(231,76,60,0.12)",
        ))
        fig_dd.add_hline(y=-10, line=dict(color="#e74c3c", dash="dash", width=1), annotation_text="disjuntor -10%", annotation_font_color="#e74c3c")
        fig_dd.update_layout(**PLOTLY_DARK_LAYOUT)
        fig_dd.update_layout(xaxis_title="Época", yaxis_title="% do pico", height=240, margin=dict(l=10, r=10, t=20, b=10))
        st.plotly_chart(fig_dd, use_container_width=True)
        st.caption("Linha tracejada mostra o limite do disjuntor de segurança (MAX_CUM_LOSS_PAUSE_PCT=10%) -- "
                   "só para referência visual, o disjuntor de verdade roda dentro do motor, intocado.")

    st.subheader("Monitor de edge ao longo das épocas")
    edge_df = load_edge_monitor_history(LIVE_SIM_ID)
    if edge_df.empty or len(edge_df) < 2:
        st.info("Ainda não há histórico suficiente do monitor de edge (precisa de mais de 1 checkpoint). "
                "Estado atual, no último checkpoint: " + (sizing_state.get("current_state") or "—"))
    else:
        edge_df = edge_df.copy()
        state_order = {"NORMAL": 0, "ATENCAO": 1, "DEFENSIVO": 2, "PAUSA": 3, "DESCONHECIDO": -1}
        edge_df["nivel"] = edge_df["edge_state"].map(state_order).fillna(-1)
        colors = [EDGE_STATE_COLOR.get(s, "#95a5a6") for s in edge_df["edge_state"]]
        fig_edge = go.Figure(go.Scatter(
            x=edge_df["epoch_index"], y=edge_df["nivel"], mode="lines+markers",
            line=dict(color="#3498db", width=1.5, shape="hv"),
            marker=dict(color=colors, size=8, line=dict(color="#0b0f1a", width=1)),
            text=edge_df["edge_state"], hovertemplate="Época %{x}: %{text}<extra></extra>",
        ))
        fig_edge.update_layout(**PLOTLY_DARK_LAYOUT)
        fig_edge.update_layout(
            height=200, margin=dict(l=10, r=10, t=10, b=10), xaxis_title="Época",
            yaxis=dict(tickmode="array", tickvals=[0, 1, 2, 3], ticktext=["NORMAL", "ATENÇÃO", "DEFENSIVO", "PAUSA"],
                       gridcolor="rgba(255,255,255,0.06)"),
        )
        st.plotly_chart(fig_edge, use_container_width=True)

# ---------------------------------------------------------------------------
# ABA 3 -- Trades (MISSAO 15)
# ---------------------------------------------------------------------------
with tab_trades:
    st.subheader("Estatísticas de trades fechados")
    trades_df = load_trade_stats(LIVE_SIM_ID)

    if trades_df.empty:
        st.info(
            "Ainda não há trades fechados registrados na nuvem para esta simulação -- normal logo após a migração "
            "enxuta (Missão 11), ou enquanto o tempo real ainda não completou uma época nova (24h desde o último "
            "checkpoint). Assim que o motor fechar a primeira posição em uma época real, as estatísticas abaixo "
            "aparecem automaticamente, calculadas a partir de mission7_experiences -- nenhum número é estimado ou "
            "inventado enquanto não houver trade de verdade."
        )
    else:
        n_total = len(trades_df)
        wins = trades_df[trades_df["pnl_percent"] > 0]
        losses = trades_df[trades_df["pnl_percent"] <= 0]
        win_rate = len(wins) / n_total * 100 if n_total else 0.0
        gross_win = wins["pnl_percent"].sum()
        gross_loss = abs(losses["pnl_percent"].sum())
        profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf") if gross_win > 0 else 0.0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Trades fechados", n_total)
        c2.metric("Taxa de acerto", f"{win_rate:.1f}%")
        c3.metric("Fator de lucro", "∞" if profit_factor == float("inf") else f"{profit_factor:.2f}")
        c4.metric("PnL médio por trade", f"{trades_df['pnl_percent'].mean():.2f}%")

        st.caption("Taxa de acerto = trades com pnl_percent > 0 / total. Fator de lucro = soma dos ganhos ÷ soma "
                   "absoluta das perdas (em pnl_percent) -- ambos calculados só sobre trades já RESOLVIDOS "
                   "(coluna `resolved=true` em mission7_experiences).")

        st.write("")
        st.subheader("Histórico de trades (mais recentes primeiro)")
        st.dataframe(trades_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------------------------
# ABA 4 -- Mercado & Donchian
# ---------------------------------------------------------------------------
with tab_market:
    st.subheader("Preço e canais de Donchian (dados ao vivo, por símbolo)")
    if state:
        symbols = sorted(state.get("price_history", {}).keys())
    else:
        symbols = []
    if symbols:
        symbol = st.selectbox("Símbolo", symbols, index=symbols.index("BTCUSDT") if "BTCUSDT" in symbols else 0)
        hist = state["price_history"].get(symbol, [])
        hist_df = pd.DataFrame(hist, columns=["open_time", "price"])
        if not hist_df.empty:
            hist_df["ts"] = pd.to_datetime(hist_df["open_time"], unit="ms", utc=True)
            entry_window = max(1, N_ENTRY_HOURS // 24)  # HOUR_MS/epoch=24h -> 1 ponto por dia no price_history
            exit_window = max(1, N_EXIT_HOURS // 24)
            hist_df["canal_entrada_max"] = hist_df["price"].rolling(entry_window, min_periods=entry_window).max()
            hist_df["canal_entrada_min"] = hist_df["price"].rolling(entry_window, min_periods=entry_window).min()
            hist_df["canal_saida_max"] = hist_df["price"].rolling(exit_window, min_periods=exit_window).max()
            hist_df["canal_saida_min"] = hist_df["price"].rolling(exit_window, min_periods=exit_window).min()

            fig2 = go.Figure()
            fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["canal_entrada_max"], name=f"Donchian entrada ({N_ENTRY_HOURS//24}d) topo",
                                       line=dict(color="rgba(231,76,60,0.55)", dash="dash", width=1)))
            fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["canal_entrada_min"], name=f"Donchian entrada ({N_ENTRY_HOURS//24}d) fundo",
                                       line=dict(color="rgba(231,76,60,0.55)", dash="dash", width=1),
                                       fill="tonexty", fillcolor="rgba(231,76,60,0.05)"))
            fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["canal_saida_max"], name=f"Donchian saída ({N_EXIT_HOURS//24}d) topo",
                                       line=dict(color="rgba(243,156,18,0.65)", dash="dot", width=1)))
            fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["canal_saida_min"], name=f"Donchian saída ({N_EXIT_HOURS//24}d) fundo",
                                       line=dict(color="rgba(243,156,18,0.65)", dash="dot", width=1)))
            fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["price"], name="Preço", line=dict(color="#3498db", width=2.2)))
            fig2.update_layout(**PLOTLY_DARK_LAYOUT)
            fig2.update_layout(height=460, margin=dict(l=10, r=10, t=20, b=10))
            st.plotly_chart(fig2, use_container_width=True)
            st.caption("Canais calculados aqui só para visualização (aproximação com pandas.rolling sobre o "
                       "price_history do último checkpoint) -- o motor de verdade usa donchian_engine._rolling_extreme, "
                       "intocado, para decidir. Com poucos dias de histórico ainda no ar, os canais completos "
                       "(20d/10d) só aparecem depois que houver dados suficientes.")
        else:
            st.info("Sem histórico de preço para este símbolo ainda.")
    else:
        st.info("Sem checkpoint carregado ainda.")

st.divider()
st.caption(f"Última atualização do painel: {datetime.now(timezone.utc).isoformat()}")

if auto:
    time.sleep(30)
    st.rerun()
