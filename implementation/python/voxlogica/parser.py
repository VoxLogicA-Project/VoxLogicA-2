"""Parser for the VoxLogicA surface language.

This module defines the AST dataclasses, the Lark grammar used to parse source
text, and the transformer that turns parse trees into reducer-friendly syntax
objects. It is intentionally syntax-only: name resolution and execution happen
in later stages.
"""

from dataclasses import dataclass
from typing import List, Union
from pathlib import Path
from lark import Lark, Transformer, v_args, Tree
from lark.exceptions import UnexpectedInput

Position = str


def format_position(source_name: str, meta: object | None) -> Position:
    """Format a Lark meta object into a stable source location string."""
    if meta is None:
        return source_name
    line = getattr(meta, "line", None)
    column = getattr(meta, "column", None)
    if line is None or column is None:
        return source_name
    return f"{source_name}:{line}:{column}"


@dataclass
class ProgramParseError(ValueError):
    """Structured parse error with source location details."""

    source_name: str
    line: int
    column: int
    expected: list[str]
    found: str | None = None
    line_text: str | None = None

    def to_clickable_line(self) -> str:
        return f"{self.source_name}:{self.line}:{self.column}: error: {self._message()}"

    def _message(self) -> str:
        found = "end of input" if self.found in (None, "", "''") else self.found
        if self.expected:
            expected = ", ".join(sorted(self.expected))
            return f"unexpected token {found}; expected one of: {expected}"
        return f"unexpected token {found}"

    def format_block(self) -> str:
        header = self.to_clickable_line()
        if not self.line_text:
            return header
        caret_padding = " " * max(self.column - 1, 0)
        return "\n".join([header, self.line_text, f"{caret_padding}^"])

    def __str__(self) -> str:
        return self.format_block()


@dataclass
class Expression:
    """Base class for expressions in the VoxLogicA language"""

    @staticmethod
    def create_call(
        position: Position, identifier: str, args: List["Expression"]
    ) -> "ECall":
        return ECall(position, identifier, args)

    @staticmethod
    def create_number(value: float) -> "ENumber":
        return ENumber(value)

    @staticmethod
    def create_bool(value: bool) -> "EBool":
        return EBool(value)

    @staticmethod
    def create_string(value: str) -> "EString":
        return EString(value)

    @staticmethod
    def create_array(items: List["Expression"]) -> "EArray":
        return EArray(items)

    def to_syntax(self) -> str:
        """Convert the expression to syntax form"""
        raise NotImplementedError("Must be implemented by subclasses")


@dataclass
class ECall(Expression):
    """Function call expression"""

    position: Position
    identifier: str
    arguments: List[Expression]

    def __str__(self) -> str:
        if not self.arguments:
            return f"{self.identifier}"
        arg_str = [str(arg) for arg in self.arguments]
        return f"{self.identifier}({arg_str})"

    def to_syntax(self) -> str:
        if not self.arguments:
            return f"{self.identifier}"
        arg_str = ",".join([arg.to_syntax() for arg in self.arguments])
        return f"{self.identifier}({arg_str})"


@dataclass
class ENumber(Expression):
    """Numeric literal expression"""

    value: float

    def __str__(self) -> str:
        return f"{self.value}"

    def to_syntax(self) -> str:
        return f"{self.value}"


@dataclass
class EBool(Expression):
    """Boolean literal expression"""

    value: bool

    def __str__(self) -> str:
        return f"{self.value}".lower()

    def to_syntax(self) -> str:
        return f"{self.value}".lower()


@dataclass
class EString(Expression):
    """String literal expression"""

    value: str

    def __str__(self) -> str:
        return self.value

    def to_syntax(self) -> str:
        return f'"{self.value}"'


@dataclass
class EArray(Expression):
    """Array literal expression."""

    items: List[Expression]

    def __str__(self) -> str:
        return f"[{', '.join(str(item) for item in self.items)}]"

    def to_syntax(self) -> str:
        return f"[{','.join(item.to_syntax() for item in self.items)}]"


@dataclass
class ESlice(Expression):
    """Slice expression using bracket syntax."""

    sequence: Expression
    start: Expression | None
    stop: Expression | None

    def __str__(self) -> str:
        start = "" if self.start is None else str(self.start)
        stop = "" if self.stop is None else str(self.stop)
        return f"{self.sequence}[{start}:{stop}]"

    def to_syntax(self) -> str:
        start = "" if self.start is None else self.start.to_syntax()
        stop = "" if self.stop is None else self.stop.to_syntax()
        return f"{self.sequence.to_syntax()}[{start}:{stop}]"


@dataclass
class EFor(Expression):
    """For loop expression"""

    position: Position
    variable: str
    iterable: Expression
    body: Expression

    def __str__(self) -> str:
        return f"for {self.variable} in {self.iterable} do {self.body}"

    def to_syntax(self) -> str:
        return f"for {self.variable} in {self.iterable.to_syntax()} do {self.body.to_syntax()}"


