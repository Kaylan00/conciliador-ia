"""Extrator por regras: regex sobre o texto. Determinístico, sem custo e fácil de auditar.

Cada campo tem uma regra própria, quase sempre "rótulo perto do valor" ("Valor total", "Vencimento", "Tomador").
O que as regras não acham fica None; quem decide se isso é grave é documentos/confianca.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from conciliador.extracao.cnpj import cnpj_valido
from conciliador.extracao.normalizacao import normalizar_texto, valor_br_para_decimal
from conciliador.modelos import DocumentoExtraido, OrigemTexto, TipoDocumento

# --------------------------------------------------------------------------- tipo

_TIPOS = (  # ordem = prioridade: recibo e NFS-e costumam citar "boleto" ou "fatura" no rodapé
    # Recibo também pela frase-padrão, que sobrevive quando o OCR perde o título.
    (TipoDocumento.RECIBO,
     re.compile(r"\brecibo\b|\breceb(?:i|emos)\b.{0,120}?\b(?:import[âa]ncia|quantia)\s+de\b", re.I | re.S)),
    (TipoDocumento.NFSE, re.compile(r"\bnfs-?e\b|nota\s+fiscal\s+(?:eletr[ôo]nica\s+)?de\s+servi[çc]os?", re.I)),
    (TipoDocumento.FATURA,
     re.compile(r"\bfatura\b|\bconta\s+de\s+(?:energia|luz|[áa]gua|g[áa]s|telefone|internet)", re.I)),
    (TipoDocumento.NFE, re.compile(r"\bdanfe\b|\bnf-?e\b|nota\s+fiscal\s+eletr[ôo]nica", re.I)),
    (TipoDocumento.BOLETO, re.compile(r"\bboleto\b|linha\s+digit[áa]vel|ficha\s+de\s+compensa[çc][ãa]o", re.I)),
)
_LINHAS_CABECALHO = 12


def extrair_tipo(texto: str) -> TipoDocumento:
    """Procura a palavra-chave primeiro no cabeçalho (é lá que fica o título) e só depois no resto."""
    linhas = [linha for linha in texto.splitlines() if linha.strip()]
    for trecho in ("\n".join(linhas[:_LINHAS_CABECALHO]), texto):
        for tipo, padrao in _TIPOS:
            if padrao.search(trecho):
                return tipo
    return TipoDocumento.OUTRO


# --------------------------------------------------------------------------- CNPJ e papéis

_CNPJ = re.compile(r"(?<![\d./-])(\d{2})\.? ?(\d{3})\.? ?(\d{3}) ?/? ?(\d{4}) ?-? ?(\d{2})(?!\d)")
_ROTULO_CNPJ = re.compile(r"CNP[J)I1l]\s*(?:/\s*MF)?\s*(?:n[º°o.]*)?\s*[:.\-]?\s*$", re.I)
_ROTULO_DESTINATARIO = re.compile(
    r"\b(?:tomador|destinat[áa]rio|cliente|sacado|pagador|contratante|adquirente)\b|\breceb(?:i|emos)\s+de\b", re.I
)
_ROTULO_EMITENTE = re.compile(r"\b(?:prestador|emitente|fornecedor|benefici[áa]rio|cedente|vendedor)\b", re.I)
_JANELA_PAPEL = 160  # caracteres antes do CNPJ onde um rótulo ainda vale para ele

EMITENTE = "emitente"
DESTINATARIO = "destinatario"


@dataclass(frozen=True)
class CnpjEncontrado:
    digitos: str
    inicio: int
    papel: str | None  # EMITENTE, DESTINATARIO ou None quando nenhum rótulo perto


def _papel_pelo_rotulo(janela: str) -> str | None:
    """O rótulo mais próximo (o último da janela) decide de quem é o CNPJ."""
    ultimo: tuple[int, str] | None = None
    for padrao, papel in ((_ROTULO_DESTINATARIO, DESTINATARIO), (_ROTULO_EMITENTE, EMITENTE)):
        for achado in padrao.finditer(janela):
            if ultimo is None or achado.start() > ultimo[0]:
                ultimo = (achado.start(), papel)
    return ultimo[1] if ultimo else None


def encontrar_cnpjs(texto: str) -> list[CnpjEncontrado]:
    """CNPJs do texto, com o papel sugerido pelo rótulo mais próximo antes de cada um.

    Com o rótulo "CNPJ" na frente, o número entra mesmo com dígito verificador errado (a confiança é que vai
    reclamar). Sem rótulo, 14 dígitos soltos só contam se o DV fechar: linha digitável e código de barras têm
    sequências de 14 dígitos o tempo todo.
    """
    encontrados = []
    fim_anterior = 0
    for achado in _CNPJ.finditer(texto):
        digitos = "".join(achado.groups())
        rotulado = _ROTULO_CNPJ.search(texto[max(0, achado.start() - 20):achado.start()]) is not None
        if not rotulado and not cnpj_valido(digitos):
            continue
        # A janela não volta para antes do CNPJ anterior: o rótulo de lá pertence a ele.
        janela = texto[max(fim_anterior, achado.start() - _JANELA_PAPEL):achado.start()]
        encontrados.append(CnpjEncontrado(digitos, achado.start(), _papel_pelo_rotulo(janela)))
        fim_anterior = achado.end()
    return encontrados


def _separar_papeis(cnpjs: list[CnpjEncontrado]) -> tuple[CnpjEncontrado | None, CnpjEncontrado | None]:
    emitente = next((c for c in cnpjs if c.papel == EMITENTE), None)
    destinatario = next((c for c in cnpjs if c.papel == DESTINATARIO), None)
    for cnpj in (c for c in cnpjs if c.papel is None):
        # Sem rótulo, vale a ordem de leitura: quem emite aparece primeiro, no cabeçalho.
        if emitente is None and (destinatario is None or cnpj.digitos != destinatario.digitos):
            emitente = cnpj
        elif destinatario is None and emitente is not None and cnpj.digitos != emitente.digitos:
            destinatario = cnpj
    return emitente, destinatario


# --------------------------------------------------------------------------- nomes

_PREFIXO_NOME = re.compile(
    r"^\s*(?:raz[ãa]o\s+social|nome(?:\s*/\s*raz[ãa]o\s+social)?|nome\s+empresarial|cliente|"
    r"tomador(?:\s+de\s+servi[çc]os)?|prestador(?:\s+de\s+servi[çc]os)?|emitente|destinat[áa]rio|"
    r"receb(?:i|emos)\s+de)\b\s*[:\-]?\s*",
    re.I,
)
# Palavras que não fazem parte de nome. O que vem a partir delas é cortado: o OCR junta colunas na mesma linha
# ("Rede Sul Fibra Ltda FATURA DE SERVIÇOS") e o nome é o que sobra antes.
_NAO_E_NOME = re.compile(
    r"\b(?:cnp[j)i1l]|cpf|endere[çc]o|inscri[çc][ãa]o|cep|telefone|e-?mail|valor|data|recibo|nota\s+fiscal|"
    r"fatura|prefeitura|secretaria|assinatura|vencimento|emiss[ãa]o|dados\s+d[oa]|import[âa]ncia|referente)(?!\w)"
    r"|R\$",
    re.I,
)
_TITULO = re.compile(r"\b(?:prefeitura|secretaria|governo|minist[ée]rio|nota\s+fiscal|fatura|recibo|conta\s+de)\b",
                     re.I)


def _limpar_nome(trecho: str) -> str | None:
    """Tira rótulo ("Razão social:") e sobras de pontuação; devolve None se o que sobrou não parece um nome."""
    nome = _PREFIXO_NOME.sub("", trecho)
    if corte := _NAO_E_NOME.search(nome):
        nome = nome[:corte.start()]
    nome = nome.strip(" \t,;:-|_·")
    if not nome:
        return None
    palavras = re.findall(r"[^\W\d_]{2,}", nome)
    letras = sum(c.isalpha() for c in nome)
    if len(palavras) < 2 or letras < 0.6 * len(nome.replace(" ", "")):
        return None
    return nome


def _nome_perto(texto: str, inicio: int) -> str | None:
    """Nome na mesma linha antes do CNPJ ou numa das duas linhas de cima."""
    inicio_linha = texto.rfind("\n", 0, inicio) + 1
    acima = [linha for linha in texto[:inicio_linha].splitlines() if linha.strip()][-2:]
    for candidato in (texto[inicio_linha:inicio], *reversed(acima)):
        if nome := _limpar_nome(candidato):
            return nome
    return None


def _nome_no_cabecalho(texto: str) -> str | None:
    """Nome solto no topo, como o de um logotipo. Linha com rótulo ("Recebi de", "Cliente:") não conta: o nome
    rotulado é de outro papel ou já foi tratado junto do CNPJ."""
    for linha in [linha for linha in texto.splitlines() if linha.strip()][:3]:
        if not _TITULO.search(linha) and not _PREFIXO_NOME.match(linha) and (nome := _limpar_nome(linha)):
            return nome
    return None


def _assinante(texto: str, datas: list[DataEncontrada]) -> str | None:
    """Em recibo, quem recebeu assina embaixo da linha de cidade e data (ou, sem data legível, no fim da folha)."""
    if datas:
        candidatas = texto[datas[-1].fim:].splitlines()[1:]
    else:
        candidatas = [linha for linha in texto.splitlines() if linha.strip()][-3:][::-1]
    for linha in candidatas:
        if not _PREFIXO_NOME.match(linha) and (nome := _limpar_nome(linha)):
            return nome
    return None


_PAGADOR_RECIBO = re.compile(r"\breceb(?:i|emos)\s+de\s+(.+?)(?:,|\s+a\s+(?:quantia|import[âa]ncia))", re.I | re.S)
_DESTINATARIO_ROTULADO = re.compile(r"^\s*(?:cliente|tomador|destinat[áa]rio)\s*:\s*(.+)$", re.I | re.M)


# --------------------------------------------------------------------------- datas

MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7,
         "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
_DATA_NUMERICA = re.compile(r"(?<![\d/.\-])(\d{1,2}) ?[/.\-] ?(\d{1,2}) ?[/.\-] ?(\d{4})(?!\d)")
# O dia por extenso vem sozinho ("5 de outubro"), sem vizinho numérico para a normalização corrigir; aqui o
# "de <mês> de <ano>" em volta dá segurança para aceitar S, O e l no lugar de 5, 0 e 1.
_DATA_EXTENSO = re.compile(
    rf"(?<![\w/])([\dSOolI]{{1,2}})\s*[º°o]?\s+de\s+({'|'.join(MESES)})\s+de\s+(\d{{4}})(?!\d)", re.I)
_DIA_SUJO = str.maketrans({"S": "5", "O": "0", "o": "0", "l": "1", "I": "1"})

VENCIMENTO = "vencimento"
EMISSAO = "emissao"
OUTRA = "outra"
_ROTULOS_DATA = (
    (VENCIMENTO, re.compile(r"\bvenc(?:imento|e|\.)|pag[áa]vel\s+at[ée]|data\s+limite|pagar\s+at[ée]", re.I)),
    (EMISSAO, re.compile(r"emiss[ãa]o|\bemitid[ao]|data\s+do\s+documento", re.I)),
    (OUTRA, re.compile(r"leitura|per[íi]odo|refer[êe]ncia|compet[êe]ncia|autoriza|nascimento|validade|vig[êe]ncia|"
                       r"protocolo|recebimento|pr[óo]xima", re.I)),
)
_JANELA_DATA = 50
_INTERVALO = re.compile(r"^\s*(?:a|at[ée]|à|-)\s*$", re.I)


@dataclass(frozen=True)
class DataEncontrada:
    valor: date
    inicio: int
    fim: int
    rotulo: str | None  # VENCIMENTO, EMISSAO, OUTRA ou None


def _montar_data(dia: str, mes: str, ano: str) -> date | None:
    try:
        valor = date(int(ano), int(mes), int(dia))
    except ValueError:
        return None
    return valor if 1990 <= valor.year <= 2100 else None


def encontrar_datas(texto: str) -> list[DataEncontrada]:
    """Datas dd/mm/aaaa e "5 de outubro de 2026", cada uma com o rótulo mais próximo antes dela."""
    brutas: list[tuple[int, int, date]] = []
    for achado in _DATA_NUMERICA.finditer(texto):
        if valor := _montar_data(*achado.groups()):
            brutas.append((achado.start(), achado.end(), valor))
    for achado in _DATA_EXTENSO.finditer(texto):
        dia, mes, ano = achado.groups()
        if valor := _montar_data(dia.translate(_DIA_SUJO), str(MESES[mes.lower()]), ano):
            brutas.append((achado.start(), achado.end(), valor))

    encontradas: list[DataEncontrada] = []
    for inicio, fim, valor in sorted(brutas, key=lambda b: b[0]):
        fim_anterior = encontradas[-1].fim if encontradas else 0
        janela = texto[max(fim_anterior, inicio - _JANELA_DATA):inicio]
        if encontradas and _INTERVALO.match(texto[fim_anterior:inicio]):
            rotulo = encontradas[-1].rotulo  # "01/10/2026 a 31/10/2026": a segunda herda o rótulo da primeira
        else:
            rotulo = _rotulo_mais_proximo(janela)
        encontradas.append(DataEncontrada(valor, inicio, fim, rotulo))
    return encontradas


def _rotulo_mais_proximo(janela: str) -> str | None:
    ultimo: tuple[int, str] | None = None
    for rotulo, padrao in _ROTULOS_DATA:
        for achado in padrao.finditer(janela):
            if ultimo is None or achado.start() > ultimo[0]:
                ultimo = (achado.start(), rotulo)
    return ultimo[1] if ultimo else None


# --------------------------------------------------------------------------- valor total

_VALOR = re.compile(r"(?<![\d.,])(\d{1,3}(?:\.\d{3})+|\d+),(\d{2})(?!\d)(?! ?%)")
_ROTULOS_VALOR = tuple(re.compile(padrao, re.I) for padrao in (  # do mais específico para o mais genérico
    r"valor\s+total\s+(?:da\s+nota|do\s+documento|a\s+pagar|l[íi]quido)",
    r"total\s+a\s+pagar",
    r"valor\s+a\s+pagar",
    r"valor\s+total",
    r"import[âa]ncia\s+de",
    r"quantia\s+de",
    r"valor\s+do\s+documento",
    r"valor\s+l[íi]quido",
    r"\btotal\b",
))
_DISTANCIA_ROTULO_VALOR = 60


def extrair_valor_total(texto: str) -> Decimal | None:
    """Primeiro valor logo depois do rótulo de maior prioridade; sem rótulo, o maior valor com "R$"."""
    for rotulo in _ROTULOS_VALOR:
        for achado in rotulo.finditer(texto):
            trecho = texto[achado.end():achado.end() + _DISTANCIA_ROTULO_VALOR]
            valor = _VALOR.search(trecho)
            # Entre o rótulo e o número só pode haver pontuação e "R$": "Valor total dos tributos 13,00" não vale.
            if valor and not re.search(r"[^\W\d_]", trecho[:valor.start()].replace("R$", "")):
                return valor_br_para_decimal(valor.group(0))
    com_cifrao = [valor_br_para_decimal(v.group(0)) for v in re.finditer(rf"R\$ ?{_VALOR.pattern}", texto)]
    return max(com_cifrao, default=None)


# --------------------------------------------------------------------------- número e descrição

_NOME_DOC = r"(?:recibo|fatura|conta|nota(?:\s+fiscal)?(?:\s+de\s+servi[çc]os?)?|nfs-?e|nf-?e|documento)"
_ROTULO_NUMERO = r"(?:n[úu]mero|n[º°oe]|num)\.?"  # "Ne": o OCR costuma ler o º como e
_NUMERO = (
    re.compile(rf"\b{_NOME_DOC}\s*{_ROTULO_NUMERO}\s*[:\-]?\s*(\d[\d.\-/]*)", re.I),  # "Fatura nº 88213"
    re.compile(rf"\b{_ROTULO_NUMERO}\s*d[aoe]\s+{_NOME_DOC}\s*[:\-]?\s*(\d[\d.\-/]*)", re.I),  # "Número da NFS-e 318"
    re.compile(rf"^\s*{_ROTULO_NUMERO}\s*[:\-]?\s*(\d[\d.\-/]*)", re.I | re.M),  # "Nº 771" abrindo a linha
)


def extrair_numero(texto: str) -> str | None:
    for padrao in _NUMERO:
        if achado := padrao.search(texto):
            digitos = re.sub(r"\D", "", achado.group(1))
            if digitos:
                return digitos.lstrip("0") or "0"  # "000318" e "318" são a mesma nota
    return None


_REFERENTE = re.compile(r"\breferente\s+(?:a|ao|à|aos|às)\b\s*:?\s*", re.I)
_DISCRIMINACAO = re.compile(r"discrimina[çc][ãa]o\s+dos\s+servi[çc]os\s*:?\s*\n\s*(.+)", re.I)
_DESCRICAO_ROTULADA = re.compile(r"descri[çc][ãa]o\s*:\s*(.+)", re.I)


def extrair_descricao(texto: str) -> str | None:
    if achado := _REFERENTE.search(texto):
        linhas = [linha.strip() for linha in texto[achado.end():].split("\n")]
        partes = [linhas[0]]
        # A frase do recibo quebra de linha no meio (e o OCR às vezes põe linha em branco entre as duas partes):
        # continua enquanto a próxima linha com texto começar minúscula.
        for linha in filter(None, linhas[1:]):
            if not linha[0].islower() or partes[-1].endswith("."):
                break
            partes.append(linha)
        frase = re.split(r"\.(?:\s|$)", " ".join(partes))[0].strip(" .,;")
        if frase:
            return frase[0].upper() + frase[1:]
    for padrao in (_DISCRIMINACAO, _DESCRICAO_ROTULADA):
        if achado := padrao.search(texto):
            return achado.group(1).strip()
    return None


# --------------------------------------------------------------------------- extrator


class ExtratorRegras:
    """Extrator padrão: regex, sem rede e sem custo. Itens de NFS-e e fatura não são lidos (ficam vazios)."""

    def extrair(self, texto: str, origem: OrigemTexto) -> DocumentoExtraido:
        limpo = normalizar_texto(texto)
        tipo = extrair_tipo(limpo)
        cnpjs = encontrar_cnpjs(limpo)
        emitente, destinatario = _separar_papeis(cnpjs)
        datas = encontrar_datas(limpo)

        emissao = next((d for d in datas if d.rotulo == EMISSAO), None) or next(
            (d for d in datas if d.rotulo is None), None)
        vencimento = next((d.valor for d in datas if d.rotulo == VENCIMENTO), None)
        if vencimento is None and tipo == TipoDocumento.RECIBO and emissao:
            vencimento = emissao.valor  # recibo é pagamento feito na hora

        emitente_nome = _nome_perto(limpo, emitente.inicio) if emitente else None
        if emitente_nome is None and tipo == TipoDocumento.RECIBO:
            emitente_nome = _assinante(limpo, datas)
        if emitente_nome is None:
            emitente_nome = _nome_no_cabecalho(limpo)

        return DocumentoExtraido(
            tipo=tipo,
            emitente_nome=emitente_nome,
            emitente_cnpj=emitente.digitos if emitente else None,
            destinatario_nome=self._destinatario_nome(limpo, destinatario),
            destinatario_cnpj=destinatario.digitos if destinatario else None,
            numero=extrair_numero(limpo),
            data_emissao=emissao.valor if emissao else None,
            data_vencimento=vencimento,
            valor_total=extrair_valor_total(limpo),
            descricao=extrair_descricao(limpo),
            origem=origem,
            texto=texto,
        )

    @staticmethod
    def _destinatario_nome(texto: str, destinatario: CnpjEncontrado | None) -> str | None:
        if destinatario and (nome := _nome_perto(texto, destinatario.inicio)):
            return nome
        for padrao in (_PAGADOR_RECIBO, _DESTINATARIO_ROTULADO):
            if (achado := padrao.search(texto)) and (nome := _limpar_nome(" ".join(achado.group(1).split()))):
                return nome
        return None
