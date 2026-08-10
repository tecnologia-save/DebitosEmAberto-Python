# Débitos em Aberto — Automação eCAC

Automação Python que consulta o status de **débitos em aberto** de empresas no portal da Receita Federal (eCAC / Serviços RF), usando o **certificado digital já instalado no Windows** e resolvendo automaticamente os **hCaptcha** com Gemini AI.

---

## Funcionalidades

- Interface gráfica simples (Tkinter) para seleção da planilha de entrada
- Login via certificado instalado no Windows (CAPI) e fluxo gov.br
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
| Certificado digital | instalado no Windows |

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

### 1. Certificados

**Não é preciso configurar nada.** A automação lê os certificados já instalados no
Windows (`Cert:\CurrentUser\My`) — os mesmos que aparecem no navegador. Não existe
pasta de `.pfx` nem arquivo de senhas.

Requisitos do certificado para ser considerado:

- ter chave privada
- não estar arquivado
- estar dentro do período de validade

A autenticação é feita pelo próprio Chrome via CAPI: a automação escreve a policy
`AutoSelectCertificateForUrls` no registro, apontando o CN do certificado escolhido,
e o Chrome o apresenta sem exibir diálogo e sem pedir senha.

> **UAC:** ao trocar de certificado, o Windows pede elevação uma vez. Um processo
> guardião mantém a policy enquanto a automação roda e a **remove ao terminar** —
> por fim normal, erro, Ctrl+C ou fechamento da janela — para não deixar o Chrome
> do usuário com auto-seleção presa.

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
| C | CERTIFICADO | `Cristiano` |
| D | RESULTADO | *(preenchido pela automação)* |

- CNPJs podem ser formatados (`XX.XXX.XXX/XXXX-XX`) ou apenas dígitos
- A coluna C aceita o nome do certificado **como você o chama** — não precisa ser
  o nome completo. `Cristiano` encontra `CRISTIANO VASCONCELOS BOAVENTURA LEITE`,
  `GSH` encontra `G S H CONSULTORIAS`, `Cardoso` encontra `EMPRESARIAL CARDOSO LTDA`.
  Acentos e maiúsculas são ignorados
- Se o nome descrever **mais de um** certificado instalado, a automação avisa e
  pula a linha em vez de escolher — usar o certificado errado significaria entrar
  na conta de outra empresa
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
├── resolvedor_captcha/            # Módulo de resolução de hCaptcha
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
   ├── Login (patchright + certificado do Windows via CAPI)
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

O módulo `resolvedor_captcha` detecta automaticamente o tipo de desafio e usa o **Google Gemini** para resolvê-lo:

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
| `GEMINI_API_KEY` | Chave de API do Google Gemini | Sim (embutida no .exe pelo build) |
| `CERT_SUBJECT_CN` | CN do certificado do Windows | Auto (via planilha) |

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
└── ResolvedorCaptcha/      ← opcional (legado)
```

O `main.py` detecta automaticamente qual layout está sendo usado e configura o `sys.path` adequadamente.
