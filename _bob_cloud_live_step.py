# -*- coding: utf-8 -*-
"""
MISSAO 11 -- passo MANUAL do paper trading ao vivo do BOB, rodando DIRETO
contra o projeto Supabase DEDICADO "bob-paper-live" (ref
dutmxvqluuxfexbdfbxm), em vez do Postgres local. Irmao direto de
_bob_paper_live_step.py (Missao 10) -- MESMA logica de busca/insercao de
candles publicos (REUSO DIRETO de _live_market_data.py, nada duplicado) --
a UNICA diferenca real e' QUAL banco recebe os dados e QUAL guard valida
o ambiente antes de conectar.

POR QUE UM SCRIPT NOVO EM VEZ DE SO' TROCAR O .env.local: mission7_run.py
(reusado sem alteracao por mission8_donchian_v1_sizing_v4_run.py, ambos
arquivos CONGELADOS/hasheados na Missao 9) carrega .env.local e monta a
conexao (get_pg_conn) na hora do IMPORT, de forma hardcoded -- nao aceita
parametro de conexao. Pra rodar contra a nuvem SEM editar nem um byte
desses arquivos congelados (o que quebraria os hashes registrados em
MISSAO9_BOB_CONGELAMENTO_OFICIAL.md), este script importa
mission8_donchian_v1_sizing_v4_run normalmente (reuso completo do motor,
0% duplicado) e so' REDIRECIONA, em runtime, a funcao get_pg_conn que ele
usa internamente pra' abrir conexao -- pra' uma versao que conecta na
nuvem, usando as credenciais de .env.cloud, validadas pelo guard novo
(assert_cloud_paper_live_safe). Nenhum arquivo em disco e' alterado.

(O import de mission7_run, por baixo dos panos, tambem vai imprimir
"environment_guard: OK (LOCAL_LAB confirmado)" -- isso e' so' o efeito
colateral do proprio import validando .env.local, como sempre fez; nao
significa que este script esta' conectando no banco local. A conexao
DE VERDADE usada por este script e' sempre a de nuvem, validada
separadamente logo abaixo por assert_cloud_paper_live_safe.)

NUNCA envia ordem real, nunca usa BINANCE_API_KEY/SECRET (so' endpoints
publicos), nunca importa placeBuy/placeSellAll/placeSellPartial.
environment_guard (guard de NUVEM) e' a PRIMEIRA validacao real antes de
qualquer conexao de nuvem ser aberta.

Uso (comeca pequeno, mesma convencao do laboratorio):
  python _bob_cloud_live_step.py --max-epochs 2
  python _bob_cloud_live_step.py --max-epochs 20      # depois de validar
  python _bob_cloud_live_step.py --fetch-only         # so' busca/insere dados

MISSAO 16 -- resiliencia da varredura Binance (aditivo, nenhuma logica de
entrada/saida/sizing/disjuntor tocada): investigacao de campo (2026-08-27)
mostrou que os candles de mission7_market_states pararam silenciosamente
as 2026-08-26 14:00 UTC por horas -- a epoca 1079 nao conseguia fechar
porque a janela de 24h nunca ficava completa. Causa raiz identificada no
codigo: fetch_and_insert_live_data() ja capturava LiveDataError por
simbolo (correto, isola falha de UM simbolo sem travar os outros 39), mas
sem nenhum alerta visivel nem retry -- uma instabilidade de rede
passageira com a Binance podia derrubar a rodada inteira (0 candles
novos, em TODOS os 40 simbolos) sem nenhum traceback, sem nenhuma linha
que chamasse atencao no meio da rolagem normal do terminal. Duas mudancas
resolvem isso, mantendo a MESMA arquitetura (motor congelado intocado,
so' endpoints publicos, sem chave, sem ordem real):
  1) fetch_public_klines (em _live_market_data.py, reuso direto, nao
     duplicado aqui) agora tenta de novo (default 3x, 2s de espera) antes
     de desistir de uma chamada -- e' avisado alto no terminal a cada
     tentativa, nunca falha em silencio.
  2) Este script agora CONTA quantos simbolos falharam de verdade (erro
     de rede/API, nao "sem candle novo ainda" -- que e' normal e
     esperado) e: (a) imprime um resumo bem visivel no final da fase de
     busca; (b) se TODOS os simbolos da rodada falharam por erro de rede
     -- ou seja, zero chance de haver dado novo -- ABORTA antes de
     chamar mission8_donchian_v1_sizing_v4_run.run(), porque chamar o
     motor de epocas nessas condicoes so' mascararia o problema real
     (pareceria "epoca nao fechou" quando na verdade e' "nem tentamos
     buscar dado nenhum com sucesso"); (c) se so' PARTE dos simbolos
     falhou, ou se nenhum candle novo apareceu mesmo sem erro de rede
     (caso normal: rodou cedo demais, ainda nao fechou candle novo),
     avisa claramente e ainda assim chama o motor normalmente -- que ja'
     sabe lidar com "sem dado novo" (nao fecha epoca, nao inventa nada).

MISSAO 17 -- venda manual via painel (dashboard). Fecha uma posicao aberta
a pedido explicito do usuario, ANTES do criterio tecnico do motor. Ver
_apply_manual_close/_process_manual_close_requests/
_load_latest_checkpoint_with_manual_closes mais abaixo.

MISSAO 21 -- CORRECAO CRITICA na Missao 17, encontrada em teste com dado
real (2026-08-27, pedido de venda manual de ZECUSDT): a versao original
gravava a linha em mission7_experiences e marcava o pedido como
'executed' IMEDIATAMENTE ao aplicar o fechamento em memoria -- mas o
fechamento em memoria so' se torna permanente se a PROXIMA epoca natural
realmente fechar e escrever um checkpoint novo (mission8_donchian_v1_
sizing_v4_run.run() so' faz `continue` sem escrever nada quando a janela
de 24h ainda nao tem nenhum candle -- ver linha "epoca N: nenhum candle
... pulando" no run() reusado). No teste real, a epoca seguinte NAO
fechou nesta rodada (so' 7 candles novos chegaram, nao os suficientes pra
completar a janela) -- o checkpoint continuou intacto (ZECUSDT ainda
in_position=true, mesmo capital alocado), mas mission7_experiences ja'
tinha uma linha dizendo que o trade fechou com +46.53%. Isso e' exatamente
o tipo de inconsistencia que a regra "nunca escondido/nunca corrompido"
proibe: o registro de trades diverge silenciosamente da fonte de verdade
(checkpoint). CORRECAO: separar em 2 fases -- (1) aplica em memoria e
GUARDA a informacao de fechamento num buffer, SEM gravar nada em
mission7_experiences nem mudar o status do pedido; (2) SO' DEPOIS que
mission8_donchian_v1_sizing_v4_run.run() retorna, confirma (consultando
mission7_checkpoints de novo) se um checkpoint com epoca MAIOR que a base
foi realmente escrito -- SO' ENTAO grava a experiencia e marca o pedido
'executed'. Se nenhuma epoca fechou nesta rodada, o pedido continua
'pending' (nada e' gravado) e e' automaticamente retentado, sem duplicar
nada, na proxima vez que o script rodar. Os casos que NAO mutam state
(sem posicao aberta / sem candle) continuam sendo finalizados na hora,
porque nesses dois casos nao ha nada pendente de persistencia.
"""
import sys
import os
import time
import argparse

