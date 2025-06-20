"""
VoxLogicA Parser module - Python implementation using Lark
"""

from dataclasses import dataclass
from typing import List, Union
from pathlib import Path
from lark import Lark, Transformer, v_args, Tree

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
    
    let_cmd: "let" variable_name formal_args? "=" expression
    save_cmd: "save" string expression
    print_cmd: "print" string expression
    import_cmd: "import" string
    
    formal_args: "(" identifier ("," identifier)* ")"
    actual_args: "(" expression ("," expression)* ")"
    
    expression: simple_expr | call_expr | op_expr | paren_expr
    
    simple_expr: number | boolean | string
    call_expr: function_name actual_args?
    op_expr: expression OPERATOR expression
    paren_expr: "(" expression ")"
    
    function_name: identifier | OPERATOR
    variable_name: identifier | OPERATOR
    identifier: qualified_identifier | simple_identifier
    qualified_identifier: simple_identifier "." simple_identifier
    simple_identifier: /[a-zA-Z_][a-zA-Z0-9_]*/
    
    // Make the OPERATOR pattern more specific to exclude "//" sequence and "."
    // Removed "." from operator pattern to avoid conflicts with qualified identifiers
    OPERATOR: /(?!\/{2})[A-Z#;:_'|!$%&\/^=*\-+<>?@~\\]+/
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
    """Transform the parse tree into the AST"""

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
    def variable_name(self, name):
        return str(name)

    @v_args(inline=True)
    def save_cmd(self, identifier, expression):
        return Save("pos", identifier.value, expression)

    @v_args(inline=True)
    def print_cmd(self, identifier, expression):
        return Print("pos", identifier.value, expression)

    @v_args(inline=True)
    def import_cmd(self, path):
        return Import(path.value)

    @v_args(inline=True)
    def formal_args(self, *args):
        return list(args)

    @v_args(inline=True)
    def actual_args(self, *args):
        return list(args)

    @v_args(inline=True)
    def call_expr(self, function_name, args=None):
        if args is None:
            args = []
        return ECall("pos", function_name, args)

    @v_args(inline=True)
    def function_name(self, name):
        return str(name)

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
    def identifier(self, identifier):
        return identifier

    @v_args(inline=True)
    def qualified_identifier(self, namespace, primitive):
        return f"{namespace}.{primitive}"

    @v_args(inline=True)
    def simple_identifier(self, token):
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
    grammar,
    start="program",
    parser="lalr",
    transformer=VoxLogicATransformer(),
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
    with open(filename, "r") as f:
        program_text = f.read()

    # First parse without transformation to get the tree
    parser_no_transform = Lark(
        grammar, start="program", parser="lalr", propagate_positions=True
    )
    parse_tree = parser_no_transform.parse(program_text)

    # Then transform the tree
    transformer = VoxLogicATransformer()
    result = transformer.transform(parse_tree)

    # Ensure we got a Program object
    if not isinstance(result, Program):
        raise ValueError(f"Expected Program object, got {type(result).__name__}")

    return result


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


def parse_program_content(content: str) -> Program:
    """
    Parse a VoxLogicA program from content string

    Args:
        content: String containing the program text

    Returns:
        A Program object representing the parsed program
    """
    # First parse without transformation to get the tree
    parser_no_transform = Lark(
        grammar, start="program", parser="lalr", propagate_positions=True
    )
    parse_tree = parser_no_transform.parse(content)

    # Then transform the tree
    parser = Lark(grammar, start="program", parser="lalr", transformer=VoxLogicATransformer())
    result = parser.parse(content)

    # Ensure we got a Program object
    if not isinstance(result, Program):
        raise ValueError(f"Expected Program object, got {type(result).__name__}")

    return result
