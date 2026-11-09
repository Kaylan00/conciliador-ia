# Avaliação da leitura de documentos

Gerado por `python scripts/avaliar_documentos.py`: os números abaixo foram medidos, não escritos à mão.

- Extrator: **regras**
- Tesseract: 5.5.0 (`lang=por`)
- Documentos: 12 arquivos de `dados/exemplos/documentos/`, gerados por `scripts/gerar_documentos.py` a partir de `dados/exemplos/cenario.json`
- Valor total **e** data de emissão certos: **12/12**
- Acerto geral: **96/96 (100.0%)** dos campos comparados

## Acerto por campo e por formato

| campo | xml | pdf_texto | pdf_escaneado | imagem | total |
| --- | --- | --- | --- | --- | --- |
| tipo | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| direcao | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| emitente_cnpj | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| emitente_nome | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| numero | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| data_emissao | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| data_vencimento | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| valor_total | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| todos | 32/32 (100.0%) | 32/32 (100.0%) | 8/8 (100.0%) | 24/24 (100.0%) | 96/96 (100.0%) |

`emitente_nome` conta como certo com similaridade ≥ 85 (rapidfuzz `token_sort_ratio`, sem diferenciar maiúsculas e pontuação). Os outros campos são comparados exatamente; campo que o gabarito deixa vazio (ex.: CNPJ de recibo de pessoa física) só conta como certo se a leitura também deixar vazio.

## Por documento

| documento | formato | acertos | confiança | problemas | tempo (s) |
| --- | --- | --- | --- | --- | --- |
| doc-01 `nfe_4521_tecidos_vale_verde.xml` | xml | 8/8 | 1.00 | - | 0.00 |
| doc-02 `nfse_0318_clic_contabilidade.pdf` | pdf_texto | 8/8 | 1.00 | - | 0.02 |
| doc-03 `recibo_aluguel_outubro.jpg` | imagem | 8/8 | 1.00 | - | 0.41 |
| doc-04 `fatura_energia_outubro.pdf` | pdf_texto | 8/8 | 1.00 | - | 0.01 |
| doc-05 `fatura_internet_outubro.pdf` | pdf_escaneado | 8/8 | 1.00 | - | 0.91 |
| doc-06 `nfe_1187_aviamentos_costa.xml` | xml | 8/8 | 1.00 | - | 0.00 |
| doc-07 `recibo_motofrete.png` | imagem | 8/8 | 1.00 | - | 0.40 |
| doc-08 `nfse_2290_nuvem_gestao.pdf` | pdf_texto | 8/8 | 1.00 | - | 0.01 |
| doc-09 `nfe_0093_venda_loja_bem_vestir.xml` | xml | 8/8 | 1.00 | - | 0.00 |
| doc-10 `nfse_0041_hotel_mar_azul.pdf` | pdf_texto | 8/8 | 1.00 | - | 0.01 |
| doc-11 `recibo_mercadinho.jpg` | imagem | 8/8 | 1.00 | - | 0.37 |
| doc-12 `nfe_4610_tecidos_vale_verde.xml` | xml | 8/8 | 1.00 | - | 0.00 |

## Erros

Nenhum campo errado nesta execução.

## Teste de estresse do OCR

Os documentos que passam por OCR, piorados de propósito antes da leitura (uma degradação por vez, por cima do arquivo versionado, que já vem torto, ruidoso e borrado). Cada célula mostra quantos dos 8 campos saíram certos e a confiança calculada. **Erro sem alerta** conta os documentos com algum campo errado e confiança ≥ 0.8, ou seja, que passariam sem revisão: é o número que importa, porque erro com confiança baixa vai para uma pessoa conferir.

| degradação | doc-03 | doc-05 | doc-07 | doc-11 | campos certos | valor e data certos | erro sem alerta |
| --- | --- | --- | --- | --- | --- | --- | --- |
| nenhuma (arquivo versionado) | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 32/32 (100.0%) | 4/4 | 0 |
| mais 3° de rotação | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 32/32 (100.0%) | 4/4 | 0 |
| mais 6° de rotação | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 32/32 (100.0%) | 4/4 | 0 |
| ruído forte (σ = 35) | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 32/32 (100.0%) | 4/4 | 0 |
| desfoque (raio 2,5 px) | 4/8 · 0.50 | 5/8 · 0.70 | 4/8 · 0.70 | 4/8 · 0.70 | 17/32 (53.1%) | 1/4 | 0 |
| metade da resolução | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 32/32 (100.0%) | 4/4 | 0 |
| um terço da resolução | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 32/32 (100.0%) | 4/4 | 0 |
| JPEG qualidade 10 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 32/32 (100.0%) | 4/4 | 0 |

Campos errados no estresse:

- desfoque (raio 2,5 px) · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `urgência de`
- desfoque (raio 2,5 px) · doc-03 · `data_emissao`: esperado `2026-10-05`, obtido `None`
- desfoque (raio 2,5 px) · doc-03 · `data_vencimento`: esperado `2026-10-05`, obtido `None`
- desfoque (raio 2,5 px) · doc-03 · `valor_total`: esperado `1800.00`, obtido `1000.00`
- desfoque (raio 2,5 px) · doc-05 · `emitente_cnpj`: esperado `08246281000179`, obtido `None`
- desfoque (raio 2,5 px) · doc-05 · `emitente_nome`: esperado `Rede Sul Fibra Ltda`, obtido `As. Champagnat, 911 - Centro - Via Velha/ES Deta de`
- desfoque (raio 2,5 px) · doc-05 · `numero`: esperado `88213`, obtido `None`
- desfoque (raio 2,5 px) · doc-07 · `emitente_cnpj`: esperado `51819093000111`, obtido `None`
- desfoque (raio 2,5 px) · doc-07 · `emitente_nome`: esperado `Leva e Traz Entregas Ltda`, obtido `Leve e Taz Em" ita`
- desfoque (raio 2,5 px) · doc-07 · `numero`: esperado `771`, obtido `None`
- desfoque (raio 2,5 px) · doc-07 · `valor_total`: esperado `95.00`, obtido `93.00`
- desfoque (raio 2,5 px) · doc-11 · `emitente_nome`: esperado `Mercadinho Boa Vista`, obtido `as ua ama`
- desfoque (raio 2,5 px) · doc-11 · `data_emissao`: esperado `2026-10-18`, obtido `2026-10-10`
- desfoque (raio 2,5 px) · doc-11 · `data_vencimento`: esperado `2026-10-18`, obtido `2026-10-10`
- desfoque (raio 2,5 px) · doc-11 · `valor_total`: esperado `57.80`, obtido `87.90`

## Como ler estes números

Os 12 arquivos são sintéticos: o mesmo gerador desenhou o layout e o leitor foi escrito conhecendo esse layout. Acerto alto aqui mostra que o caminho inteiro funciona (detecção de formato, XML, camada de texto, OCR com pré-processamento, regras e confiança), não que a taxa vale para documentos reais, que variam muito mais de layout e de qualidade de imagem. O teste de estresse mede só a imagem, com o layout fixo. Para medir de verdade, rode o mesmo script sobre uma pasta de documentos reais anotados.