@dataclass
class EFilter(Expression):
    """Filter expression that keeps items matching a predicate."""

    position: Position
    variable: str
    iterable: Expression
    predicate: Expression

    def __str__(self) -> str:
        return f"filter {self.variable} in {self.iterable} do {self.predicate}"

    def to_syntax(self) -> str:
        return (
            f"filter {self.variable} in {self.iterable.to_syntax()} "
            f"do {self.predicate.to_syntax()}"
        )


@dataclass
class EFold(Expression):
    """Fold expression that reduces a sequence with a built-in combiner."""

    position: Position
    operator: str
    init: Expression | None
    sequence: Expression

    def __str__(self) -> str:
        if self.init is None:
            return f"fold {self.operator} {self.sequence}"
        return f"fold {self.operator} {self.init} {self.sequence}"

    def to_syntax(self) -> str:
        if self.init is None:
            return f"fold {self.operator} {self.sequence.to_syntax()}"
        return (
            f"fold {self.operator} {self.init.to_syntax()} {self.sequence.to_syntax()}"
        )


@dataclass
class ELet(Expression):
    """Let expression for local variable binding"""

    position: Position
    variable: str
    value: Expression
    body: Expression

    def __str__(self) -> str:
        return f"let {self.variable} = {self.value} in {self.body}"

    def to_syntax(self) -> str:
        return f"let {self.variable} = {self.value.to_syntax()} in {self.body.to_syntax()}"


@dataclass
class Command:
    """Base class for commands in the VoxLogicA language"""

    def to_syntax(self) -> str:
        """Convert the command to syntax form"""
        raise NotImplementedError("Must be implemented by subclasses")


@dataclass
class Declaration(Command):
    """Variable or function declaration"""

    identifier: str
    arguments: List[str]
    expression: Expression

    def to_syntax(self) -> str:
        if not self.arguments:
            return f"let {self.identifier}={self.expression.to_syntax()}"
        args_str = ",".join(self.arguments)
        return f"let {self.identifier}({args_str})={self.expression.to_syntax()}"


@dataclass
class Save(Command):
    """Command to save an expression to a file"""

    position: Position
    identifier: str
    expression: Expression

    def to_syntax(self) -> str:
        return f'save "{self.identifier}" {self.expression.to_syntax()}'


@dataclass
class Print(Command):
    """Command to print an expression"""

    position: Position
    identifier: str
    expression: Expression

    def to_syntax(self) -> str:
        return f'print "{self.identifier}" {self.expression.to_syntax()}'


@dataclass
class Import(Command):
    """Command to import a file"""

    path: str

    def to_syntax(self) -> str:
        return f"import {self.path}"


@dataclass
class Program:
    """A program consisting of a list of commands"""

    commands: List[Command]

    def to_syntax(self) -> str:
        return "\n".join([cmd.to_syntax() for cmd in self.commands])

    def __str__(self) -> str:
        return self.to_syntax()


# Lark grammar for the VoxLogicA language
grammar = r"""
    program: command*
    
    command: let_cmd | assignment_cmd | save_cmd | print_cmd | import_cmd
    
    let_cmd: "let" variable_name formal_args? "=" expression
    assignment_cmd: variable_name formal_args? "=" expression
    save_cmd: "save" string expression
    print_cmd: "print" string expression
    import_cmd: "import" string
    
    formal_args: "(" identifier ("," identifier)* ")"
    actual_args: "(" expression ("," expression)* ")"
    
        ?expression: let_expr | for_expr | filter_expr | fold_expr | op_expr
    
        ?op_expr: prefix_expr
            | op_expr infix_operator prefix_expr   -> op_expr
        ?prefix_expr: postfix_expr
            | prefix_operator prefix_expr      -> prefix_expr
        postfix_expr: primary_expr postfix_access*
        postfix_access: "[" expression "]"        -> postfix_index
                      | "[" expression ":" expression "]" -> postfix_slice_both
                      | "[" expression ":" "]"   -> postfix_slice_from
                      | "[" ":" expression "]"   -> postfix_slice_to
                      | "[" ":" "]"              -> postfix_slice_all
        ?primary_expr: simple_expr | array_expr | call_id_expr | call_op_expr | paren_expr

        simple_expr: number | boolean | string
    call_id_expr: identifier actual_args?
    call_op_expr: OPERATOR actual_args
    paren_expr: "(" expression ")"
        array_expr: "[" [expression ("," expression)*] "]"
    for_expr: "for" identifier "in" expression "do" expression
    filter_expr: "filter" identifier "in" expression "do" expression
    fold_expr: "fold" fold_op expression expression -> fold_with_init
             | "fold" fold_op expression -> fold_default_init
    ?fold_op: OPERATOR | FOLD_MAX | FOLD_MIN

    FOLD_MAX: "max"
    FOLD_MIN: "min"
    let_expr: "let" identifier "=" expression "in" expression
    
    variable_name: identifier | OPERATOR
    infix_operator: OPERATOR
    prefix_operator: OPERATOR
    identifier: IDENTIFIER | UPPER_IDENTIFIER | DOLLAR_IDENTIFIER

    IDENTIFIER: /[a-z_][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?/
    UPPER_IDENTIFIER: /[A-Z][a-zA-Z0-9_]*(\.[a-zA-Z_][a-zA-Z0-9_]*)?/
    DOLLAR_IDENTIFIER.2: /\$[a-z][a-z0-9_]*/
    OPERATOR: /(?!\/{2})(?:[#;:_'\.|!$%&\/^=*\-+<>?@~\\]+|[A-Z][A-Z0-9]*[#;:_'\.|!$%&\/^=*\-+<>?@~\\][A-Z0-9#;:_'\.|!$%&\/^=*\-+<>?@~\\]*)/
    number: SIGNED_NUMBER -> float
    boolean: "true" -> true
           | "false" -> false
    string: ESCAPED_STRING
    
    // Define the comment pattern at the top level
    COMMENT: "//" /[^\n]*/
    
    %import common.ESCAPED_STRING
    %import common.SIGNED_NUMBER
    %import common.WS
    %import common.NEWLINE
    %ignore WS
    %ignore COMMENT
    %ignore NEWLINE
"""


