# parser.py - VoxLogicA Language Parser

## Purpose

The `parser.py` module implements the VoxLogicA language parser using Lark parsing framework. It transforms VoxLogicA source code into Abstract Syntax Trees (ASTs) that can be processed by the reduction engine.

## Architecture

### Core Components

#### 1. Expression Hierarchy
- **Base Expression Class**: Common interface for all language constructs
- **Primitive Expressions**: Numbers, booleans, strings, identifiers
- **Composite Expressions**: Function calls, let bindings, for loops
- **Control Structures**: Conditionals, loops, and flow control

#### 2. Program Structure
- **Declarations**: Function and variable declarations
- **Commands**: Save, print, import statements
- **Programs**: Complete VoxLogicA programs with multiple statements

#### 3. Parser Infrastructure
- **Lark Integration**: Grammar-based parsing with syntax error handling
- **AST Transformation**: Conversion from parse trees to typed AST nodes
- **Position Tracking**: Source location information for error reporting

### Key Classes and AST Nodes

#### Base Expression Types
```python
@dataclass
class Expression:
    """Base class for all VoxLogicA expressions"""
    
    def to_syntax(self) -> str:
        """Convert expression back to VoxLogicA syntax"""
        raise NotImplementedError()

@dataclass
class ECall(Expression):
    """Function call expression: func(arg1, arg2, ...)"""
    position: Position
    identifier: str
    arguments: List[Expression]

@dataclass
class ENumber(Expression):
    """Numeric literal: 42, 3.14, -7"""
    value: float

@dataclass
class EBool(Expression):
    """Boolean literal: true, false"""
    value: bool

@dataclass
class EString(Expression):
    """String literal: "hello world" """
    value: str
```

#### Control Flow Expressions
```python
@dataclass
class ELet(Expression):
    """Let binding: let x = expr1 in expr2"""
    position: Position
    identifier: str
    value: Expression
    body: Expression

@dataclass
class EFor(Expression):
    """For loop: for x in iterable do body"""
    position: Position
    variable: str
    iterable: Expression
    body: Expression
```

#### Program Structure
```python
@dataclass
class Declaration:
    """Function declaration: def func(params) = body"""
    position: Position
    identifier: str
    parameters: List[str]
    body: Expression

@dataclass
class Save:
    """Save command: save expr as "filename" """
    position: Position
    expression: Expression
    filename: str

@dataclass
class Print:
    """Print command: print expr"""
    position: Position
    expression: Expression

@dataclass
class Program:
    """Complete VoxLogicA program"""
    position: Position
    declarations: List[Declaration]
    commands: List[Command]
```

## Implementation Details

### Grammar Definition

The parser uses a Lark grammar file defining VoxLogicA syntax:

```lark
start: program

program: (declaration | command)*

declaration: "def" CNAME "(" [CNAME ("," CNAME)*] ")" "=" expression

command: save_cmd | print_cmd | import_cmd

save_cmd: "save" expression "as" STRING
print_cmd: "print" expression  
import_cmd: "import" STRING

expression: let_expr
          | for_expr
          | call_expr
          | atom

let_expr: "let" CNAME "=" expression "in" expression
for_expr: "for" CNAME "in" expression "do" expression

call_expr: CNAME "(" [expression ("," expression)*] ")"
         | CNAME

atom: NUMBER
    | BOOLEAN  
    | STRING
    | CNAME

BOOLEAN: "true" | "false"
NUMBER: /-?\d+(\.\d+)?/
STRING: /"[^"]*"/
CNAME: /[a-zA-Z_][a-zA-Z0-9_]*/
```

### AST Transformation

The parser uses a Lark Transformer to convert parse trees to typed AST nodes:

```python
class VoxLogicATransformer(Transformer):
    
    @v_args(inline=True)
    def call_expr(self, name, *args):
        return ECall("", name, list(args))
    
    @v_args(inline=True) 
    def let_expr(self, var, value, body):
        return ELet("", var, value, body)
    
    @v_args(inline=True)
    def for_expr(self, var, iterable, body):
        return EFor("", var, iterable, body)
    
    def number(self, value):
        return ENumber(float(value))
    
    def boolean(self, value):
        return EBool(value == "true")
    
    def string(self, value):
        return EString(value[1:-1])  # Remove quotes
```

### Error Handling

The parser provides comprehensive error handling:

```python
def parse_program_content(content: str) -> Program:
    """Parse VoxLogicA source code with error handling."""
    try:
        tree = parser.parse(content)
        return transformer.transform(tree)
    except LarkError as e:
        raise ParseError(f"Syntax error: {e}")
    except Exception as e:
        raise ParseError(f"Parse error: {e}")
```

## Dependencies

### Internal Dependencies
- No internal VoxLogicA dependencies (parser is self-contained)

### External Dependencies
- `lark` - Parsing framework and grammar engine
- `dataclasses` - AST node definitions
- `typing` - Type annotations
- `pathlib` - File path handling

## Usage Examples

### Basic Expression Parsing
```python
from voxlogica.parser import parse_expression

# Parse simple expressions
expr1 = parse_expression("42")
# Returns: ENumber(value=42.0)

expr2 = parse_expression("add(x, y)")  
# Returns: ECall(position="", identifier="add", arguments=[...])

expr3 = parse_expression("let x = 5 in multiply(x, 2)")
# Returns: ELet(position="", identifier="x", value=ENumber(5.0), body=...)
```

