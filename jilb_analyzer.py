"""Анализатор метрик Джилба (и числа Маккейба) для программ на Kotlin.

CL  – абсолютная сложность: число условий (if, else if, ветки when, for, while, do-while).
      when с n ветками эквивалентен n-1 вложенным if, поэтому считаются только
      ветки с условием; селектор when и ветка else НЕ являются условиями.
cl  – относительная сложность: CL / общее число операторов программы.
CLI – максимальный уровень вложенности условных операторов.
      when с k условными ветками = цепочка из k вложенных if (else-if),
      поэтому i-я ветка лежит на уровне L+i, а ветка else – на уровне L+k.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    tk = None


# ───────────────────────── лексер ─────────────────────────

@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int
    line: int
    nl_before: bool


TOKEN_RE = re.compile(
    r'(?P<RAW_STRING>"""[\s\S]*?""")'
    r'|(?P<STRING>"(?:\\.|[^"\\\n])*")'
    r"|(?P<CHAR>'(?:\\u[0-9A-Fa-f]{4}|\\.|[^'\\\n])')"
    r'|(?P<NUMBER>(?:0[xX][0-9A-Fa-f_]+|0[bB][01_]+|'
    r'(?:\d[\d_]*\.\d[\d_]*|\d[\d_]*)(?:[eE][+-]?\d[\d_]*)?)[fFdDuUlL]*)'
    r'|(?P<BACKTICK>`[^`]+`)'
    r'|(?P<OP>===|!==|!!|\.\.<|\.\.|\?\.|\?:|::|->|'
    r'==|!=|<=|>=|&&|\|\||\+\+|--|\+=|-=|\*=|/=|%=|&=|\|=|\^=|as\?)'
    r'|(?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)'
    r'|(?P<SYMBOL>[()\[\]{}+\-*/%=<>!&|^~?:.,;@])'
    r'|(?P<WS>\s+)'
    r'|(?P<OTHER>.)'
)


def scan_string(src: str, start: int) -> int:
    """Индекс после закрывающей кавычки; учитывает экранирование и ${...}."""
    j = start + 1
    while j < len(src):
        ch = src[j]
        if ch == "\\":
            j += 2
        elif ch == '"':
            return j + 1
        elif ch == "\n":
            return j
        elif ch == "$" and src[j + 1:j + 2] == "{":
            depth, j = 1, j + 2
            while j < len(src) and depth:
                inner = src[j]
                if inner == '"':
                    j = scan_string(src, j)
                    continue
                if inner == "{":
                    depth += 1
                elif inner == "}":
                    depth -= 1
                j += 1
        else:
            j += 1
    return j


def strip_comments(code: str) -> str:
    """Удаляет // и вложенные /* */ комментарии, сохраняя переводы строк."""
    out: list[str] = []
    i, state, nesting = 0, "code", 0
    while i < len(code):
        ch, pair, triple = code[i], code[i:i + 2], code[i:i + 3]
        if state == "raw":
            out.append(ch)
            if triple == '"""':
                out.append(code[i + 1:i + 3])
                i += 3
                state = "code"
            else:
                i += 1
            continue
        if state == "char":
            out.append(ch)
            if ch == "\\" and i + 1 < len(code):
                out.append(code[i + 1])
                i += 2
            else:
                if ch == "'":
                    state = "code"
                i += 1
            continue
        if state == "block":
            if pair == "/*":
                nesting += 1
                i += 2
            elif pair == "*/":
                nesting -= 1
                i += 2
                if nesting == 0:
                    state = "code"
            else:
                if ch == "\n":
                    out.append("\n")
                i += 1
            continue
        if triple == '"""':
            out.append(triple)
            i += 3
            state = "raw"
        elif ch == '"':
            end = scan_string(code, i)
            out.append(code[i:end])
            i = end
        elif ch == "'":
            out.append(ch)
            i += 1
            state = "char"
        elif pair == "//":
            i += 2
            while i < len(code) and code[i] != "\n":
                i += 1
        elif pair == "/*":
            state, nesting = "block", 1
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    pos, line, nl = 0, 1, False
    while pos < len(source):
        if source[pos] == '"' and not source.startswith('"""', pos):
            end = scan_string(source, pos)
            tokens.append(Token("STRING", source[pos:end], pos, end, line, nl))
            nl = False
            pos = end
            continue
        m = TOKEN_RE.match(source, pos)
        kind, text = m.lastgroup, m.group()
        if kind == "WS":
            if "\n" in text:
                nl = True
                line += text.count("\n")
        elif kind != "OTHER":
            tokens.append(Token(kind, text, m.start(), m.end(), line, nl))
            nl = False
            line += text.count("\n")
        pos = m.end()
    return tokens