class VoxLogicATransformer(Transformer):
    """Transform the parse tree into the AST."""

    def __init__(self, source_name: str = "<input>") -> None:
        self.source_name = source_name

    def _pos(self, meta: object | None) -> Position:
        return format_position(self.source_name, meta)

    @v_args(inline=True)
    def program(self, *commands):
        # Extract actual command objects from Tree objects if needed
        processed_commands = []
        for cmd in commands:
            if isinstance(cmd, Tree) and cmd.data == "command" and cmd.children:
                # Extract the actual command from the Tree's children
                processed_commands.append(cmd.children[0])
            else:
                processed_commands.append(cmd)
        return Program(processed_commands)

    @v_args(inline=True)
    def command(self, cmd):
        # Just return the command directly
        return cmd

    @v_args(inline=True)
    def let_cmd(self, variable_name, *args):
        if len(args) == 1:  # No formal args, just the expression
            return Declaration(variable_name, [], args[0])
        return Declaration(variable_name, args[0], args[1])

    @v_args(inline=True)
    def assignment_cmd(self, variable_name, *args):
        if len(args) == 1:  # No formal args, just the expression
            return Declaration(variable_name, [], args[0])
        return Declaration(variable_name, args[0], args[1])

    @v_args(inline=True)
    def variable_name(self, name):
        return str(name)

    @v_args(meta=True, inline=True)
    def save_cmd(self, meta, identifier, expression):
        return Save(self._pos(meta), identifier.value, expression)

    @v_args(meta=True, inline=True)
    def print_cmd(self, meta, identifier, expression):
        return Print(self._pos(meta), identifier.value, expression)

    @v_args(inline=True)
    def import_cmd(self, path):
        return Import(path.value)

    @v_args(inline=True)
    def formal_args(self, *args):
        return list(args)

    @v_args(inline=True)
    def actual_args(self, *args):
        return list(args)

    @v_args(meta=True, inline=True)
    def call_id_expr(self, meta, function_name, args=None):
        if args is None:
            args = []
        return ECall(self._pos(meta), function_name, args)

    @v_args(meta=True, inline=True)
    def call_op_expr(self, meta, function_name, args):
        return ECall(self._pos(meta), str(function_name), args)

    @v_args(meta=True, inline=True)
    def postfix_expr(self, meta, expr, *indices):
        current = expr
        position = self._pos(meta)
        for index in indices:
            kind = index[0]
            if kind == "index":
                current = ECall(position, "index", [current, index[1]])
                continue
            current = ESlice(current, index[1], index[2])
        return current

    @v_args(inline=True)
    def postfix_index(self, expr):
        return ("index", expr)

    def postfix_slice_both(self, items):
        return ("slice", items[0], items[1])

    def postfix_slice_from(self, items):
        return ("slice", items[0], None)

    def postfix_slice_to(self, items):
        return ("slice", None, items[0])

    def postfix_slice_all(self, _items):
        return ("slice", None, None)

    @v_args(meta=True, inline=True)
    def op_expr(self, meta, left, op, right):
        return ECall(self._pos(meta), op, [left, right])

    @v_args(meta=True, inline=True)
    def prefix_expr(self, meta, op, expr):
        return ECall(self._pos(meta), op, [expr])

    @v_args(inline=True)
    def infix_operator(self, op):
        return str(op)

    @v_args(inline=True)
    def prefix_operator(self, op):
        return str(op)

    @v_args(inline=True)
    def paren_expr(self, expr):
        return expr

    @v_args(inline=True)
    def array_expr(self, *items):
        return EArray(list(items))

    @v_args(meta=True, inline=True)
    def for_expr(self, meta, variable, iterable, body):
        return EFor(self._pos(meta), str(variable), iterable, body)

    @v_args(meta=True, inline=True)
    def filter_expr(self, meta, variable, iterable, predicate):
        return EFilter(self._pos(meta), str(variable), iterable, predicate)

    @v_args(meta=True, inline=True)
    def fold_with_init(self, meta, operator, init, sequence):
        return EFold(self._pos(meta), str(operator), init, sequence)

    @v_args(meta=True, inline=True)
    def fold_default_init(self, meta, operator, sequence):
        return EFold(self._pos(meta), str(operator), None, sequence)

    @v_args(meta=True, inline=True)
    def let_expr(self, meta, variable, value, body):
        return ELet(self._pos(meta), str(variable), value, body)

    @v_args(inline=True)
    def simple_expr(self, value):
        return value

    @v_args(inline=True)
    def expression(self, expr):
        return expr

    @v_args(inline=True)
    def identifier(self, identifier):
        return identifier

    @v_args(inline=True)
    def IDENTIFIER(self, token):
        return str(token)

    @v_args(inline=True)
    def UPPER_IDENTIFIER(self, token):
        return str(token)

    @v_args(inline=True)
    def DOLLAR_IDENTIFIER(self, token):
        return str(token)

    @v_args(inline=True)
    def OPERATOR(self, token):
        return str(token)

    @v_args(inline=True)
    def float(self, token):
        return ENumber(float(token))

    @v_args(inline=True)
    def true(self):
        return EBool(True)

    @v_args(inline=True)
    def false(self):
        return EBool(False)

    @v_args(inline=True)
    def string(self, token):
        # Remove the quotes from the string
        return EString(token[1:-1])