# Caminho local generico (funciona pra qualquer usuario/maquina) -- pode
# ser sobrescrito via env var BOB_LOCAL_CONFIG_DIR se o BOT_LAB estiver em
# outro lugar. Antes disso o caminho vinha fixo com a pasta pessoal do
# usuario, exposta sem necessidade no repositorio publico.
_LOCAL_CONFIG_DIR = os.environ.get(
    "BOB_LOCAL_CONFIG_DIR",
    os.path.join(os.path.expanduser("~"), "BOT_LAB", "config"),
)
sys.path.insert(0, _LOCAL_CONFIG_DIR)
from environment_guard import load_env_file, assert_cloud_paper_live_safe  # noqa: E402

CLOUD_ENV_PATH = os.path.join(_LOCAL_CONFIG_DIR, ".env.cloud")
cloud_cfg = load_env_file(CLOUD_ENV_PATH)
assert_cloud_paper_live_safe(cloud_cfg)
print("environment_guard: OK (CLOUD_PAPER_LIVE confirmado -- projeto Supabase 'bob-paper-live')")

import psycopg2  # noqa: E402
import psycopg2.extras  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))

from _live_market_data import (  # noqa: E402 -- REUSO, nao duplicado
    fetch_public_klines, compute_atr_pct_series, compute_adx_series, LiveDataError, HOUR_MS, WILDER_PERIOD,
)
import mission8_donchian_v1_sizing_v4_run as m8run  # noqa: E402 -- REUSO COMPLETO, nenhum byte alterado

# MISSAO 17 -- venda manual via painel (ver bloco de monkeypatch de
# load_latest_checkpoint mais abaixo). So' REUSO -- nenhuma funcao nova de
# calculo de PnL/reward/disjuntor e' inventada aqui, todas vem direto dos
# mesmos modulos puros que o motor congelado ja usa.
import copy  # noqa: E402
from dataclasses import asdict  # noqa: E402
import mission7_run  # noqa: E402 -- ja' carregado em cache pelo import de m8run acima; so' expoe o nome aqui
from donchian_engine import _close_position  # noqa: E402 -- MESMA funcao que o motor usa pra fechar posicoes
from reward_engine import compute_reward  # noqa: E402 -- MESMA formula de recompensa do motor
from risk_engine import MAX_CUM_LOSS_PAUSE_PCT  # noqa: E402 -- MESMO limiar do disjuntor (nunca enfraquecido)

