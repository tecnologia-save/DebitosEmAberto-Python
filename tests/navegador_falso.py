"""Um navegador de mentira — o minimo que `fazer_login` toca antes de desistir.

Nao e um fake completo do Playwright, e nao deve virar um. Ele existe para
alcancar os PRIMEIROS caminhos de saida do login e provar o que e feito com os
recursos ja criados. Tudo depois do clique em 'gov.br' e navegacao real e fica
fora do alcance de teste sem browser — isso esta registrado no relatorio.
"""


class ErroDeNavegacao(Exception):
    """Substitui a familia do patchright nos testes que precisam falhar."""


class LocatorFalso:
    def __init__(self, visivel=True, ao_esperar=None, contagem=0):
        self.visivel = visivel
        self.ao_esperar = ao_esperar
        self.contagem = contagem
        self.cliques = 0

    @property
    def first(self):
        return self

    def wait_for(self, **kwargs):
        if self.ao_esperar:
            raise self.ao_esperar

    def is_visible(self, **kwargs):
        return self.visivel

    def click(self, **kwargs):
        self.cliques += 1

    def evaluate(self, *args, **kwargs):
        return None

    def count(self):
        return self.contagem


class PaginaFalsa:
    def __init__(self, url="https://exemplo.invalido/", ao_navegar=None, locators=None):
        self.url = url
        self.ao_navegar = ao_navegar
        self.locators = locators or {}
        self.navegacoes = []
        self.esperas = []
        self.screenshots = []

    def goto(self, url, **kwargs):
        self.navegacoes.append(url)
        if self.ao_navegar:
            raise self.ao_navegar
        self.url = url

    def locator(self, seletor):
        return self.locators.get(seletor, LocatorFalso(visivel=False))

    def wait_for_timeout(self, ms):
        self.esperas.append(ms)

    def wait_for_load_state(self, *args, **kwargs):
        pass

    def screenshot(self, **kwargs):
        self.screenshots.append(kwargs)

    def go_back(self, **kwargs):
        pass


class ContextoFalso:
    def __init__(self, pagina):
        self.pages = [pagina]
        self.fechado = False

    def close(self):
        self.fechado = True

    def new_page(self):
        return self.pages[0]


class PlaywrightFalso:
    """O objeto devolvido por `sync_playwright().start()`."""

    def __init__(self, pagina, ao_lancar=None):
        self.pagina = pagina
        self.ao_lancar = ao_lancar
        self.parado = False
        self.kwargs_de_lancamento = None
        self.chromium = self

    def launch_persistent_context(self, **kwargs):
        self.kwargs_de_lancamento = kwargs
        if self.ao_lancar:
            raise self.ao_lancar
        self.contexto = ContextoFalso(self.pagina)
        return self.contexto

    def stop(self):
        self.parado = True


class SyncPlaywrightFalso:
    def __init__(self, playwright):
        self._playwright = playwright

    def start(self):
        return self._playwright

    def __call__(self):
        return self
