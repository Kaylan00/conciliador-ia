# Avaliação da leitura de documentos

Gerado por `python scripts/avaliar_documentos.py`: os números abaixo foram medidos, não escritos à mão.

- Extrator: **llm (openai/gpt-4o-mini)**
- Tesseract: 5.5.0 (`lang=por`)
- Documentos: 12 arquivos de `dados/exemplos/documentos/`, gerados por `scripts/gerar_documentos.py` a partir de `dados/exemplos/cenario.json`
- Valor total **e** data de emissão certos: **12/12**
- Acerto geral: **93/96 (96.9%)** dos campos comparados

## Acerto por campo e por formato

| campo | xml | pdf_texto | pdf_escaneado | imagem | total |
| --- | --- | --- | --- | --- | --- |
| tipo | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| direcao | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 2/3 (66.7%) | 11/12 (91.7%) |
| emitente_cnpj | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 2/3 (66.7%) | 11/12 (91.7%) |
| emitente_nome | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 2/3 (66.7%) | 11/12 (91.7%) |
| numero | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| data_emissao | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| data_vencimento | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| valor_total | 4/4 (100.0%) | 4/4 (100.0%) | 1/1 (100.0%) | 3/3 (100.0%) | 12/12 (100.0%) |
| todos | 32/32 (100.0%) | 32/32 (100.0%) | 8/8 (100.0%) | 21/24 (87.5%) | 93/96 (96.9%) |

`emitente_nome` conta como certo com similaridade ≥ 85 (rapidfuzz `token_sort_ratio`, sem diferenciar maiúsculas e pontuação). Os outros campos são comparados exatamente; campo que o gabarito deixa vazio (ex.: CNPJ de recibo de pessoa física) só conta como certo se a leitura também deixar vazio.

## Por documento

| documento | formato | acertos | confiança | problemas | tempo (s) |
| --- | --- | --- | --- | --- | --- |
| doc-01 `nfe_4521_tecidos_vale_verde.xml` | xml | 8/8 | 1.00 | - | 0.00 |
| doc-02 `nfse_0318_clic_contabilidade.pdf` | pdf_texto | 8/8 | 1.00 | - | 3.86 |
| doc-03 `recibo_aluguel_outubro.jpg` | imagem | 5/8 | 0.50 | recibo_emitente_e_quem_pagou | 3.72 |
| doc-04 `fatura_energia_outubro.pdf` | pdf_texto | 8/8 | 1.00 | - | 4.15 |
| doc-05 `fatura_internet_outubro.pdf` | pdf_escaneado | 8/8 | 1.00 | - | 4.39 |
| doc-06 `nfe_1187_aviamentos_costa.xml` | xml | 8/8 | 1.00 | - | 0.00 |
| doc-07 `recibo_motofrete.png` | imagem | 8/8 | 1.00 | - | 4.05 |
| doc-08 `nfse_2290_nuvem_gestao.pdf` | pdf_texto | 8/8 | 1.00 | - | 3.68 |
| doc-09 `nfe_0093_venda_loja_bem_vestir.xml` | xml | 8/8 | 1.00 | - | 0.00 |
| doc-10 `nfse_0041_hotel_mar_azul.pdf` | pdf_texto | 8/8 | 1.00 | - | 3.77 |
| doc-11 `recibo_mercadinho.jpg` | imagem | 8/8 | 1.00 | - | 4.23 |
| doc-12 `nfe_4610_tecidos_vale_verde.xml` | xml | 8/8 | 1.00 | - | 0.00 |

## Erros