# MISSAO 12 -- kill switch do dashboard. Le' mission7_bot_control.paused
# (tabela NOVA, aditiva, criada so' pra isso -- nenhuma das 8 tabelas
# mission7_* originais foi tocada). QUANDO PAUSADO: a UNICA coisa que
# muda e' o sizing_fn do modelo, que passa a devolver 0.0 pra QUALQUER
# entrada nova -- EXATAMENTE o mesmo mecanismo que make_recovery_ladder_
# sizing_fn ja usa hoje pra bloquear entrada quando n_open>=max_positions
# (ver sizing_models_v4.py). O motor de entrada/saida/stop/disjuntor
# (donchian_v1_sizing_lab_v4.py) NAO E' TOCADO -- posicoes ja abertas
# continuam sendo fechadas pelos MESMOS criterios tecnicos de sempre
# (Donchian de saida, stop ATR, disjuntor de risco), nunca de forma
# abrupta. reserve_fn e sizing_update_fn tambem ficam intocados -- so' o
# "portao de entrada" fecha.
SIM_ID = "MISSION10_BOB_PAPER_LIVE_20USD_001"
MODEL_KEY = "BOB_EDGE_MILD_RECAL"
CONTEXT_BARS = WILDER_PERIOD * 3
KLINES_PAGE_LIMIT = 1000
# MESMO valor passado a capital_per_position_usd em m8run.run() no final
# deste arquivo -- constante unica pra nunca divergir do ctx["base_stake_usd"]
# usado pela venda manual (Missao 17) mais abaixo.
CAPITAL_PER_POSITION_USD_LIVE = 20.0

# MISSAO 21 -- buffer module-level usado SO' dentro de uma unica execucao
# deste script (processo novo a cada `python _bob_cloud_live_step.py ...`,
# entao comeca sempre vazio -- nao ha estado "sujo" entre rodadas). Guarda
# fechamentos manuais aplicados em memoria por
# _load_latest_checkpoint_with_manual_closes, que so' viram gravacao real
# em mission7_experiences/mission7_manual_close_requests se
# _finalize_manual_closes_if_persisted (chamada em main(), DEPOIS de
# m8run.run() retornar) confirmar que um checkpoint novo foi escrito.
_PENDING_MANUAL_CLOSE_FINALIZATIONS = []
_PENDING_MANUAL_CLOSE_BASE_EPOCH = None


def _cloud_pg_conn():
    """Unica funcao NOVA deste script: conexao de nuvem, construida a
    partir de .env.cloud (ja validado acima por assert_cloud_paper_live_safe).
    Usada tanto pela fase de busca/insercao de dados quanto -- via
    monkeypatch logo abaixo -- pelo motor reusado."""
    return psycopg2.connect(
        host=cloud_cfg["DB_HOST"], port=cloud_cfg.get("DB_PORT", 5432),
        user=cloud_cfg["DB_USER"], password=cloud_cfg["DB_PASSWORD"], dbname=cloud_cfg["DB_NAME"],
    )


# redireciona SO' EM RUNTIME (nenhum arquivo em disco tocado) a conexao que
# mission8_donchian_v1_sizing_v4_run.run() abre internamente -- ver docstring acima.
m8run.get_pg_conn = _cloud_pg_conn


