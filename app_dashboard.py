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
    st.title("🔒 BOB -- Painel Espelho")
    pw = st.text_input("Senha de acesso", type="password")
    if st.button("Entrar", type="primary"):
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
# Sidebar -- kill switch + controles
# ---------------------------------------------------------------------------
st.sidebar.title("⚙️ Controle")
st.sidebar.caption("Projeto Supabase: bob-paper-live (dutmxvqluuxfexbdfbxm)")

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
st.title("📈 BOB -- Painel Espelho (Missao 12)")

sims = load_simulations()
live = sims.get(LIVE_SIM_ID)

if live is None:
    st.error(f"Simulacao {LIVE_SIM_ID} nao encontrada no Supabase -- rode a migracao (Missao 11) primeiro.")
    st.stop()

epoch_index, simulated_time, state = load_latest_checkpoint_state(LIVE_SIM_ID)

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Status", "🟢 ATIVO" if not is_paused else "🛑 PAUSADO")
col2.metric("Ultima epoca processada", epoch_index if epoch_index is not None else "—")
col3.metric("Ultima sincronizacao (sim_time)", str(simulated_time) if simulated_time else "—")
if state:
    cap = state.get("capital", {})
    equity_total = (cap.get("available", 0) or 0) + (cap.get("allocated", 0) or 0) + (cap.get("reserved", 0) or 0)
    col4.metric("Capital total (USD)", f"${equity_total:,.2f}")
    col5.metric("PnL realizado (USD)", f"${cap.get('realized_pnl', 0):,.2f}")

if state:
    cap = state.get("capital", {})
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Disponivel", f"${cap.get('available', 0):,.2f}")
    m2.metric("Alocado em posicoes", f"${cap.get('allocated', 0):,.2f}")
    m3.metric("Reservado", f"${cap.get('reserved', 0):,.2f}")
    sizing_state = cap.get("sizing_state", {})
    m4.metric("Estado do monitor de edge", sizing_state.get("current_state", "—"))

    positions = state.get("positions", {})
    open_positions = {sym: p for sym, p in positions.items() if p.get("in_position")}
    universe_size = len(state.get("price_history", {}))
    st.caption(f"Universo: {universe_size} simbolos · Posicoes abertas: {len(open_positions)}")

    if open_positions:
        st.subheader("Posicoes em andamento")
        pos_df = pd.DataFrame([
            {"simbolo": sym, "preco_entrada": p.get("entry_price"), "capital_alocado_usd": p.get("allocated_capital_usd"),
             "atr_pct_na_entrada": p.get("atr_pct_at_entry")}
            for sym, p in open_positions.items()
        ])
        st.dataframe(pos_df, use_container_width=True, hide_index=True)
    else:
        st.caption("Nenhuma posicao aberta no momento.")

st.divider()

st.subheader("Curva de capital ao longo das epocas")
eq_df = load_equity_history(LIVE_SIM_ID)
if eq_df.empty or len(eq_df) < 2:
    st.info("So' existe 1 checkpoint na nuvem ate' agora (migracao enxuta trouxe so' o ultimo). "
            "A curva cresce a cada rodada de _bob_cloud_live_step.py -- volte depois de alguns dias.")
    if not eq_df.empty:
        st.dataframe(eq_df, use_container_width=True, hide_index=True)
else:
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=eq_df["epoch_index"], y=eq_df["equity_total"], name="Capital total", line=dict(color="#2ecc71")))
    fig.add_trace(go.Scatter(x=eq_df["epoch_index"], y=eq_df["peak_equity"], name="Pico historico", line=dict(color="#95a5a6", dash="dot")))
    fig.update_layout(xaxis_title="Epoca", yaxis_title="USD", height=400, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)

st.divider()

st.subheader("Preco e canais de Donchian (dados ao vivo, por simbolo)")
if state:
    symbols = sorted(state.get("price_history", {}).keys())
else:
    symbols = []
if symbols:
    symbol = st.selectbox("Simbolo", symbols, index=symbols.index("BTCUSDT") if "BTCUSDT" in symbols else 0)
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
        fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["price"], name="Preco", line=dict(color="#3498db")))
        fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["canal_entrada_max"], name=f"Donchian entrada ({N_ENTRY_HOURS//24}d) topo", line=dict(color="#e74c3c", dash="dash")))
        fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["canal_entrada_min"], name=f"Donchian entrada ({N_ENTRY_HOURS//24}d) fundo", line=dict(color="#e74c3c", dash="dash")))
        fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["canal_saida_max"], name=f"Donchian saida ({N_EXIT_HOURS//24}d) topo", line=dict(color="#f39c12", dash="dot")))
        fig2.add_trace(go.Scatter(x=hist_df["ts"], y=hist_df["canal_saida_min"], name=f"Donchian saida ({N_EXIT_HOURS//24}d) fundo", line=dict(color="#f39c12", dash="dot")))
        fig2.update_layout(height=450, margin=dict(l=10, r=10, t=30, b=10))
        st.plotly_chart(fig2, use_container_width=True)
        st.caption("Canais calculados aqui so' pra visualizacao (aproximacao com pandas.rolling sobre o "
                   "price_history do ultimo checkpoint) -- o motor de verdade usa donchian_engine._rolling_extreme, "
                   "intocado, pra decidir. Com poucos dias de historico ainda no ar, os canais completos "
                   "(20d/10d) so' aparecem depois que houver dados suficientes.")
    else:
        st.info("Sem historico de preco pra este simbolo ainda.")
else:
    st.info("Sem checkpoint carregado ainda.")

st.caption(f"Ultima atualizacao do painel: {datetime.now(timezone.utc).isoformat()}")

if auto:
    time.sleep(30)
    st.rerun()