- doc-03 · `direcao`: esperado `pagar`, obtido `receber`
- doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601815000120`
- doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `Ateliê Ponto Firme Ltda`

## Teste de estresse do OCR

Os documentos que passam por OCR, piorados de propósito antes da leitura (uma degradação por vez, por cima do arquivo versionado, que já vem torto, ruidoso e borrado). Cada célula mostra quantos dos 8 campos saíram certos e a confiança calculada. **Erro sem alerta** conta os documentos com algum campo errado e confiança ≥ 0.8, ou seja, que passariam sem revisão: é o número que importa, porque erro com confiança baixa vai para uma pessoa conferir.

| degradação | doc-03 | doc-05 | doc-07 | doc-11 | campos certos | valor e data certos | erro sem alerta |
| --- | --- | --- | --- | --- | --- | --- | --- |
| nenhuma (arquivo versionado) | 5/8 · 0.50 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 29/32 (90.6%) | 4/4 | 0 |
| mais 3° de rotação | 5/8 · 0.50 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 29/32 (90.6%) | 4/4 | 0 |
| mais 6° de rotação | 5/8 · 0.50 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 29/32 (90.6%) | 4/4 | 0 |
| ruído forte (σ = 35) | 5/8 · 0.50 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 29/32 (90.6%) | 4/4 | 0 |
| desfoque (raio 2,5 px) | 3/8 · 0.20 | 7/8 · 0.70 | 5/8 · 0.40 | 4/8 · 0.50 | 19/32 (59.4%) | 1/4 | 0 |
| metade da resolução | 5/8 · 0.50 | 8/8 · 1.00 | 7/8 · 0.70 | 8/8 · 1.00 | 28/32 (87.5%) | 4/4 | 0 |
| um terço da resolução | 3/8 · 0.50 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 27/32 (84.4%) | 3/4 | 0 |
| JPEG qualidade 10 | 5/8 · 0.50 | 8/8 · 1.00 | 8/8 · 1.00 | 8/8 · 1.00 | 29/32 (90.6%) | 4/4 | 0 |

Campos errados no estresse:

- nenhuma (arquivo versionado) · doc-03 · `direcao`: esperado `pagar`, obtido `receber`
- nenhuma (arquivo versionado) · doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601815000120`
- nenhuma (arquivo versionado) · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `Ateliê Ponto Firme Ltda`
- mais 3° de rotação · doc-03 · `direcao`: esperado `pagar`, obtido `receber`
- mais 3° de rotação · doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601815000120`
- mais 3° de rotação · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `Ateliê Ponto Firme Ltda`
- mais 6° de rotação · doc-03 · `direcao`: esperado `pagar`, obtido `receber`
- mais 6° de rotação · doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601815000120`
- mais 6° de rotação · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `Ateliê Ponto Firme Ltda`
- ruído forte (σ = 35) · doc-03 · `direcao`: esperado `pagar`, obtido `receber`
- ruído forte (σ = 35) · doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601815000120`
- ruído forte (σ = 35) · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `Ateliê Ponto Firme Ltda`
- desfoque (raio 2,5 px) · doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601018000120`
- desfoque (raio 2,5 px) · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `None`
- desfoque (raio 2,5 px) · doc-03 · `data_emissao`: esperado `2026-10-05`, obtido `None`
- desfoque (raio 2,5 px) · doc-03 · `data_vencimento`: esperado `2026-10-05`, obtido `None`
- desfoque (raio 2,5 px) · doc-03 · `valor_total`: esperado `1800.00`, obtido `1000.00`
- desfoque (raio 2,5 px) · doc-05 · `emitente_cnpj`: esperado `08246281000179`, obtido `06246281000179`
- desfoque (raio 2,5 px) · doc-07 · `emitente_cnpj`: esperado `51819093000111`, obtido `31019092000104`
- desfoque (raio 2,5 px) · doc-07 · `emitente_nome`: esperado `Leva e Traz Entregas Ltda`, obtido `Leve e Taz Em`
- desfoque (raio 2,5 px) · doc-07 · `valor_total`: esperado `95.00`, obtido `93.00`
- desfoque (raio 2,5 px) · doc-11 · `emitente_nome`: esperado `Mercadinho Boa Vista`, obtido `None`
- desfoque (raio 2,5 px) · doc-11 · `data_emissao`: esperado `2026-10-18`, obtido `2026-10-10`
- desfoque (raio 2,5 px) · doc-11 · `data_vencimento`: esperado `2026-10-18`, obtido `2026-10-10`
- desfoque (raio 2,5 px) · doc-11 · `valor_total`: esperado `57.80`, obtido `87.90`
- metade da resolução · doc-03 · `direcao`: esperado `pagar`, obtido `receber`
- metade da resolução · doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601815000120`
- metade da resolução · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `Ateliê Ponto Firme Ltda`
- metade da resolução · doc-07 · `emitente_cnpj`: esperado `51819093000111`, obtido `51819093100111`
- um terço da resolução · doc-03 · `direcao`: esperado `pagar`, obtido `receber`
- um terço da resolução · doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601815000120`
- um terço da resolução · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `Ateliê Ponto Firme Ltda`
- um terço da resolução · doc-03 · `data_emissao`: esperado `2026-10-05`, obtido `2026-10-01`
- um terço da resolução · doc-03 · `data_vencimento`: esperado `2026-10-05`, obtido `2026-10-01`
- JPEG qualidade 10 · doc-03 · `direcao`: esperado `pagar`, obtido `receber`
- JPEG qualidade 10 · doc-03 · `emitente_cnpj`: esperado `None`, obtido `52601815000120`
- JPEG qualidade 10 · doc-03 · `emitente_nome`: esperado `José Carlos Almeida`, obtido `Ateliê Ponto Firme Ltda`

## Como ler estes números

Os 12 arquivos são sintéticos: o mesmo gerador desenhou o layout e o leitor foi escrito conhecendo esse layout. Acerto alto aqui mostra que o caminho inteiro funciona (detecção de formato, XML, camada de texto, OCR com pré-processamento, regras e confiança), não que a taxa vale para documentos reais, que variam muito mais de layout e de qualidade de imagem. O teste de estresse mede só a imagem, com o layout fixo. Para medir de verdade, rode o mesmo script sobre uma pasta de documentos reais anotados.