def _latest_market_row(conn, simulation_id, symbol):
    """Ultimo candle conhecido (mission7_market_states) pra este simbolo --
    MESMA fonte de preco que o motor usaria pra decidir a proxima epoca;
    nunca busca um tick novo a parte (evitaria usar um preco de um
    instante que o motor em si ainda nem "viu")."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "select open_time, ts, price, market_regime from mission7_market_states "
            "where simulation_id=%s and symbol=%s order by open_time desc limit 1",
            (simulation_id, symbol),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def _apply_manual_close(conn, simulation_id, epoch_index, state, model, fee_pct, symbol, request_id):
    """MISSAO 17 -- aplica em MEMORIA (no dict `state`, que sera' usado
    pela PROXIMA epoca natural) o fechamento MANUAL de uma posicao aberta,
    a pedido explicito do usuario via painel. Reaproveita _close_position
    (donchian_engine.py) -- a MESMA funcao pura que o motor congelado usa
    pra fechar posicoes por saida Donchian/stop-ATR -- e replica, passo a
    passo, o MESMO efeito colateral (contabilidade de capital, reserve_fn,
    sizing_update_fn, disjuntor de risco) que donchian_v1_sizing_lab_v4.
    run_one_epoch() aplica no bloco de saida, so' que disparado por pedido
    do usuario em vez de rompimento de canal/stop-ATR.

    MISSAO 21 -- devolve tipos DIFERENTES conforme o caso, de proposito:
      * sem posicao aberta / sem candle: NAO mutam `state`, entao sao
        seguros pra finalizar NA HORA (grava o status final direto aqui)
        -- devolve None (nada pendente).
      * fechamento aplicado com sucesso: MUTA `state`, mas isso so' vira
        permanente se um checkpoint novo for escrito depois -- NAO grava
        nada em mission7_experiences/mission7_manual_close_requests aqui;
        devolve um dict com tudo que _finalize_manual_closes_if_persisted
        precisa pra' gravar depois, SE E SO' SE confirmar a persistencia."""
    pos = state.get("positions", {}).get(symbol)
    if not pos or not pos.get("in_position"):
        with conn.cursor() as cur:
            cur.execute(
                "update mission7_manual_close_requests set status='skipped_no_position', executed_at=now(), "
                "execution_note=%s where id=%s",
                (f"sem posicao aberta em {symbol} no momento em que o pedido foi processado.", request_id),
            )
        conn.commit()
        print(f"  [venda manual] {symbol}: pedido #{request_id} pulado -- sem posicao aberta.")
        return None

    market_row = _latest_market_row(conn, simulation_id, symbol)
    if market_row is None:
        with conn.cursor() as cur:
            cur.execute(
                "update mission7_manual_close_requests set status='failed', executed_at=now(), "
                "execution_note=%s where id=%s",
                (f"nenhum candle encontrado em mission7_market_states pra {symbol}.", request_id),
            )
        conn.commit()
        print(f"  [venda manual] {symbol}: pedido #{request_id} FALHOU -- sem candle nenhum na nuvem.")
        return None

    t = int(market_row["open_time"])
    price = float(market_row["price"])
    entry_price = pos["entry_price"]

    # mesmos defaults defensivos que donchian_v1_sizing_lab_v4.run_one_epoch
    # aplica no topo -- garante que as mesmas chaves existem antes de
    # atualiza-las abaixo, sem mudar nenhum valor ja existente.
    state["capital"].setdefault("reserved", 0.0)
    state["capital"].setdefault("peak_equity", state["capital"]["available"] + state["capital"]["allocated"] + state["capital"]["realized_pnl"])
    state["capital"].setdefault("highest_equity", state["capital"]["available"] + state["capital"]["allocated"] + state["capital"]["realized_pnl"] + state["capital"]["reserved"])
    state["capital"].setdefault("highest_operational_equity", state["capital"]["available"] + state["capital"]["allocated"] + state["capital"]["realized_pnl"])
    state["capital"].setdefault("sizing_state", {})
    state.setdefault("global_stats", {})
    state["global_stats"].setdefault("trades_closed", 0)
    state["global_stats"].setdefault("wins", 0)
    state["global_stats"].setdefault("losses", 0)
    state["global_stats"].setdefault("risk_breaker_events", 0)

    change_pct = ((price - entry_price) / entry_price) * 100 if entry_price else 0.0
    holding_hours = (t - pos["position_opened_idx"]) / HOUR_MS
    pnl_pct_after_fees = change_pct - fee_pct * 2
    reward = compute_reward(pnl_pct_after_fees, 0, fee_pct * 2, 0, 100.0, holding_hours)
    trade_alloc_usd = pos.get("allocated_capital_usd", 0.0)

    experience = {
        "symbol": symbol, "market_state_open_time": t, "decision_sim_time": market_row["ts"],
        "epoch_index": epoch_index, "action": "SELL", "confidence": None,
        "policy_source": "fechamento_manual_via_painel", "market_regime": market_row.get("market_regime"),
        "entry_price": entry_price, "exit_price": price, "pnl_percent": pnl_pct_after_fees,
        "max_drawdown_pct": abs(min(0.0, change_pct)), "holding_time_hours": holding_hours,
        "reward": reward.reward_total, "reward_components": asdict(reward),
        "resolved": True, "resolution_sim_time": market_row["ts"], "created_at_simulated": market_row["ts"],
    }

    _close_position(state, pos, pnl_pct_after_fees, change_pct, t, fee_pct)
    trade_pnl_usd = trade_alloc_usd * (pnl_pct_after_fees / 100.0)

    # mesmas atualizacoes de pico (FASE 5/v3) que o motor faz logo apos
    # _close_position, incondicionais (independem de reserve_fn existir).
    total_equity_now_pre_reserve = state["capital"]["available"] + state["capital"]["allocated"] + state["capital"]["realized_pnl"] + state["capital"]["reserved"]
    operational_equity_now_pre_reserve = state["capital"]["available"] + state["capital"]["allocated"] + state["capital"]["realized_pnl"]
    state["capital"]["highest_equity"] = max(state["capital"]["highest_equity"], total_equity_now_pre_reserve)
    state["capital"]["highest_operational_equity"] = max(state["capital"]["highest_operational_equity"], operational_equity_now_pre_reserve)

    reserve_fn = model.get("reserve_fn")
    if reserve_fn is not None:
        real_bankroll_now = state["capital"]["available"] + state["capital"]["realized_pnl"]
        state["capital"]["peak_equity"] = max(
            state["capital"]["peak_equity"], real_bankroll_now + state["capital"]["allocated"] + state["capital"]["reserved"],
        )
        ctx = {"real_bankroll": real_bankroll_now, "reserved": state["capital"]["reserved"],
               "peak_equity": state["capital"]["peak_equity"], "base_stake_usd": CAPITAL_PER_POSITION_USD_LIVE,
               "symbol": symbol, "row": market_row}
        new_reserved_target = reserve_fn(state["capital"], trade_pnl_usd, ctx)
        delta_reserve = max(0.0, new_reserved_target - state["capital"]["reserved"])
        delta_reserve = min(delta_reserve, max(0.0, state["capital"]["realized_pnl"]))
        if delta_reserve > 0:
            state["capital"]["realized_pnl"] -= delta_reserve
            state["capital"]["reserved"] += delta_reserve

    sizing_update_fn = model.get("sizing_update_fn")
    if sizing_update_fn is not None:
        equity_now_post = state["capital"]["available"] + state["capital"]["allocated"] + state["capital"]["realized_pnl"] + state["capital"]["reserved"]
        operational_equity_now_post = state["capital"]["available"] + state["capital"]["allocated"] + state["capital"]["realized_pnl"]
        update_ctx = {"symbol": symbol, "row": market_row, "equity": equity_now_post,
                      "operational_equity": operational_equity_now_post,
                      "highest_equity": state["capital"]["highest_equity"],
                      "highest_operational_equity": state["capital"]["highest_operational_equity"]}
        sizing_update_fn(state["capital"], trade_pnl_usd, pnl_pct_after_fees, update_ctx)

    risk_breaker_row = None
    if pos["cum_realized_loss_pct"] >= MAX_CUM_LOSS_PAUSE_PCT and not pos["paused"]:
        pos["paused"] = True
        pos["paused_at_idx"] = t
        state["global_stats"]["risk_breaker_events"] += 1
        risk_breaker_row = {
            "symbol": symbol, "epoch_index": epoch_index, "threshold_pct": MAX_CUM_LOSS_PAUSE_PCT,
            "actual_pct": pos["cum_realized_loss_pct"], "event_sim_time": market_row["ts"],
            "simulation_continued": True, "drawdown_pct_at_event": 0,
            "position_size_at_event": trade_alloc_usd,
            "portfolio_state": {"capital": dict(state["capital"])},
            "detail": {"note": "disjuntor disparou (via fechamento manual pelo painel) -- este simbolo especifico "
                                "nao abre novas posicoes ate reativar; simulacao e demais simbolos continuam."},
        }

    print(f"  [venda manual] {symbol}: pedido #{request_id} aplicado EM MEMORIA (pnl_apos_taxas={pnl_pct_after_fees:.2f}%, "
          f"capital_liquidado=${trade_alloc_usd:.2f}) -- so' fica definitivo se um checkpoint novo for escrito "
          f"nesta rodada (ver _finalize_manual_closes_if_persisted).")
    return {
        "request_id": request_id, "symbol": symbol, "experience": experience,
        "risk_breaker_row": risk_breaker_row, "trade_alloc_usd": trade_alloc_usd,
        "pnl_pct_after_fees": pnl_pct_after_fees,
    }


def _process_manual_close_requests(conn, simulation_id, epoch_index, state):
    """Le' pedidos PENDENTES em mission7_manual_close_requests (tabela
    NOVA, aditiva) e aplica cada um, em memoria, no `state` que sera'
    usado pela PROXIMA epoca natural. Devolve a lista de fechamentos que
    ficaram PENDENTES DE CONFIRMACAO (ver _apply_manual_close/
    _finalize_manual_closes_if_persisted) -- os casos sem posicao/sem
    candle ja' se resolvem sozinhos dentro de _apply_manual_close."""
    with conn.cursor() as cur:
        cur.execute(
            "select id, symbol from mission7_manual_close_requests "
            "where simulation_id=%s and status='pending' order by requested_at asc",
            (simulation_id,),
        )
        pending = cur.fetchall()
    if not pending:
        return []
    model = dict(m8run.MODEL_REGISTRY_V4[MODEL_KEY])
    to_finalize = []
    for request_id, symbol in pending:
        result = _apply_manual_close(conn, simulation_id, epoch_index, state, model, m8run.FEE_PCT_DEFAULT, symbol, request_id)
        if result is not None:
            to_finalize.append(result)
    return to_finalize


