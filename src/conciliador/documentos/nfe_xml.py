"""Leitura estruturada de NF-e: XML do leiaute 4.00, com ou sem o envelope nfeProc. Não passa por extrator."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from conciliador.modelos import DocumentoExtraido, ItemDocumento, OrigemTexto, TipoDocumento

NS_NFE = "http://www.portalfiscal.inf.br/nfe"
_NS = {"nfe": NS_NFE}
_CENTAVOS = Decimal("0.01")
_TAMANHO_DESCRICAO = 120


class NFeInvalida(ValueError):
    """O conteúdo não é uma NF-e legível."""


def parece_nfe(conteudo: bytes) -> bool:
    return NS_NFE.encode() in conteudo[:4096] and b"infNFe" in conteudo


def ler_nfe(conteudo: bytes) -> DocumentoExtraido:
    # NF-e não usa DTD. Recusar DOCTYPE fecha a porta para entidade externa (XXE) e para expansão de entidades
    # ("billion laughs") sem depender de biblioteca extra.
    if re.search(rb"<!DOCTYPE", conteudo, re.IGNORECASE):
        raise NFeInvalida("XML com DOCTYPE: NF-e não usa DTD, arquivo recusado")
    try:
        raiz = ET.fromstring(conteudo)
    except ET.ParseError as erro:
        raise NFeInvalida(f"XML mal formado: {erro}") from erro
    inf = raiz if raiz.tag == f"{{{NS_NFE}}}infNFe" else raiz.find(".//nfe:infNFe", _NS)
    if inf is None:
        raise NFeInvalida("não há infNFe no XML: não é uma NF-e")

    itens = [_item(det) for det in inf.findall("nfe:det", _NS)]
    vencimentos = [_data(d.text) for d in inf.findall("nfe:cobr/nfe:dup/nfe:dVenc", _NS) if d.text]
    descricoes = "; ".join(item.descricao for item in itens)
    if len(descricoes) > _TAMANHO_DESCRICAO:
        descricoes = descricoes[:_TAMANHO_DESCRICAO - 3].rstrip() + "..."

    return DocumentoExtraido(
        tipo=TipoDocumento.NFE,
        emitente_nome=_texto(inf, "nfe:emit/nfe:xNome"),
        emitente_cnpj=_texto(inf, "nfe:emit/nfe:CNPJ"),
        destinatario_nome=_texto(inf, "nfe:dest/nfe:xNome"),
        destinatario_cnpj=_texto(inf, "nfe:dest/nfe:CNPJ"),
        numero=_texto(inf, "nfe:ide/nfe:nNF"),
        data_emissao=_data(_texto(inf, "nfe:ide/nfe:dhEmi") or _texto(inf, "nfe:ide/nfe:dEmi")),
        # Com várias parcelas, a primeira a vencer é a que interessa para conciliar o primeiro pagamento.
        data_vencimento=min(vencimentos, default=None),
        valor_total=_dinheiro(_texto(inf, "nfe:total/nfe:ICMSTot/nfe:vNF"), "vNF"),
        itens=itens,
        descricao=descricoes or None,
        origem=OrigemTexto.XML,
    )


def _texto(elemento: ET.Element, caminho: str) -> str | None:
    achado = elemento.find(caminho, _NS)
    if achado is None or achado.text is None:
        return None
    return achado.text.strip() or None


def _data(texto: str | None) -> date | None:
    """dhEmi (4.00) vem com hora e fuso; dEmi (2.00) só com a data. A data vale no fuso de quem emitiu."""
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto).date() if "T" in texto else date.fromisoformat(texto)
    except ValueError as erro:
        raise NFeInvalida(f"data inválida: {texto!r}") from erro


def _decimal(texto: str | None, campo: str) -> Decimal | None:
    if texto is None:
        return None
    try:
        return Decimal(texto)
    except InvalidOperation as erro:
        raise NFeInvalida(f"{campo} não é número: {texto!r}") from erro


def _dinheiro(texto: str | None, campo: str) -> Decimal | None:
    valor = _decimal(texto, campo)
    return valor.quantize(_CENTAVOS) if valor is not None else None


def _sem_zeros_inuteis(valor: Decimal, casas: Decimal) -> Decimal:
    """38.0000000000 -> 38.00 e 60.0000 -> 60, mas 0.1234 continua 0.1234: tira zero, não precisão."""
    arredondado = valor.quantize(casas)
    return arredondado if arredondado == valor else valor


def _item(det: ET.Element) -> ItemDocumento:
    prod = det.find("nfe:prod", _NS)
    if prod is None:
        raise NFeInvalida(f"item {det.get('nItem')} sem prod")
    quantidade = _decimal(_texto(prod, "nfe:qCom"), "qCom") or Decimal("1")
    total = _dinheiro(_texto(prod, "nfe:vProd"), "vProd")
    if total is None:
        raise NFeInvalida(f"item {det.get('nItem')} sem vProd")
    unitario = _decimal(_texto(prod, "nfe:vUnCom"), "vUnCom")
    return ItemDocumento(
        descricao=_texto(prod, "nfe:xProd") or "",
        quantidade=_sem_zeros_inuteis(quantidade, Decimal("1")),
        valor_unitario=_sem_zeros_inuteis(unitario, _CENTAVOS) if unitario is not None else total / quantidade,
        valor_total=total,
    )