# Base parser without a transformer; attach one per parse for source locations.
_base_parser = Lark(
    grammar,
    start="program",
    parser="lalr",
    propagate_positions=True,
    maybe_placeholders=False,
)


def parse_program(filename: Union[str, Path]) -> Program:
    """
    Parse a VoxLogicA program from a file

    Args:
        filename: Path to the file containing the program

    Returns:
        A Program object representing the parsed program
    """
    path = Path(filename) if not isinstance(filename, Path) else filename
    program_text = path.read_text(encoding="utf-8")
    return parse_program_content(program_text, source_name=str(path))


def parse_import(filename: Union[str, Path]) -> List[Command]:
    """
    Parse a VoxLogicA import file

    Args:
        filename: Path to the file to import

    Returns:
        A list of commands from the imported file
    """
    program = parse_program(filename)
    return program.commands


def parse_program_content(content: str, source_name: str = "<input>") -> Program:
    """
    Parse a VoxLogicA program from content string

    Args:
        content: String containing the program text

    Returns:
        A Program object representing the parsed program
    """
    # Attach a transformer with the requested source name for location strings.
    try:
        tree = _base_parser.parse(content)
        result = VoxLogicATransformer(source_name=source_name).transform(tree)
    except UnexpectedInput as exc:
        found = getattr(exc, "token", None)
        found_type = getattr(found, "type", None)
        found_value = getattr(found, "value", None)
        if found_type == "$END":
            found_str = "end of input"
        elif found_value not in (None, ""):
            found_str = repr(found_value)
        else:
            found_str = None if found is None else str(found)
        expected = list(getattr(exc, "expected", []) or [])
        line_text = None
        if exc.line is not None:
            source_lines = content.splitlines()
            if 1 <= exc.line <= len(source_lines):
                line_text = source_lines[exc.line - 1]
        raise ProgramParseError(
            source_name=source_name,
            line=int(exc.line),
            column=int(exc.column),
            expected=expected,
            found=found_str,
            line_text=line_text,
        ) from None

    # Ensure we got a Program object
    if not isinstance(result, Program):
        raise ValueError(f"Expected Program object, got {type(result).__name__}")

    return result


def parse_expression_content(content: str) -> Expression:
    """
    Parse a single VoxLogicA expression from a content string.

    This helper wraps the expression in a synthetic declaration and reuses the
    existing program parser to keep grammar handling centralized.
    """
    wrapped = f"let expr_tmp = {content}"
    program = parse_program_content(wrapped)
    if not program.commands:
        raise ValueError("Expected expression content, got empty program")

    first = program.commands[0]
    if not isinstance(first, Declaration):
        raise ValueError("Expected declaration when parsing wrapped expression")

    return first.expression