# redireciona SO' EM RUNTIME (nenhum arquivo em disco tocado) o load do
# checkpoint mais recente que mission8_donchian_v1_sizing_v4_run.run() usa
# internamente -- MESMO padrao de monkeypatch ja usado acima pra
# get_pg_conn. Antes de devolver o (epoch_index, state) pro motor, aplica
# (em memoria, numa COPIA do state) qualquer venda manual pendente -- o
# efeito entra dentro do proprio checkpoint real que o motor vai escrever
# no final da PROXIMA epoca, sem inserir nenhuma linha de checkpoint extra
# e sem jamais alterar epoch_index (ver docstring do modulo pra' o porque
# disso ser CRITICO -- epoch_index -> janela de calendario e' deterministico).
_real_load_latest_checkpoint = mission7_run.load_latest_checkpoint


def _load_latest_checkpoint_with_manual_closes(conn, simulation_id):
    global _PENDING_MANUAL_CLOSE_FINALIZATIONS, _PENDING_MANUAL_CLOSE_BASE_EPOCH
    epoch_index, state = _real_load_latest_checkpoint(conn, simulation_id)
    if state is None:
        return epoch_index, state
    state = copy.deepcopy(state)
    to_finalize = _process_manual_close_requests(conn, simulation_id, epoch_index, state)
    if to_finalize:
        _PENDING_MANUAL_CLOSE_FINALIZATIONS = to_finalize
        _PENDING_MANUAL_CLOSE_BASE_EPOCH = epoch_index
        symbols = ", ".join(item["symbol"] for item in to_finalize)
        print(f"\n*** venda manual aplicada em memoria pra {symbols} (sim={simulation_id}, base=epoca {epoch_index}) "
              f"-- so' fica DEFINITIVA (experiencia gravada + pedido marcado 'executed') se um checkpoint novo "
              f"(epoca > {epoch_index}) for realmente escrito NESTA rodada; caso contrario o pedido continua "
              f"'pending' e e' retentado automaticamente na proxima rodada, sem duplicar nada. ***")
    return epoch_index, state


