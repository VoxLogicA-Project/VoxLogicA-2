"""
Demonstration primitive showing that ANY structured data works
"""

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
