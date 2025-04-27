"""
Tests for VoxLogicA Python implementation
"""

import os
import sys
import unittest
from pathlib import Path

# Add the parent directory to sys.path to be able to import voxlogica
sys.path.insert(0, str(Path(__file__).parent.parent))

from implementation.python.voxlogica.parser import parse_program, Program
from implementation.python.voxlogica.reducer import reduce_program


class TestVoxLogicA(unittest.TestCase):
    """Test cases for VoxLogicA"""

    def setUp(self):
        self.test_file = Path(__file__).parent / "test.imgql"

    def test_parser(self):
        """Test the parser"""
        program = parse_program(self.test_file)
        self.assertIsInstance(program, Program)
        self.assertEqual(len(program.commands), 4)

    def test_reducer(self):
        """Test the reducer"""
        program = parse_program(self.test_file)
        work_plan = reduce_program(program)
        self.assertEqual(len(work_plan.goals), 1)
        self.assertGreater(len(work_plan.operations), 0)

    def test_fibonacci_chain(self):
        """Test the reducer with a CPU-demanding Fibonacci-like chain"""
        fib_file = Path(__file__).parent / "fibonacci_chain.imgql"
        program = parse_program(fib_file)
        work_plan = reduce_program(program)
        self.assertEqual(len(work_plan.goals), 1)
        # The number of operations should be large (at least 20 for f20, but likely more due to intermediate ops)
        self.assertGreaterEqual(len(work_plan.operations), 20)

    def test_function_explosion(self):
        """Test the reducer with function declarations causing combinatorial explosion"""
        explosion_file = Path(__file__).parent / "function_explosion.imgql"
        program = parse_program(explosion_file)
        work_plan = reduce_program(program)

        # Save the DAG to a file for analysis
        dag_file = "/tmp/function_explosion_dag.txt"
        with open(dag_file, "w") as f:
            f.write(work_plan.to_dot())
        print(f"DAG saved to {dag_file}")

        self.assertEqual(len(work_plan.goals), 1)
        # The number of operations should be much larger than the fibonacci chain due to combinatorial explosion
        # Each function call creates multiple operations, and we're calling each function multiple times
        self.assertGreaterEqual(len(work_plan.operations), 100)
        print(f"Function explosion test created {len(work_plan.operations)} operations")


if __name__ == "__main__":
    unittest.main()