m8run.load_latest_checkpoint = _load_latest_checkpoint_with_manual_closes


def _finalize_manual_closes_if_persisted(simulation_id):
    """MISSAO 21 -- chamada em main() SO' DEPOIS que m8run.run() retorna.
    Confirma, consultando mission7_checkpoints de novo, se um checkpoint
    com epoca MAIOR que a base (a epoca que estava vigente quando os
    fechamentos manuais desta rodada foram aplicados em memoria) foi
    REALMENTE escrito. So' nesse caso grava mission7_experiences/
    mission7_risk_breaker_events e marca o pedido 'executed' -- caso
    contrario nao grava nada, e o pedido continua 'pending' (retentado
    sozinho na proxima rodada, sem duplicar)."""
    global _PENDING_MANUAL_CLOSE_FINALIZATIONS, _PENDING_MANUAL_CLOSE_BASE_EPOCH
    if not _PENDING_MANUAL_CLOSE_FINALIZATIONS:
        return
    pending = _PENDING_MANUAL_CLOSE_FINALIZATIONS
    base_epoch = _PENDING_MANUAL_CLOSE_BASE_EPOCH
    _PENDING_MANUAL_CLOSE_FINALIZATIONS = []
    _PENDING_MANUAL_CLOSE_BASE_EPOCH = None

    conn = _cloud_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("select max(epoch_index) from mission7_checkpoints where simulation_id=%s", (simulation_id,))
            latest_epoch = cur.fetchone()[0]
        if latest_epoch is None or latest_epoch <= base_epoch:
            symbols = ", ".join(item["symbol"] for item in pending)
            print(f"\n[venda manual] NENHUM checkpoint novo foi escrito nesta rodada (ainda na epoca {base_epoch}) -- "
                  f"pedido(s) de {symbols} continuam 'pending', SEM NADA gravado em mission7_experiences, e serao' "
                  f"retentados automaticamente na proxima rodada.")
            return
        for item in pending:
            mission7_run.insert_experiences(conn, simulation_id, [item["experience"]])
            if item["risk_breaker_row"] is not None:
                mission7_run.insert_rows(conn, "mission7_risk_breaker_events", [item["risk_breaker_row"]], simulation_id)
            with conn.cursor() as cur:
                cur.execute(
                    "update mission7_manual_close_requests set status='executed', executed_at=now(), execution_note=%s where id=%s",
                    (f"fechado manualmente: preco_saida={item['experience']['exit_price']:.8f}, "
                     f"pnl_apos_taxas={item['pnl_pct_after_fees']:.2f}%, capital_liquidado_usd={item['trade_alloc_usd']:.2f}. "
                     f"Confirmado persistido no checkpoint {latest_epoch} (base era {base_epoch}).",
                     item["request_id"]),
                )
            conn.commit()
            print(f"  [venda manual] {item['symbol']}: pedido #{item['request_id']} EXECUTADO e CONFIRMADO "
                  f"(checkpoint {latest_epoch} persistiu o fechamento).")
    finally:
        conn.close()


