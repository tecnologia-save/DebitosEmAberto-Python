# Validação de runtime Windows — fatia 12E

Este diretório **não faz parte do runtime de produção**. Nada em `automation/`,
`runner.py`, `local.py`, `main.py` ou `cert_windows.py` o importa, e o pytest não
o coleta (`testpaths = ["tests"]` no `pyproject.toml`).

O que ele contém é o harness que exercita as **primitivas reais do produto** num
Windows real, e a matriz de cenários que a fatia 12E pede.

---

## Pré-condição, e ela não é negociável

O harness escreve no registro do Windows e lança processo elevado. Por isso ele
só roda com **confirmação dupla**:

1. variável de ambiente `DEBITOS_VALIDACAO_VM=confirmo-que-esta-vm-e-descartavel`
2. arquivo `_validacao-windows/CONFIRMO_VM_DESCARTAVEL` (conteúdo irrelevante)

As duas metades são deliberadas. Não há como detectar "é uma VM com snapshot" de
forma confiável — um `Manufacturer` não prova rollback — então o harness não
adivinha: ele exige a afirmação de quem tem o snapshot na mão.

**A máquina precisa ser:**

- VM Windows descartável, com snapshot **anterior** aos testes;
- sem certificados de cliente instalados;
- sem policy de Chrome que importe ao operador;
- sem planilha, portal, Gemini ou CNPJ reais.

**Ao final: rollback do snapshot.**

---

## Como rodar

```
# Estrutura do harness, sem tocar em nada. Dispensa a trava.
python _validacao-windows/harness/executar.py --seco

# Na VM, depois da confirmação dupla:
python _validacao-windows/harness/executar.py --grupo lease
python _validacao-windows/harness/executar.py --grupo tudo
```

A evidência sai em `_validacao-windows/evidencias/rodada.md`, sanitizada: sem
usuário, host, caminho pessoal, SID ou documento de 14 dígitos. Os CNs
fictícios são preservados de propósito — mascará-los tornaria a evidência
ilegível.

Código de saída: `0` sem `FAIL_UNSAFE`, `1` com, `2` se a trava recusou.

---

## Grupos automatizados

| Grupo | Seções | Cenários | Precisa de UAC? |
|---|---|---|---|
| `lease` | §5 §6 §30 | criação do Global Event, segunda instância recusada, host devolvido | não |
| `policy` | §9 §11 §12 §13 §14 | guardião elevado escreve HKCU+HKLM, limpeza normal confirmada, HKLM legível pelo processo comum | **sim** |
| `residuo` | §16 | matriz de estado residual real contra `policy_existe()` | não |
| `startup` | §22–§25 | CREATE / BORROW / REFUSE sobre registro real, sem mutação | não |
| `crash` | §17 §18 | parent morto por `TerminateProcess`, guardião segura o lease, host volta depois da limpeza | **sim** |

`--seco` percorre os cinco grupos e classifica tudo como `NOT_TESTED`. É o teste
de fumaça do próprio harness, e roda em qualquer máquina.

---

## O que o harness NÃO automatiza

### §7 · outra sessão do Windows · §8 · outro usuário

Precisam de duas sessões interativas ou de duas contas na mesma VM. O
procedimento é: sessão/conta A roda `--papel segunda-instancia` esperando
adquirir; sessão/conta B roda o mesmo e deve ser recusada.

Sem essa infraestrutura o resultado é **`NOT_TESTED`**, e
`SINGLE_HOST_CONCURRENCY_CONTRACT` fica **parcialmente aberto** — dois processos
na mesma sessão não provam single-host.

### §19 · falha de cleanup induzida

Exige tornar uma colmeia deliberadamente não-removível (ACL). Só com snapshot e
rollback garantidos. Sem isso: **`NOT_TESTED`**. Não substituir por double — o
comportamento já está provado em teste unitário, e aqui a pergunta é outra.

### §29 · §Z · Chrome, perfil, porta 9222 e cleanup de falha do login

**É alcançável sem portal.** Em `servicos_rf_login/login.py`, o primeiro
`page.goto(SERVICOS_RF_URL, ...)` vem logo depois de
`launch_persistent_context`, e o seu tratador de falha é a primeira das sete
saídas que a fatia 12B.1 corrigiu — ela fecha o `context` e para o Playwright.

Procedimento na VM, **sem autenticar**:

1. bloquear a saída de rede da VM, ou apontar o domínio para um endereço morto
   no `hosts`;
2. chamar `fazer_login` com CN fictício e o diretório de perfil de teste;
3. observar: o Chrome abre, o `goto` falha, a função devolve `None`;
4. conferir depois: a porta 9222 não está mais ocupada, e nenhum processo Chrome
   desta execução segura o perfil.

Isso valida `LOGIN_RESOURCE_CLEANUP_GAP` em runtime. Não foi automatizado aqui
porque depende de configuração de rede da VM, que o harness não deve mexer.

### §31 · §32 · UAC recusado e UAC interativo

O grupo `policy` e o grupo `crash` **abrem prompt de UAC**. Alguém precisa
clicar. Para o cenário de recusa, clicar em "Não" e conferir que o resultado é
`ELEVACAO_RECUSADA`, sem policy OWNED falsa e sem liberação indevida do host.

E a pergunta do §32 é outra, e continua valendo mesmo se tudo passar:

- **TECHNICALLY_WORKS** — o mecanismo funciona quando alguém clica;
- **UNATTENDED_COMPATIBLE** — o mecanismo funciona sem ninguém.

São perguntas diferentes. Passar na primeira não responde a segunda.

---

## Regras da rodada

- **§37 · o produto não muda durante a medição.** Se um cenário falhar,
  registrar e parar. Correção é fatia nova. A única exceção é erro do harness
  que não toque no produto.
- **§3 · `NOT_TESTED` não vira `PASS`.** Ambiente que não permite provar é
  ambiente que não provou.
- **§35 · nada de gov.br, e-CAC, hCaptcha, Gemini, certificado real ou CNPJ
  real.** Se um cenário começar a exigir isso, separar o teste.
- **§36 · evidência sanitizada.** O filtro em `comum.sanitizar` é defesa
  adicional, não garantia: a regra que vale é não coletar o que não pode ser
  publicado.
