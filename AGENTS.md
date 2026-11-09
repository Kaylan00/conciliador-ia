# AGENTS.md: regras para quem for mexer (gente ou agente)

## O que é

Assistente de escrituração para pequena empresa: lê documentos fiscais (XML de NF-e, PDF com texto,
recibo escaneado ou fotografado), lê o extrato bancário (OFX ou CSV), concilia cada transação com o
documento certo e sugere os lançamentos em partidas dobradas. Nada vira lançamento aprovado sem uma
pessoa conferir: o sistema mostra por que sugeriu cada coisa e o que o deixou em dúvida.

## Mapa do código

| Caminho | Responsável | O que faz |
| --- | --- | --- |
| `src/conciliador/modelos.py` | contrato | Tipos compartilhados (Pydantic). Mudou aqui, mudou para todos: abra o PR só com isso primeiro. |
| `src/conciliador/documentos/` | módulo documentos | `ler_documento(conteudo: bytes, nome_arquivo: str) -> DocumentoExtraido`. Detecta o formato, lê XML de NF-e, extrai texto de PDF, faz OCR quando não há texto, extrai os campos e calcula a confiança. |
| `src/conciliador/extracao/` | módulo documentos | Provedores de extração de campos a partir de texto: `regras` (regex, determinístico, sem custo) e `llm` (modelo com saída validada por schema). |
| `src/conciliador/extrato/` | módulo conciliação | `ler_extrato(conteudo: bytes, nome_arquivo: str) -> list[TransacaoBancaria]` para OFX 1.x (SGML), OFX 2.x (XML) e CSV. |
| `src/conciliador/conciliacao.py` | módulo conciliação | `conciliar(documentos: dict[str, DocumentoExtraido], transacoes: list[TransacaoBancaria]) -> ResultadoConciliacao`. |
| `src/conciliador/lancamentos.py` | núcleo | Classificação no plano de contas e geração dos lançamentos. |
| `src/conciliador/api.py`, `web/` | núcleo | FastAPI, persistência e a tela de revisão. |
| `dados/exemplos/cenario.json` | todos | **Fonte única** dos dados de exemplo e do gabarito. Geradores leem daqui; testes e avaliação comparam com daqui. |
| `dados/plano_de_contas.json` | núcleo | Plano de contas simplificado. |
| `scripts/` | cada módulo o seu | Geradores dos arquivos de exemplo e a avaliação. |

## Regras

1. **Dinheiro é `Decimal`**, nunca float. CNPJ só com dígitos. Datas como `date`.
2. **Nada de rede nos testes.** Provedor de LLM é testado com transporte HTTP falso (`httpx.MockTransport`).
3. **Confiança se calcula, não se pergunta ao modelo.** Cada desconto vira um `Problema` com código, mensagem
   que a pessoa da revisão entende e penalidade.
4. **O modelo não inventa:** campo que não está no documento fica `None`. A validação depois decide.
5. **Funções puras no meio, I/O nas bordas.** Leitura de arquivo, banco e HTTP ficam fora da regra de negócio.
6. Nomes em português, como no resto do projeto. Comentário só quando explica um porquê.
7. Segredo nunca no repositório: chave de API só por variável de ambiente (`OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`).

## Como trabalhamos em paralelo

- Cada tarefa roda numa **git worktree** própria, numa branch `feat/<assunto>`, a partir da `main`:
  `git worktree add ../conciliador-ia-<assunto> -b feat/<assunto>`.
- Cada módulo só mexe nos arquivos da sua linha na tabela acima. Precisa mudar o contrato? Pare e avise.
- Entrega = **pull request** para a `main` com testes passando no CI e descrição do que foi feito, do que ficou
  de fora e de como testar. Ninguém faz merge do próprio PR sem revisão.

## Comandos

```bash
docker build -f Dockerfile.dev -t conciliador-dev .            # Python 3.12 + Tesseract (por)
docker run --rm -v "$PWD":/app -w /app conciliador-dev pytest -q
docker run --rm -v "$PWD":/app -w /app conciliador-dev ruff check src tests
docker run --rm -v "$PWD":/app -w /app conciliador-dev python scripts/avaliar.py
```

## Pronto quer dizer

- Testes novos cobrindo o caminho feliz **e** as armadilhas listadas em `cenario.json` (`_armadilhas`).
- `ruff check` limpo.
- O módulo funciona com os arquivos de `dados/exemplos/` gerados pelos scripts.