def _insert_rows_cloud(conn, table, rows, simulation_id):
    """Equivalente MINIMO de mission7_run.insert_rows -- reimplementado aqui
    (nao importado) so' porque mission7_run.py e' hardcoded pro Postgres
    LOCAL no import; a LOGICA e' identica (mesmo padrao de INSERT dinamico
    a partir das chaves do dict), nao inventa nada novo."""
    if not rows:
        return
    with conn.cursor() as cur:
        for r in rows:
            full = dict(r)
            full["simulation_id"] = simulation_id
            cols = list(full.keys())
            vals = [psycopg2.extras.Json(v) if isinstance(v, dict) else v for v in full.values()]
            placeholders = ", ".join(["%s"] * len(cols))
            cur.execute(f"insert into {table} ({', '.join(cols)}) values ({placeholders})", vals)


def _fetch_closed_klines(symbol, start_ms, now_ms):
    out = []
    cursor = start_ms
    while True:
        batch = fetch_public_klines(symbol, interval="1h", start_ms=cursor, limit=KLINES_PAGE_LIMIT)
        if not batch:
            break
        closed = [b for b in batch if b["close_time"] <= now_ms]
        out.extend(closed)
        if len(batch) < KLINES_PAGE_LIMIT or len(closed) < len(batch):
            break
        cursor = batch[-1]["close_time"] + 1
    return out