# ───────────────────────── разбор ─────────────────────────

MODIFIERS = {
    "abstract", "open", "override", "private", "protected", "public", "internal", "final",
    "inline", "noinline", "crossinline", "tailrec", "operator", "infix", "suspend", "const",
    "lateinit", "data", "sealed", "enum", "annotation", "companion", "inner", "external",
    "expect", "actual",
}
LABEL_REF = {"return", "break", "continue", "this", "super"}
CONT_PREV = {
    "+", "-", "*", "/", "%", "=", "==", "!=", "===", "!==", "<=", ">=", "&&", "||", ".", "?.",
    "?:", "::", ",", "(", "[", "->", "+=", "-=", "*=", "/=", "%=", "..", "..<", "!", "&", "|",
    "^", "in", "is", "as", "as?", "by", ":",
}
CONT_NEXT = {".", "?.", "?:", "&&", "||", "as", "as?"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "++", "--"}


@dataclass
class Condition:
    kind: str
    line: int
    level: int
    code: str


@dataclass
class OperatorItem:
    """Один оператор программы (то, что попадает в знаменатель cl)."""
    kind: str
    line: int
    code: str
    pos: int


@dataclass
class JilbResult:
    conditions: list[Condition] = field(default_factory=list)
    items: list[OperatorItem] = field(default_factory=list)
    by_kind: Counter = field(default_factory=Counter)

    @property
    def operators(self) -> int:
        return len(self.items)

    @property
    def op_kinds(self) -> Counter:
        return Counter(i.kind for i in self.items)

    @property
    def CL(self) -> int:
        return len(self.conditions)

    @property
    def cl(self) -> float:
        return self.CL / self.operators if self.operators else 0.0

    @property
    def cli(self) -> int:
        """Максимальный уровень вложенности (внешнее условие – уровень 0, как в методичке)."""
        return max((c.level for c in self.conditions), default=0)


