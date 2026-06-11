import os
import tkinter as tk
from tkinter import filedialog, messagebox

# ── Paleta ────────────────────────────────────────────────────────────────────
C_BLACK  = "#111111"
C_YELLOW = "#ffcc00"
C_YHOVER = "#e6b800"
C_WHITE  = "#ffffff"
C_CARD   = "#f7f7f7"
C_BORDER = "#e0e0e0"
C_TEXT   = "#111111"
C_MUTED  = "#aaaaaa"
C_SUB    = "#666666"

FONT = "Segoe UI"
VALID_EXTENSIONS = {".xlsx", ".xls", ".csv"}


# ── Utilitários de desenho ────────────────────────────────────────────────────

def create_rounded_rect(canvas, x1, y1, x2, y2, radius=20, **kwargs):
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


# ── Componentes de botão ──────────────────────────────────────────────────────

class RoundedButton(tk.Canvas):
    """Botão arredondado de largura fixa."""

    def __init__(self, parent, text, command, bg, fg, activebg,
                 width=120, height=40, radius=20, font_size=10):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bg=parent["bg"])
        self.command = command
        self._bg      = bg
        self._activebg = activebg

        self._rect  = create_rounded_rect(self, 0, 0, width, height,
                                          radius=radius, fill=bg, outline=bg)
        self._label = self.create_text(width // 2, height // 2, text=text,
                                       fill=fg, font=(FONT, font_size, "bold"))

        self.bind("<Button-1>", lambda _: self.command())
        self.bind("<Enter>",    self._on_enter)
        self.bind("<Leave>",    self._on_leave)

    def _on_enter(self, _):
        self.itemconfig(self._rect, fill=self._activebg, outline=self._activebg)

    def _on_leave(self, _):
        self.itemconfig(self._rect, fill=self._bg, outline=self._bg)


class StretchButton(tk.Canvas):
    """Botão arredondado que se estica para preencher toda a largura disponível."""

    def __init__(self, parent, text, command, bg, fg, activebg,
                 height=46, radius=23, font_size=11):
        super().__init__(parent, height=height,
                         highlightthickness=0, bg=parent["bg"])
        self.command   = command
        self._bg       = bg
        self._activebg = activebg
        self._fg       = fg
        self._text     = text
        self._h        = height
        self._radius   = radius
        self._font_sz  = font_size
        self._rect_id  = None

        self.bind("<Configure>", self._redraw)
        self.bind("<Button-1>",  lambda _: self.command())
        self.bind("<Enter>",     self._on_enter)
        self.bind("<Leave>",     self._on_leave)

    def _redraw(self, _=None):
        self.delete("all")
        w = self.winfo_width()
        if w < 4:
            return
        self._rect_id = create_rounded_rect(
            self, 0, 0, w, self._h, radius=self._radius,
            fill=self._bg, outline=self._bg,
        )
        self.create_text(w // 2, self._h // 2, text=self._text,
                         fill=self._fg, font=(FONT, self._font_sz, "bold"))

    def _on_enter(self, _):
        if self._rect_id:
            self.itemconfig(self._rect_id, fill=self._activebg, outline=self._activebg)

    def _on_leave(self, _):
        if self._rect_id:
            self.itemconfig(self._rect_id, fill=self._bg, outline=self._bg)


# ── Aplicação principal ───────────────────────────────────────────────────────