def _check_paused(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("select paused from mission7_bot_control where simulation_id=%s", (SIM_ID,))
        row = cur.fetchone()
        return bool(row[0]) if row else False


def _force_zero_sizing_fn(spendable_bankroll_usd, base_stake_usd, context):
    """Kill switch ativo: 0.0 sempre -- nenhuma entrada nova e' aberta.
    context (n_open_positions, etc.) e' ignorado de proposito; o objetivo
    e' bloquear TUDO, incondicionalmente, enquanto pausado."""
    return 0.0


def fetch_and_insert_live_data(conn, universe_symbols):
    """MISSAO 16: alem de buscar/inserir candles novos, agora tambem
    RASTREIA quais simbolos falharam de verdade por erro de rede/API
    (LiveDataError, ja depois de fetch_public_klines ter tentado de novo
    internamente) -- diferente de "nenhum candle novo fechado ainda", que
    e' um resultado NORMAL (rodou antes da proxima hora fechar) e nao
    entra na lista de falhas. Devolve (total_new, n_attempted,
    failed_symbols) -- n_attempted conta so' os simbolos que realmente
    tinham historico pra' tentar buscar (exclui os "SEM historico"
    abaixo, que nunca deveriam acontecer pos-migracao e nao sao falha de
    rede)."""
    now_ms = int(time.time() * 1000)
    total_new = 0
    n_attempted = 0
    failed_symbols = []  # lista de (symbol, motivo) -- SO' erro de rede/API
    with conn.cursor() as cur:
        for symbol in sorted(universe_symbols):
            cur.execute(
                "select max(open_time) from mission7_market_states where simulation_id=%s and symbol=%s",
                (SIM_ID, symbol),
            )
            last_open_time = cur.fetchone()[0]
            if last_open_time is None:
                print(f"  {symbol}: SEM historico em {SIM_ID} na nuvem -- pulando (nao deveria acontecer pos-migracao).")
                continue
            n_attempted += 1
            gap_start_ms = last_open_time + HOUR_MS
            context_start_ms = gap_start_ms - CONTEXT_BARS * HOUR_MS
            try:
                bars = _fetch_closed_klines(symbol, context_start_ms, now_ms)
            except LiveDataError as e:
                failed_symbols.append((symbol, str(e)))
                print(f"  !!! {symbol}: ERRO DE REDE/API ao buscar klines publicos -- {e}")
                print(f"  !!! {symbol}: pulando este simbolo NESTA RODADA (nao e' 'sem candle novo', e' falha real).")
                continue
            if not bars:
                print(f"  {symbol}: nenhum candle novo fechado ainda.")
                continue
            atr_pcts = compute_atr_pct_series(bars, period=WILDER_PERIOD)
            adxs = compute_adx_series(bars, period=WILDER_PERIOD)
            new_rows = []
            for i, bar in enumerate(bars):
                if bar["open_time"] <= last_open_time:
                    continue
                from datetime import datetime, timezone
                ts_iso = datetime.fromtimestamp(bar["open_time"] / 1000.0, tz=timezone.utc).isoformat()
                new_rows.append({
                    "symbol": symbol, "open_time": bar["open_time"], "ts": ts_iso,
                    "price": bar["close"], "atr_pct": atr_pcts[i], "adx": adxs[i],
                })
            if not new_rows:
                print(f"  {symbol}: nenhum candle novo fechado ainda.")
                continue
            _insert_rows_cloud(conn, "mission7_market_states", new_rows, SIM_ID)
            conn.commit()
            total_new += len(new_rows)
            print(f"  {symbol}: +{len(new_rows)} candles novos (open_time {new_rows[0]['open_time']} .. {new_rows[-1]['open_time']}).")

    if failed_symbols:
        print(f"\n{'=' * 70}")
        print(f"!!! AVISO: {len(failed_symbols)}/{n_attempted} simbolos falharam por erro de "
              f"rede/API da Binance nesta rodada (apos retry):")
        for sym, motivo in failed_symbols:
            print(f"    - {sym}: {motivo}")
        print(f"{'=' * 70}")

    return total_new, n_attempted, failed_symbols


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-epochs", type=int, default=2)
    parser.add_argument("--fetch-only", action="store_true")
    args = parser.parse_args()

    conn = _cloud_pg_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("select distinct symbol from mission7_market_states where simulation_id=%s order by symbol", (SIM_ID,))
            universe_symbols = [r[0] for r in cur.fetchall()]
        if not universe_symbols:
            print(f"ABORTADO: nenhum simbolo encontrado pra {SIM_ID} na nuvem -- rode _migrate_bob_lean_to_cloud.py primeiro.")
            return
        print(f"universo ({len(universe_symbols)} simbolos, na nuvem): {universe_symbols}")
        print("buscando candles publicos recentes da Binance (sem chave, sem autenticacao) ...")
        total_new, n_attempted, failed_symbols = fetch_and_insert_live_data(conn, universe_symbols)
        print(f"\ntotal de candles novos inseridos nesta rodada (nuvem): {total_new}")
    finally:
        conn.close()

    # MISSAO 16 -- validacao de sucesso antes de chamar o motor de epocas.
    total_outage = n_attempted > 0 and len(failed_symbols) == n_attempted
    if total_outage:
        print(f"\n{'#' * 70}")
        print("### RODADA ABORTADA -- TODOS os simbolos falharam por erro de rede/API.")
        print("### Zero chance de ter dado novo nesta rodada; chamar o motor de epocas")
        print("### agora so' mascararia o problema real. O motor de epocas NAO foi chamado.")
        print("### Verifique a conectividade com api.binance.com (rede/firewall/VPN) e")
        print("### rode o script de novo.")
        print(f"{'#' * 70}")
        return

    if args.fetch_only:
        print("--fetch-only: dados atualizados na nuvem, motor de epocas NAO chamado nesta rodada.")
        return

    if total_new == 0:
        if failed_symbols:
            print(f"\n[AVISO] nenhum candle novo foi inserido E {len(failed_symbols)}/{n_attempted} "
                  f"simbolos falharam por erro de rede -- a epoca muito provavelmente NAO vai avancar "
                  f"nesta rodada. Veja a lista de falhas acima antes de assumir que e' so' 'ainda nao "
                  f"fechou candle'.")
        else:
            print("\n[aviso] nenhum candle novo foi inserido nesta rodada, mas sem nenhum erro de rede "
                  "-- provavelmente e' so' cedo demais (ainda nao fechou candle novo desde a ultima "
                  "execucao). O motor sera chamado mesmo assim, mas nao deve fechar nenhuma epoca nova.")

    ctrl_conn = _cloud_pg_conn()
    try:
        paused = _check_paused(ctrl_conn)
    finally:
        ctrl_conn.close()

    if paused:
        print("\n*** KILL SWITCH ATIVO (mission7_bot_control.paused=true) ***")
        print("Novas entradas BLOQUEADAS nesta rodada (sizing_fn forcado a 0.0).")
        print("Posicoes ja abertas continuam sendo geridas normalmente -- saida/stop/disjuntor intocados.")
        model = dict(m8run.MODEL_REGISTRY_V4[MODEL_KEY])  # copia rasa -- nao muta o registro global
        model["sizing_fn"] = _force_zero_sizing_fn
        m8run.MODEL_REGISTRY_V4 = dict(m8run.MODEL_REGISTRY_V4)
        m8run.MODEL_REGISTRY_V4[MODEL_KEY] = model
    else:
        print("\nkill switch: OK, bot ATIVO -- operando normalmente.")

    print(f"\nchamando mission8_donchian_v1_sizing_v4_run.run(...) contra a NUVEM -- model={MODEL_KEY} max_epochs={args.max_epochs} ...")
    m8run.run(
        simulation_id=SIM_ID,
        model_key=MODEL_KEY,
        max_epochs=args.max_epochs,
        epoch_hours=m8run.EPOCH_HOURS_DEFAULT,
        fee_pct=m8run.FEE_PCT_DEFAULT,
        capital_per_position_usd=CAPITAL_PER_POSITION_USD_LIVE,
        initial_capital_usd=20.0,  # so' usado se nao houver checkpoint algum (nao e' o caso -- checkpoint ja migrado)
    )

    # MISSAO 21 -- SO' AGORA, com m8run.run() ja' totalmente retornado (e
    # portanto qualquer checkpoint novo desta rodada ja' escrito de
    # verdade), confirma e finaliza os fechamentos manuais desta rodada.
    _finalize_manual_closes_if_persisted(SIM_ID)


if __name__ == "__main__":
    main()