class Analyzer:
    def __init__(self, source: str) -> None:
        self.lines = source.splitlines()
        self.t = tokenize(strip_comments(source))
        self.n = len(self.t)
        self.p = 0
        self.res = JilbResult()

    # --- вспомогательное ---
    def val(self, k: int = 0):
        i = self.p + k
        return self.t[i].value if 0 <= i < self.n else None

    def line_text(self, tok: Token) -> str:
        return self.lines[tok.line - 1].strip() if tok.line - 1 < len(self.lines) else ""

    def add_op(self, kind: str, tok: Token) -> None:
        self.res.items.append(OperatorItem(kind, tok.line, self.line_text(tok), tok.start))

    def register(self, kind: str, tok: Token, level: int) -> None:
        self.res.conditions.append(Condition(kind, tok.line, level, self.line_text(tok)))
        self.res.by_kind[kind] += 1
        group = {"for": "цикл", "while": "цикл", "do-while": "цикл"}.get(kind, "условие")
        label = "ветка when" if kind == "when-ветка" else kind
        self.add_op(f"{group} ({label})", tok)

    def add_simple(self, start: int, kind: str | None = None) -> None:
        """Регистрирует простой оператор, занимающий токены [start, self.p)."""
        if self.p <= start:
            return
        if kind is None:
            first = self.t[start].value
            if first in ("val", "var"):
                kind = "объявление переменной"
            elif first in ("return", "break", "continue", "throw"):
                kind = f"переход ({first})"
            else:
                kind = "вызов / выражение"
                depth = 0
                for tk_ in self.t[start:self.p]:
                    if tk_.value in ("(", "[", "{"):
                        depth += 1
                    elif tk_.value in (")", "]", "}"):
                        depth -= 1
                    elif depth == 0 and tk_.value in ASSIGN_OPS:
                        kind = "присваивание"
                        break
        self.add_op(kind, self.t[start])

    def skip_group(self) -> None:
        depth = 0
        while self.p < self.n:
            v = self.t[self.p].value
            if v in ("(", "["):
                depth += 1
            elif v in (")", "]"):
                depth -= 1
                if depth <= 0:
                    self.p += 1
                    return
            self.p += 1

    def close_brace(self) -> None:
        if self.val() == "}":
            self.p += 1

    @staticmethod
    def ends_statement(prev: Token, nxt: Token) -> bool:
        return prev.value not in CONT_PREV and nxt.value not in CONT_NEXT

    # --- верхний уровень ---
    def run(self) -> JilbResult:
        while self.p < self.n:
            before = self.p
            self.statement(0)
            if self.p == before:
                self.p += 1
        self.res.conditions.sort(key=lambda c: c.line)
        self.res.items.sort(key=lambda i: i.pos)
        return self.res

    def block(self, level: int) -> None:
        while self.p < self.n and self.val() != "}":
            before = self.p
            self.statement(level)
            if self.p == before:
                self.p += 1

    def body(self, level: int) -> None:
        if self.val() == "{":
            self.p += 1
            self.block(level)
            self.close_brace()
        else:
            self.statement(level)

    # --- операторы ---
    def statement(self, level: int) -> None:
        if self.p >= self.n:
            return
        tok = self.t[self.p]
        v = tok.value

        if v in (";", "}"):
            self.p += 1
            return
        # метка  loop@ for ...
        if tok.kind == "IDENT" and self.val(1) == "@" and v not in LABEL_REF \
                and self.t[self.p + 1].start == tok.end:
            self.p += 2
            return self.statement(level)
        # аннотация
        if v == "@":
            self.p += 1
            if self.p < self.n and self.t[self.p].kind == "IDENT":
                self.p += 1
            if self.val() == "(":
                self.skip_group()
            return self.statement(level)
        # модификаторы
        if v in MODIFIERS and self.p + 1 < self.n and self.t[self.p + 1].kind == "IDENT":
            self.p += 1
            return self.statement(level)

        if v in ("package", "import"):
            self.p += 1
            while self.p < self.n and not self.t[self.p].nl_before:
                self.p += 1
            return
        if v == "fun":
            return self.function(level)
        if v in ("class", "object", "interface"):
            return self.type_decl()
        if v == "if":
            return self.if_stmt(level, "if")
        if v in ("for", "while"):
            self.p += 1
            if self.val() == "(":
                self.skip_group()
            self.register(v, tok, level)
            return self.body(level + 1)
        if v == "do":
            self.p += 1
            self.body(level + 1)
            if self.val() == "while":
                wtok = self.t[self.p]
                self.p += 1
                if self.val() == "(":
                    self.skip_group()
                self.register("do-while", wtok, level)
            return
        if v == "when":
            return self.when(level, as_expr=False)
        if v == "try":
            self.p += 1
            self.body(level)
            while self.val() in ("catch", "finally"):
                is_catch = self.val() == "catch"
                self.p += 1
                if is_catch and self.val() == "(":
                    self.skip_group()
                self.body(level)
            return

        # простой оператор (объявление, присваивание, вызов, return, break ...)
        start = self.p
        self.scan_expr(level)
        self.add_simple(start)

    def function(self, level: int) -> None:
        self.p += 1
        while self.p < self.n and self.val() != "(":
            self.p += 1
        if self.val() == "(":
            self.skip_group()
        first = True
        while self.p < self.n:
            tok = self.t[self.p]
            if tok.value == "{":
                self.p += 1
                self.block(level)
                self.close_brace()
                return
            if tok.value == "=":
                self.p += 1
                start = self.p
                self.scan_expr(level)
                self.add_simple(start, "тело функции (= выражение)")
                return
            if tok.nl_before and not first:
                return  # функция без тела
            first = False
            self.p += 1

    def type_decl(self) -> None:
        self.p += 1
        first = True
        while self.p < self.n:
            tok = self.t[self.p]
            if tok.value == "(":
                self.skip_group()
                continue
            if tok.value == "{":
                self.p += 1
                self.block(0)
                self.close_brace()
                return
            if tok.nl_before and not first and tok.value not in (":", "where"):
                return
            first = False
            self.p += 1

    def if_stmt(self, level: int, kind: str) -> None:
        tok = self.t[self.p]
        self.p += 1
        if self.val() == "(":
            self.skip_group()
        self.register(kind, tok, level)
        self.body(level + 1)
        if self.val() == "else" and self.val(1) != "->":
            self.p += 1
            if self.val() == "if":
                self.if_stmt(level + 1, "else if")
            else:
                self.body(level + 1)

    def when(self, level: int, as_expr: bool) -> None:
        self.p += 1
        if self.val() == "(":
            self.skip_group()
        if self.val() != "{":
            return
        self.p += 1
        idx = 0
        while self.p < self.n and self.val() != "}":
            first = self.t[self.p]
            if first.value == "else" and self.val(1) == "->":
                self.p += 2
                body_level = level + idx          # ветка по умолчанию: не условие
            else:
                depth = 0
                while self.p < self.n:
                    v = self.val()
                    if v in ("(", "["):
                        depth += 1
                    elif v in (")", "]"):
                        depth -= 1
                    elif v == "->" and depth == 0:
                        break
                    elif v == "}" and depth <= 0:
                        break
                    self.p += 1
                if self.val() != "->":
                    break
                self.p += 1
                self.register("when-ветка", first, level + idx)
                body_level = level + idx + 1
                idx += 1
            if self.val() == "{":
                self.p += 1
                self.block(body_level)
                self.close_brace()
            elif as_expr:
                self.scan_expr(body_level)
            else:
                self.statement(body_level)
            while self.val() in (";", ","):
                self.p += 1
        self.close_brace()

    # --- выражения ---
    def scan_expr(self, level: int, stop_comma: bool = False) -> None:
        depth = 0
        start = self.p
        prev = None
        while self.p < self.n:
            tok = self.t[self.p]
            v = tok.value
            if depth == 0:
                if self.p > start and tok.nl_before and self.ends_statement(prev, tok):
                    break
                if v in (";", "}", "else"):
                    break
                if v == "," and stop_comma:
                    break
            if v in (")", "]"):
                if depth == 0:
                    break
                depth -= 1
            elif v in ("(", "["):
                depth += 1
            elif v == "{":
                self.lambda_(level)
                prev = self.t[self.p - 1]
                continue
            elif v == "if":
                self.if_expr(level, "if")
                prev = self.t[self.p - 1]
                continue
            elif v == "when":
                self.when(level, as_expr=True)
                prev = self.t[self.p - 1]
                continue
            prev = tok
            self.p += 1

    def lambda_(self, level: int) -> None:
        self.p += 1
        j = self.p
        while j < self.n:
            tok = self.t[j]
            if tok.value == "->":
                self.p = j + 1
                break
            if tok.kind in ("IDENT", "BACKTICK") or tok.value in (",", ":", "(", ")", "<", ">", "?", ".", "*"):
                j += 1
            else:
                break
        self.block(level)
        self.close_brace()

    def if_expr(self, level: int, kind: str) -> None:
        tok = self.t[self.p]
        self.p += 1
        if self.val() == "(":
            self.skip_group()
        self.register(kind, tok, level)
        self.branch(level + 1)
        if self.val() == "else" and self.val(1) != "->":
            self.p += 1
            if self.val() == "if":
                self.if_expr(level + 1, "else if")
            else:
                self.branch(level + 1)

    def branch(self, level: int) -> None:
        if self.val() == "{":
            self.p += 1
            self.block(level)
            self.close_brace()
        else:
            self.scan_expr(level, stop_comma=True)