class App:
    def __init__(self, root):
        self.root = root
        root.title("Débitos em Aberto")
        root.resizable(False, False)
        root.configure(bg=C_WHITE)

        self.file_path = tk.StringVar()
        self.file_path.trace_add("write", self._on_file_changed)

        self._build_header()
        self._build_body()
        self._center_window()

    # ── Construção do layout ──────────────────────────────────────────────────

    def _build_header(self):
        """Cabeçalho escuro com logo, título e faixa amarela."""
        header = tk.Frame(self.root, bg=C_BLACK)
        header.pack(fill="x")

        inner = tk.Frame(header, bg=C_BLACK)
        inner.pack(padx=28, pady=(22, 18), anchor="w")

        self.logo_image = self._load_logo(max_height=26)
        if self.logo_image:
            tk.Label(inner, image=self.logo_image,
                     bg=C_BLACK).pack(side="left", padx=(0, 12))

        tk.Label(
            inner,
            text="Débitos em Aberto",
            font=(FONT, 14, "bold"),
            fg=C_WHITE,
            bg=C_BLACK,
        ).pack(side="left")

        tk.Frame(self.root, bg=C_YELLOW, height=4).pack(fill="x")

    def _build_body(self):
        """Área de conteúdo: badge de passo, descrição, card de arquivo e botão."""
        body = tk.Frame(self.root, bg=C_WHITE)
        body.pack(padx=28, pady=28, fill="both", expand=True)

        # ── Título ────────────────────────────────────────────────────────────
        tk.Label(
            body,
            text="Selecione a planilha no padrão PLANILHA MODELO.xlsx",
            font=(FONT, 10, "bold"),
            fg=C_TEXT,
            bg=C_WHITE,
            wraplength=400,
            justify="left",
        ).pack(anchor="w", pady=(0, 18))

        # ── Card de seleção de arquivo ────────────────────────────────────────
        self._build_file_card(body)

        # ── Botão Enviar (ocupa toda a largura) ───────────────────────────────
        tk.Frame(body, bg=C_WHITE, height=8).pack()
        StretchButton(
            body,
            text="Enviar",
            command=self.submit,
            bg=C_YELLOW,
            fg=C_BLACK,
            activebg=C_YHOVER,
            height=46,
            radius=23,
            font_size=11,
        ).pack(fill="x")

    def _build_file_card(self, parent):
        """Card com borda fina, ícone, nome do arquivo e botão Procurar."""
        # Wrapper de 1 px simula borda
        border = tk.Frame(parent, bg=C_BORDER)
        border.pack(fill="x")

        card = tk.Frame(border, bg=C_CARD)
        card.pack(padx=1, pady=1, fill="x")

        # Ícone da planilha (muda de cor ao selecionar)
        self._icon_lbl = tk.Label(card, text="📊", font=(FONT, 32),
                                  fg=C_MUTED, bg=C_CARD)
        self._icon_lbl.pack(pady=(30, 6))

        # Nome do arquivo / placeholder
        self._name_lbl = tk.Label(
            card,
            text="Nenhum arquivo selecionado",
            font=(FONT, 9),
            fg=C_MUTED,
            bg=C_CARD,
        )
        self._name_lbl.pack(pady=(0, 20))

        # Botão Procurar
        btn_wrap = tk.Frame(card, bg=C_CARD)
        btn_wrap.pack(pady=(0, 30))
        RoundedButton(
            btn_wrap,
            text="Procurar arquivo",
            command=self.browse_file,
            bg=C_YELLOW,
            fg=C_BLACK,
            activebg=C_YHOVER,
            width=160,
            height=38,
            radius=19,
            font_size=9,
        ).pack()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_logo(self, max_height=26):
        """Carrega e redimensiona o logo; retorna None se não encontrar."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logo_save.png")
        if not os.path.exists(path):
            return None
        try:
            img = tk.PhotoImage(file=path)
            if img.height() > max_height:
                ratio = (img.height() + max_height - 1) // max_height
                img = img.subsample(ratio, ratio)
            return img
        except Exception:
            return None

    def _center_window(self):
        """Centraliza a janela na tela após o layout ser calculado."""
        self.root.update_idletasks()
        w  = self.root.winfo_width()
        h  = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _on_file_changed(self, *_):
        """Atualiza o card quando um arquivo é selecionado ou limpo."""
        path = self.file_path.get()
        if path:
            self._icon_lbl.config(fg=C_YELLOW)
            self._name_lbl.config(
                text=os.path.basename(path),
                fg=C_TEXT,
                font=(FONT, 9, "bold"),
            )
        else:
            self._icon_lbl.config(fg=C_MUTED)
            self._name_lbl.config(
                text="Nenhum arquivo selecionado",
                fg=C_MUTED,
                font=(FONT, 9),
            )

    # ── Ações ─────────────────────────────────────────────────────────────────

    def browse_file(self):
        path = filedialog.askopenfilename(
            title="Selecione a planilha",
            filetypes=[
                ("Planilhas",        "*.xlsx *.xls *.csv"),
                ("Excel",            "*.xlsx *.xls"),
                ("CSV",              "*.csv"),
                ("Todos os arquivos","*"),
            ],
        )
        if path:
            self.file_path.set(path)

    def submit(self):
        path = self.file_path.get().strip()

        if not path:
            messagebox.showwarning("Aviso", "Selecione uma planilha antes de enviar.")
            return

        if not os.path.isfile(path):
            messagebox.showwarning("Aviso", "O caminho informado não corresponde a um arquivo válido.")
            return

        ext = os.path.splitext(path)[1].lower()
        if ext not in VALID_EXTENSIONS:
            messagebox.showwarning(
                "Formato inválido",
                f"Extensão '{ext}' não é suportada.\nUse arquivos .xlsx, .xls ou .csv.",
            )
            return

        self.selected_path = path
        self.root.destroy()


# ── Entrada ───────────────────────────────────────────────────────────────────

def main() -> str | None:
    """Abre a janela de seleção e retorna o caminho da planilha escolhida,
    ou None se o usuário fechar sem confirmar."""
    root = tk.Tk()
    app = App(root)
    root.mainloop()
    return getattr(app, "selected_path", None)


if __name__ == "__main__":
    planilha = main()
    if planilha:
        print(f"Planilha selecionada: {planilha}")
