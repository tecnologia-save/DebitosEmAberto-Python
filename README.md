# Débitos em Aberto — Automação eCAC

Automação Python que consulta o status de **débitos em aberto** de empresas no portal da Receita Federal (eCAC / Serviços RF), usando autenticação por **certificado digital A1 (.pfx)** e resolvendo automaticamente os **hCaptcha** com Gemini AI.

---

## Funcionalidades

- Interface gráfica simples (Tkinter) para seleção da planilha de entrada
- Login no eCAC via certificado digital A1 (`.pfx`) e fluxo gov.br
- Representação de múltiplos CNPJs como Procurador no portal da RF
- Consulta de pendências fiscais para cada CNPJ
- Preenchimento automático do resultado na coluna D da planilha
- Resolução automática de hCaptcha (imagens, grade 3×3, grade fundida, puzzle/slider) via Google Gemini
- Retry inteligente: 2 tentativas por captcha antes de recarregar e repetir a ação
- Agrupamento por certificado para minimizar re-logins
- Intervalo automático de 30s entre trocas de CNPJ (limite do portal)
- Log de erros em `logs/`

---

## Pré-requisitos

| Requisito | Versão |
|-----------|--------|
| Python | 3.11+ |
| Google Chrome | instalado no sistema |
| Certificado digital A1 | arquivo `.pfx` |

### Instalação das dependências

```bash
pip install -r requirements.txt
```

Após instalar o patchright, instale os browsers:

```bash
patchright install chromium
```

---

## Configuração

### 1. Pasta de Certificados

Crie a pasta `C:\Certificados\` (ou uma pasta `Certificados\` ao lado do `main.py`) contendo:

```
C:\Certificados\
├── empresa_a.pfx          # certificado digital A1
├── empresa_b.pfx
└── senhas.json            # senhas dos certificados
```

Formato do `senhas.json`:

```json
{
  "empresa_a.pfx": "senha123",
  "empresa_b.pfx": "outra_senha"
}
```

> **Nota:** A pasta `Certificados/` e o arquivo `senhas.json` estão no `.gitignore` — **nunca commite certificados ou senhas**.

### 2. Chave Google Gemini

Configure a chave de API do Gemini em um arquivo `.env` na raiz do projeto:

```env
GEMINI_API_KEY=sua_chave_aqui
```

Ou defina a variável de ambiente `GEMINI_API_KEY` antes de executar.

Para obter uma chave, acesse [Google AI Studio](https://aistudio.google.com/app/apikey).

---

## Formato da Planilha

Use `PLANILHA MODELO.xlsx` como base. A aba **`Empresas`** deve ter:

| Coluna | Campo | Exemplo |
|--------|-------|---------|
| A | CNPJ | `12345678000195` |
| B | EMPRESA | `Empresa XYZ Ltda` |
| C | CERTIFICADO | `empresa_a.pfx` |
| D | RESULTADO | *(preenchido pela automação)* |

- CNPJs podem ser formatados (`XX.XXX.XXX/XXXX-XX`) ou apenas dígitos
- A coluna C deve conter o **nome exato do arquivo `.pfx`** em `Certificados/`
- A automação ordena por certificado para minimizar re-logins

---

## Execução

### Via Python (desenvolvimento)

```bash
python main.py
```

Ou clique duas vezes em `iniciar.bat`.

### Via executável (.exe)

Execute `Débitos em Aberto.exe` — não requer Python instalado.

Para gerar o executável:

```bash
pip install pyinstaller
pyinstaller debitos_em_aberto.spec
```

O arquivo gerado ficará em `dist/Débitos em Aberto.exe`.

---

## Estrutura do Projeto

```
DebitosEmAberto/
├── main.py                    # Ponto de entrada principal
├── ui_upload.py               # Interface gráfica (Tkinter)
├── rthook_patchright.py       # Hook PyInstaller para browsers
├── debitos_em_aberto.spec     # Spec PyInstaller
├── iniciar.bat                # Atalho de execução Windows
├── requirements.txt           # Dependências Python
├── PLANILHA MODELO.xlsx       # Template da planilha de entrada
├── logo_save.png              # Logo da empresa (UI)
├── debito.ico                 # Ícone do executável
│
├── ecac_login/                # Módulo de login no eCAC
│   ├── __init__.py
│   ├── login.py               # Fluxo de autenticação gov.br + certificado
│   └── log_manager.py         # Registro de erros em arquivo
│
├── captcha_uipath/            # Módulo de resolução de hCaptcha
│   ├── __init__.py
│   ├── solver.py              # Lógica de detecção e resolução
│   └── prompt.md              # Prompt base para o Gemini
│
└── logs/                      # Logs de execução (gerado em runtime)
```

---

## Fluxo de Automação

```
1. Seleção da planilha (UI Tkinter)
       ↓
2. Leitura e ordenação por certificado
       ↓
3. Para cada grupo de certificado:
   ├── Login no eCAC (patchright + certificado .pfx)
   └── Autenticação gov.br (hCaptcha automático se necessário)
       ↓
4. Para cada CNPJ:
   ├── Aguardar 30s desde a última troca de CNPJ
   ├── Clicar "Representar" (hCaptcha automático se necessário)
   ├── Navegar para Pendências Fiscais
   └── Extrair status → gravar na coluna D da planilha
```

---

## Resolução de hCaptcha

O módulo `captcha_uipath` detecta automaticamente o tipo de desafio e usa o **Google Gemini** para resolvê-lo:

| Tipo | Descrição | Estratégia |
|------|-----------|------------|
| `grade` | Grade 3×3 ou 4×4 com tiles separados | Screenshot + grid overlay com coordenadas |
| `grade_fused` | 9 tiles fundidos em uma única imagem | Dois screenshots: iframe completo (contexto) + tiles recortados com grid 3×3 numerado |
| `puzzle` | Peça de encaixe (slider) | Screenshot + arrastar até a posição alvo |
| `checkbox` | Apenas checkbox "Não sou robô" | Clique direto |

**Retry automático:** Se o captcha não for resolvido em 2 tentativas, a automação recarrega a página e repete a ação que originou o captcha.

---

## Variáveis de Ambiente

| Variável | Descrição | Obrigatório |
|----------|-----------|-------------|
| `GEMINI_API_KEY` | Chave de API do Google Gemini | Sim |
| `CERT_PFX_PATH` | Caminho do certificado .pfx | Auto (via planilha) |
| `CERT_PFX_PASSPHRASE` | Senha do certificado | Auto (via senhas.json) |

---

## Possíveis Erros

| Erro | Causa | Solução |
|------|-------|---------|
| `AcessoBloqueado` | eCAC bloqueou acesso automatizado | Aguardar e tentar novamente |
| `DispositivosMaximo` | Máximo de dispositivos conectados | Deslogar outros dispositivos |
| `FalhaPermanente` | Procuração expirada ou CNPJ inválido | Verificar procuração no portal |
| Captcha não resolvido | Gemini não identificou os tiles | Verificar `GEMINI_API_KEY` e debug_screenshots |

---

## Desenvolvimento

Para rodar em modo de desenvolvimento com os pacotes em pastas separadas (layout legado):

```
Automações Python/
├── DebitosEmAberto/    ← este repositório
├── LoginEcac/          ← opcional (legado)
└── CaptchaSolver/      ← opcional (legado)
```

O `main.py` detecta automaticamente qual layout está sendo usado e configura o `sys.path` adequadamente.
