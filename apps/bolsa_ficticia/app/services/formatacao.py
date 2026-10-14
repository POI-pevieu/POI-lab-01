"""Regras de apresentação compartilhadas pelos services (centavos -> reais, tendência, dicts de resposta).

Valores monetários são inteiros em centavos (sem erro de ponto flutuante);
as respostas trazem também o valor em reais.
"""

from __future__ import annotations

from typing import Any, Mapping


def reais(centavos: int) -> float:
    return round(centavos / 100.0, 2)


def tendencia(preco: int, anterior: int) -> str:
    if preco > anterior:
        return "alta"
    if preco < anterior:
        return "baixa"
    return "estavel"


def acao_dict(r: Mapping[str, Any]) -> dict:
    p, a = r["preco_centavos"], r["preco_anterior_centavos"]
    return {
        "ticker": r["ticker"],
        "nome": r["nome"],
        "preco": reais(p),
        "preco_centavos": p,
        "preco_anterior": reais(a),
        "preco_anterior_centavos": a,
        "variacao_pct": round((p - a) / a * 100.0, 4),
        "tendencia": tendencia(p, a),
        "atualizado_em": r["atualizado_em"],
    }


def investidor_dict(inv: Mapping[str, Any]) -> dict:
    return {
        "id": inv["id"],
        "nome": inv["nome"],
        "saldo_inicial": reais(inv["saldo_inicial_centavos"]),
        "saldo_inicial_centavos": inv["saldo_inicial_centavos"],
        "saldo": reais(inv["saldo_centavos"]),
        "saldo_centavos": inv["saldo_centavos"],
        "criado_em": inv["criado_em"],
    }


def ordem_dict(r: Mapping[str, Any]) -> dict:
    return {
        "id": r["id"],
        "investidor_id": r["investidor_id"],
        "ticker": r["ticker"],
        "tipo": r["tipo"],
        "quantidade": r["quantidade"],
        "preco": reais(r["preco_centavos"]),
        "preco_centavos": r["preco_centavos"],
        "valor": reais(r["valor_centavos"]),
        "valor_centavos": r["valor_centavos"],
        "saldo_apos": reais(r["saldo_apos_centavos"]),
        "saldo_apos_centavos": r["saldo_apos_centavos"],
        "origem": r["origem"],
        "criado_em": r["criado_em"],
    }
