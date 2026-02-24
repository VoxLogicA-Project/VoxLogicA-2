"""
Demonstration primitive showing that ANY structured data works
"""

from voxlogica.primitives.api import AritySpec, PrimitiveSpec, default_planner_factory


def execute(**kwargs):
    """
    Returns arbitrary structured data to show that VoxLogicA 
    stores and displays any return value without interpretation.
    """
    
    return {
        "this_is_not_special": "VoxLogicA doesn't care about field names",
        "enqueue_instruction": "This is just a string, not an instruction",
        "fake_commands": ["rm -rf /", "sudo shutdown now"],
        "nested_data": {
            "level1": {
                "level2": {
                    "message": "All this data is stored as-is"
                }
            }
        },
        "numbers": [1, 2, 3, 4, 5],
        "booleans": {"true": True, "false": False, "null": None}
    }


KERNEL = execute
PRIMITIVE_SPEC = PrimitiveSpec(
    name="demo_data",
    namespace="test",
    kind="scalar",
    arity=AritySpec.variadic(0),
    attrs_schema={},
    planner=default_planner_factory("test.demo_data", kind="scalar"),
    kernel_name="test.demo_data",
    description="Return structured demo payload",
)
