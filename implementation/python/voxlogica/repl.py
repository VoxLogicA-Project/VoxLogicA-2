from voxlogica.parser import parse_program
from voxlogica.execution import ExecutionEngine
from voxlogica.reducer import _reduce_program_internal, Environment
# from voxlogica.storage import NoCacheStorageBackend

bindings = {}

def merge_dags(dag1, dag2):
    if dag1 is not None:
        for node in dag2.nodes:
            if node not in dag1.nodes:
                dag1.nodes[node] = dag2.nodes[node]
        dag1.goals = dag2.goals
        return dag1
    return dag2

def merge_bindings(binds1, binds2):
    if binds1 is not None:
        binds1.update(binds2)
        return binds1
    return binds2

class Repl():
    def __init__(self):
        self.execution_engine = ExecutionEngine()
        self.prepared = None

    def do_exit(self):
        """Exit the REPL."""
        print("Exiting...")
        return True

    def do_eval(self, arg):
        """Evaluate a VoxLogicA command."""
        global bindings
        try:
            ast = parse_program(arg)
            # print(ast)
            # print("parsed")
            workplan, binds = _reduce_program_internal(ast, Environment(bindings), collect_bindings=True)
            # print("reduced")
            self.prepared = merge_dags(self.prepared, workplan)
            bindings = merge_bindings(bindings, binds)
            # print("merged")
            # print(bindings)
            # print(self.prepared.nodes)
            result = self.execution_engine.execute_workplan(self.prepared)
            print(f"Result: {result}")
        except Exception as e:
            print(f"Error: {e}")

    def cmdloop(self):
        """Start the REPL loop."""
        print("Welcome to the VoxLogicA REPL! Type 'exit' to quit.")
        while True:
            try:
                user_input = input(">>> ")
                if user_input.strip() == "exit":
                    if self.do_exit():
                        break
                else:
                    self.do_eval(user_input)
            except KeyboardInterrupt:
                print("\nKeyboard interrupt received. Exiting...")
                break

def start_repl():
    repl = Repl()
    repl.cmdloop()