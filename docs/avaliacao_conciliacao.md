# Avaliação da conciliação

Gerado por `python scripts/avaliar_conciliacao.py`; não edite à mão.

Os documentos vêm do gabarito de `dados/exemplos/cenario.json`, como se a extração tivesse acertado tudo,
para medir só a conciliação. O extrato é `dados/exemplos/extrato_outubro.ofx` e o esperado é
`dados/exemplos/gabarito_conciliacao.json`.

| Métrica | Resultado |
| --- | --- |
| Pares esperados | 11 |
| Pares sugeridos | 11 |
| Certos | 11 |
| Errados | 0 |
| Faltando | 0 |
| Precisão | 100,0% |
| Revocação | 100,0% |
| Transações sem documento | 20261001001, 20261021001, 20261031001 (confere) |
| Documentos sem transação | doc-10 (confere) |

## Pares sugeridos

| FITID | Data | Valor | Memo | Documento | Score | Motivos |
| --- | --- | ---: | --- | --- | ---: | --- |
| 20261003001 | 2026-10-03 | -189,00 | PAG BOLETO NUVEM GESTAO SIST | doc-08 | 0.895 | valor igual (R$ 189,00); pago no dia do vencimento; nome parecido com o memo (89%) |
| 20261005001 | 2026-10-05 | -1.800,00 | TED ENVIADA JOSE C ALMEIDA | doc-03 | 0.877 | valor igual (R$ 1.800,00); pago no dia do vencimento; nome parecido com o memo (88%) |
| 20261007001 | 2026-10-07 | -650,00 | PIX ENVIADO CLIC CONTABILIDADE LTDA | doc-02 | 0.925 | valor igual (R$ 650,00); pago 3 dias antes do vencimento; nome parecido com o memo (100%) |
| 20261009001 | 2026-10-09 | -486,50 | PIX ENVIADO AVIAMENTOS COSTA | doc-06 | 0.925 | valor igual (R$ 486,50); pago 1 dia depois do vencimento; nome parecido com o memo (95%) |
| 20261010001 | 2026-10-10 | -129,90 | PAG CONTA REDE SUL FIBRA | doc-05 | 1.000 | valor igual (R$ 129,90); pago no dia do vencimento; nome parecido com o memo (100%) |
| 20261012001 | 2026-10-12 | -3.480,00 | PAG BOLETO TECIDOS VALE VERDE LTDA | doc-01 | 1.000 | valor igual (R$ 3.480,00); pago no dia do vencimento; nome parecido com o memo (100%) |
| 20261014001 | 2026-10-14 | -95,00 | PIX ENVIADO LEVA E TRAZ ENTR | doc-07 | 0.889 | valor igual (R$ 95,00); pago no dia do vencimento; nome parecido com o memo (89%) |
| 20261015001 | 2026-10-15 | -412,37 | DEB AUTOMATICO LUZ CAPIXABA | doc-04 | 0.900 | valor igual (R$ 412,37); pago no dia do vencimento; nome parecido com o memo (90%) |
| 20261018001 | 2026-10-18 | -57,80 | COMPRA CARTAO DEB MERCADINHO BOA V | doc-11 | 0.889 | valor igual (R$ 57,80); pago no dia do vencimento; nome parecido com o memo (89%) |
| 20261020001 | 2026-10-20 | 5.200,00 | PIX RECEBIDO LOJA BEM VESTIR LTDA | doc-09 | 1.000 | valor igual (R$ 5.200,00); recebido no dia do vencimento; nome parecido com o memo (100%) |
| 20261030001 | 2026-10-30 | -3.480,00 | PAG BOLETO TECIDOS VALE VERDE LTDA | doc-12 | 1.000 | valor igual (R$ 3.480,00); pago no dia do vencimento; nome parecido com o memo (100%) |
