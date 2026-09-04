from dataclasses import dataclass
from voxlogica.analysis.types import VoxType
from voxlogica.analysis.type_helpers import infer_literal_type

@dataclass
class TypeChecker:
    """A type checker for Voxlogica expressions."""

    def check(self, prepared: PreparedPlan) -> VoxType:
        """Check the types of a prepared plan and return the resulting type."""
        target_goals = [goal.id for goal in inprepared.plan.goals]

        for goal in target_goals:
            check_node(goal, prepared)

    def check_node(self, nodeid: NodeId, prepared: PreparedPlan) -> VoxType:
        """Check the type of a single node in the prepared plan."""
        node = prepared.plan.nodes[nodeid]
        
        if node.kind == "constant":
            return infer_literal_type(node.attrs["value"])

        if node.kind == "closure":
            arg_type = self.check_node(prepared.plan.nodes[node.attrs["arg"]], prepared)
            return_type = self.check_node(prepared.plan.nodes[node.attrs["body"]], prepared)
            return VoxClosure(arg_type, return_type)

        if node.kind == "primitive":
            type_rule = load_type(node.attrs["primitive_name"])
            return_type = type_rule([self.check_node(prepared.plan.nodes[arg], prepared) for arg in node.args])
            return return_type