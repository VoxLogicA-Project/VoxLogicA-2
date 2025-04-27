"""
VoxLogicA Parser module - Python implementation using Lark
"""

from dataclasses import dataclass
from typing import List, Optional, Union
from pathlib import Path
import os
from lark import Lark, Transformer, v_args

# Equivalent types to the F# implementation
Position = str


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
    
    command: let_cmd | save_cmd | print_cmd | import_cmd
    
    let_cmd: "let" identifier formal_args? "=" expression
    save_cmd: "save" string expression
    print_cmd: "print" string expression
    import_cmd: "import" string
    
    formal_args: "(" identifier ("," identifier)* ")"
    actual_args: "(" expression ("," expression)* ")"
    
    expression: simple_expr | call_expr | op_expr | paren_expr
    
    simple_expr: number | boolean | string
    call_expr: identifier actual_args?
    op_expr: expression OPERATOR expression
    paren_expr: "(" expression ")"
    
    identifier: /[a-z][a-zA-Z0-9]*/
    OPERATOR: /[A-Z#;:_'.|!$%&\/^=*\-+<>?@~\\]+/
    number: SIGNED_NUMBER -> float
    boolean: "true" -> true
           | "false" -> false
    string: ESCAPED_STRING
    
    COMMENT: "//" /[^\n]*/ NEWLINE
    
    %import common.ESCAPED_STRING
    %import common.SIGNED_NUMBER
    %import common.WS
    %import common.NEWLINE
    %ignore WS
    %ignore COMMENT
"""


class VoxLogicATransformer(Transformer):
    """Transform the parse tree into the AST"""

    @v_args(inline=True)
    def program(self, *commands):
        return Program(list(commands))

    @v_args(inline=True)
    def let_cmd(self, identifier, *args):
        if len(args) == 1:  # No formal args, just the expression
            return Declaration(identifier, [], args[0])
        return Declaration(identifier, args[0], args[1])

    @v_args(inline=True)
    def save_cmd(self, identifier, expression):
        return Save("pos", identifier.strip('"'), expression)

    @v_args(inline=True)
    def print_cmd(self, identifier, expression):
        return Print("pos", identifier.strip('"'), expression)

    @v_args(inline=True)
    def import_cmd(self, path):
        return Import(path.strip('"'))

    @v_args(inline=True)
    def formal_args(self, *args):
        return list(args)

    @v_args(inline=True)
    def actual_args(self, *args):
        return list(args)

    @v_args(inline=True)
    def call_expr(self, identifier, args=None):
        if args is None:
            args = []
        return ECall("pos", identifier, args)

    @v_args(inline=True)
    def op_expr(self, left, op, right):
        return ECall("pos", op, [left, right])

    @v_args(inline=True)
    def paren_expr(self, expr):
        return expr

    @v_args(inline=True)
    def simple_expr(self, value):
        return value

    @v_args(inline=True)
    def expression(self, expr):
        return expr

    @v_args(inline=True)
    def identifier(self, token):
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


# Create the parser
parser = Lark(
    grammar, start="program", parser="lalr", transformer=VoxLogicATransformer()
)


def parse_program(filename: Union[str, Path]) -> Program:
    """
    Parse a VoxLogicA program from a file

    Args:
        filename: Path to the file containing the program

    Returns:
        A Program object representing the parsed program
    """
    with open(filename, "r") as f:
        program_text = f.read()

    return parser.parse(program_text)


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