def analyze(source: str) -> JilbResult:
    return Analyzer(source).run()


# ───────────────────────── интерфейс ─────────────────────────

class JilbApp(tk.Tk if tk else object):
    def __init__(self) -> None:
        if tk is None:
            raise RuntimeError("Tkinter недоступен. Установите Python с поддержкой Tk.")
        super().__init__()
        self.title("Анализатор метрик Джилба – Kotlin")
        self.geometry("1500x820")
        self.minsize(1100, 650)
        self._build_ui()

    def _build_ui(self) -> None:
        controls = ttk.Frame(self, padding=10)
        controls.pack(fill=tk.X)
        ttk.Button(controls, text="Открыть .kt", command=self.open_file).pack(side=tk.LEFT)
        ttk.Button(controls, text="Рассчитать метрики", command=self.calculate).pack(side=tk.LEFT, padx=8)
        ttk.Button(controls, text="Очистить", command=self.clear).pack(side=tk.LEFT)

        pane = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        src = ttk.LabelFrame(pane, text="Исходный код Kotlin", padding=6)
        src.rowconfigure(0, weight=1)
        src.columnconfigure(0, weight=1)
        self.code_text = tk.Text(src, wrap=tk.NONE, font=("Consolas", 11), undo=True)
        self.code_text.bind("<Control-v>", self.paste_from_clipboard)
        self.code_text.bind("<Control-V>", self.paste_from_clipboard)
        self.code_text.bind("<Shift-Insert>", self.paste_from_clipboard)
        self.code_text.bind("<Button-3>", self.show_context_menu)
        sy = ttk.Scrollbar(src, orient=tk.VERTICAL, command=self.code_text.yview)
        sx = ttk.Scrollbar(src, orient=tk.HORIZONTAL, command=self.code_text.xview)
        self.code_text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        self.code_text.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        pane.add(src, weight=1)

        res = ttk.Frame(pane)
        res.rowconfigure(0, weight=1)
        res.columnconfigure(0, weight=1)
        tabs = ttk.Notebook(res)
        tabs.grid(row=0, column=0, sticky="nsew")
        cond_tab = ttk.Frame(tabs, padding=4)
        ops_tab = ttk.Frame(tabs, padding=4)
        tabs.add(cond_tab, text="Условия и циклы (CL)")
        tabs.add(ops_tab, text="Операторы программы (N)")
        self.table = self._make_table(
            cond_tab,
            ("n", "kind", "line", "level", "code"),
            {"n": "№", "kind": "Тип", "line": "Строка", "level": "Уровень", "code": "Код"},
            {"n": 40, "kind": 100, "line": 60, "level": 70, "code": 380},
        )
        self.ops_table = self._make_table(
            ops_tab,
            ("n", "kind", "line", "code"),
            {"n": "№", "kind": "Вид оператора", "line": "Строка", "code": "Код"},
            {"n": 45, "kind": 190, "line": 60, "code": 400},
        )

        box = ttk.LabelFrame(res, text="Метрики Джилба", padding=8)
        box.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.metrics_var = tk.StringVar(value="Метрики будут показаны после анализа.")
        ttk.Label(box, textvariable=self.metrics_var, justify=tk.LEFT, font=("Segoe UI", 10)).pack(anchor="w")
        pane.add(res, weight=1)

        self.status_var = tk.StringVar(value="Вставьте Kotlin-код слева и нажмите «Рассчитать метрики».")
        ttk.Label(self, textvariable=self.status_var, padding=(12, 8)).pack(fill=tk.X)

    def _make_table(self, parent, cols, heads, widths) -> "ttk.Treeview":
        parent.rowconfigure(0, weight=1)
        parent.columnconfigure(0, weight=1)
        table = ttk.Treeview(parent, columns=cols, show="headings", height=20)
        for c in cols:
            table.heading(c, text=heads[c])
            table.column(c, width=widths[c], anchor=tk.W if c in ("kind", "code") else tk.CENTER)
        table.bind("<<TreeviewSelect>>", lambda e, t=table: self.on_select_row(t))
        ty = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=table.yview)
        tx = ttk.Scrollbar(parent, orient=tk.HORIZONTAL, command=table.xview)
        table.configure(yscrollcommand=ty.set, xscrollcommand=tx.set)
        table.grid(row=0, column=0, sticky="nsew")
        ty.grid(row=0, column=1, sticky="ns")
        tx.grid(row=1, column=0, sticky="ew")
        return table

    # --- работа с текстом ---
    def paste_from_clipboard(self, event=None) -> str:
        try:
            text = self.clipboard_get()
            if self.code_text.tag_ranges(tk.SEL):
                self.code_text.delete(tk.SEL_FIRST, tk.SEL_LAST)
            self.code_text.insert(tk.INSERT, text)
        except tk.TclError:
            pass
        return "break"

    def show_context_menu(self, event) -> str:
        self.code_text.focus_set()
        self.code_text.mark_set(tk.INSERT, f"@{event.x},{event.y}")
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Вырезать", command=lambda: self.code_text.event_generate("<<Cut>>"))
        menu.add_command(label="Копировать", command=lambda: self.code_text.event_generate("<<Copy>>"))
        menu.add_command(label="Вставить", command=self.paste_from_clipboard)
        menu.add_separator()
        menu.add_command(label="Выделить всё", command=lambda: self.code_text.tag_add(tk.SEL, "1.0", tk.END))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def open_file(self) -> None:
        path = filedialog.askopenfilename(title="Выберите Kotlin-файл",
                                          filetypes=[("Kotlin files", "*.kt"), ("All files", "*.*")])
        if not path:
            return
        for enc in ("utf-8", "utf-8-sig", "cp1251"):
            try:
                with open(path, "r", encoding=enc) as f:
                    source = f.read()
                self.code_text.delete("1.0", tk.END)
                self.code_text.insert("1.0", source)
                self.status_var.set(f"Загружен файл: {path}")
                return
            except UnicodeDecodeError:
                continue
            except OSError as e:
                messagebox.showerror("Ошибка открытия", str(e))
                return
        messagebox.showerror("Ошибка открытия", "Не удалось определить кодировку файла.")

    def on_select_row(self, table) -> None:
        sel = table.selection()
        if not sel:
            return
        line = table.item(sel[0], "values")[2]
        self.code_text.tag_remove("hl", "1.0", tk.END)
        self.code_text.tag_configure("hl", background="#fff3b0")
        self.code_text.tag_add("hl", f"{line}.0", f"{line}.end")
        self.code_text.see(f"{line}.0")

    def calculate(self) -> None:
        source = self.code_text.get("1.0", tk.END)
        if not source.strip():
            messagebox.showwarning("Нет кода", "Введите Kotlin-код или откройте файл .kt.")
            return
        r = analyze(source)
        self.table.delete(*self.table.get_children())
        self.ops_table.delete(*self.ops_table.get_children())
        for i, c in enumerate(r.conditions, 1):
            self.table.insert("", tk.END, values=(i, c.kind, c.line, c.level, c.code))
        for i, it in enumerate(r.items, 1):
            self.ops_table.insert("", tk.END, values=(i, it.kind, it.line, it.code))

        self.metrics_var.set(
            f"Абсолютная сложность CL = {r.CL}\n"
            f"Количество операторов N = {r.operators}\n"
            f"Относительная сложность cl = CL / N = {r.CL} / {r.operators} = {r.cl:.3f}\n"
            f"Максимальный уровень вложенности CLI = {r.cli}"
        )
        self.status_var.set(f"Анализ завершён: CL={r.CL}, N={r.operators}, cl={r.cl:.3f}, CLI={r.cli}.")

    def clear(self) -> None:
        self.code_text.delete("1.0", tk.END)
        self.table.delete(*self.table.get_children())
        self.ops_table.delete(*self.ops_table.get_children())
        self.metrics_var.set("Метрики будут показаны после анализа.")
        self.status_var.set("Вставьте Kotlin-код слева и нажмите «Рассчитать метрики».")


if __name__ == "__main__":
    JilbApp().mainloop()