### Program Parsing
```python
from voxlogica.parser import parse_program

program_source = '''
def square(x) = multiply(x, x)
def area(w, h) = multiply(w, h)

save square(5) as "result.txt"
print area(3, 4)
'''

program = parse_program(program_source)
print(f"Parsed {len(program.declarations)} declarations")
print(f"Parsed {len(program.commands)} commands")
```

### Import Handling
```python
from voxlogica.parser import parse_import

# Parse import statements
import_stmt = parse_import('import "math_functions.vl"')
print(f"Importing: {import_stmt.filename}")
```

### AST Manipulation
```python
# Create expressions programmatically
x_var = ECall("", "x", [])
y_var = ECall("", "y", [])
add_expr = ECall("", "add", [x_var, y_var])

# Convert back to syntax
syntax = add_expr.to_syntax()  # Returns: "add(x,y)"

# Traverse AST
def count_function_calls(expr: Expression) -> int:
    if isinstance(expr, ECall):
        return 1 + sum(count_function_calls(arg) for arg in expr.arguments)
    elif isinstance(expr, ELet):
        return count_function_calls(expr.value) + count_function_calls(expr.body)
    elif isinstance(expr, EFor):
        return count_function_calls(expr.iterable) + count_function_calls(expr.body)
    else:
        return 0
```

## Performance Considerations

### Parser Performance
- **Grammar Optimization**: Efficiently structured grammar for fast parsing
- **Incremental Parsing**: Support for parsing code fragments
- **Memory Efficiency**: Minimal AST node overhead

### Error Recovery
- **Syntax Error Reporting**: Clear error messages with location information
- **Partial Parsing**: Ability to parse valid portions of invalid programs
- **Error Suggestions**: Hints for common syntax mistakes

### Scalability Features
- **Streaming Support**: Can parse large programs without loading entire source
- **Parallel Parsing**: Independent modules can be parsed concurrently
- **Caching**: Parsed ASTs can be cached for repeated use

## AST Analysis and Utilities

### Expression Visitors
```python
class ExpressionVisitor:
    """Base class for AST traversal"""
    
    def visit(self, expr: Expression) -> Any:
        method_name = f"visit_{type(expr).__name__}"
        method = getattr(self, method_name, self.generic_visit)
        return method(expr)
    
    def generic_visit(self, expr: Expression) -> Any:
        pass
    
    def visit_ECall(self, expr: ECall) -> Any:
        for arg in expr.arguments:
            self.visit(arg)
    
    def visit_ELet(self, expr: ELet) -> Any:
        self.visit(expr.value)
        self.visit(expr.body)
```

### Syntax Reconstruction
```python
def ast_to_syntax(expr: Expression) -> str:
    """Convert AST back to valid VoxLogicA syntax"""
    return expr.to_syntax()

def pretty_print_ast(expr: Expression, indent: int = 0) -> str:
    """Generate human-readable AST representation"""
    spaces = "  " * indent
    if isinstance(expr, ECall):
        args = [pretty_print_ast(arg, indent + 1) for arg in expr.arguments]
        return f"{spaces}Call({expr.identifier}, [\n" + ",\n".join(args) + f"\n{spaces}])"
    elif isinstance(expr, ENumber):
        return f"{spaces}Number({expr.value})"
    # ... other cases
```

### Variable Analysis
```python
def find_free_variables(expr: Expression, bound_vars: Set[str] = None) -> Set[str]:
    """Find unbound variables in expression"""
    if bound_vars is None:
        bound_vars = set()
    
    if isinstance(expr, ECall):
        if expr.identifier not in bound_vars:
            free_vars = {expr.identifier}
        else:
            free_vars = set()
        
        for arg in expr.arguments:
            free_vars.update(find_free_variables(arg, bound_vars))
        return free_vars
    
    elif isinstance(expr, ELet):
        value_vars = find_free_variables(expr.value, bound_vars)
        body_vars = find_free_variables(expr.body, bound_vars | {expr.identifier})
        return value_vars | body_vars
    
    # ... other cases
```

## Integration Points

### With Reducer
The parser generates ASTs consumed by the reducer:

```python
# Parse program source
program = parse_program(source_code)

# Compile to workplan
reducer = Reducer()
workplan = reducer.compile_program(program)
```

### With Error Reporting
Position information enables detailed error messages:

```python
def report_type_error(expr: ECall, expected_type: str, actual_type: str):
    return f"Type error at {expr.position}: expected {expected_type}, got {actual_type}"
```

### With IDE Integration
AST structure supports IDE features:

```python
# Syntax highlighting
def get_token_types(program: Program) -> List[Tuple[Position, str]]:
    """Extract tokens for syntax highlighting"""
    
# Code completion
def get_completion_suggestions(expr: Expression, cursor_pos: Position) -> List[str]:
    """Generate code completion suggestions"""
    
# Refactoring support
def rename_identifier(program: Program, old_name: str, new_name: str) -> Program:
    """Rename all occurrences of an identifier"""
```
