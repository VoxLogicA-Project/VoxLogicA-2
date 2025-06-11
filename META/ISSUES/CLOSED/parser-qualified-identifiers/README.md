# Issue: Parser Grammar Support for Qualified Identifiers

## Date
2025-01-27

## Status
- [x] Open
- [ ] Implementation pending

## Description
The current parser grammar does not support qualified identifiers (namespace.primitive syntax) which is required for the namespace-based primitive loading system. The grammar needs to be extended to handle qualified names like `simpleitk.load_sitk_image`.

## Problem Statement
The current grammar defines identifiers as:
```
identifier: /[a-zA-Z_][a-zA-Z0-9_]*/
```

This pattern only matches simple identifiers and cannot parse qualified names with dots. When the parser encounters `simpleitk.load_sitk_image`, it:
1. Parses `simpleitk` as an identifier
2. Encounters the `.` and tries to match it as an OPERATOR
3. Parses `load_sitk_image` as another identifier
4. Fails to resolve the overall expression correctly

## Current Error
```
No primitive implementation for operator: simpleitk
```

This error occurs because the parser treats `simpleitk` as a separate token/operator rather than part of a qualified identifier.

## Requirements
1. Extend the grammar to support qualified identifiers with dot notation
2. Maintain backward compatibility with existing simple identifiers
3. Ensure qualified identifiers work in all contexts (function calls, expressions, etc.)
4. Update the transformer to properly handle qualified identifiers

## Solution Approach
Update the grammar to support qualified identifiers by:
1. Adding a `qualified_identifier` rule that matches `identifier.identifier` patterns
2. Updating the `identifier` rule to support both simple and qualified forms
3. Ensuring the transformer properly handles qualified names

## References
- Related to namespace-based primitive loading system
- Links to: META/ISSUES/OPEN/namespace_dynamic_primitives/
- Test file: test_sitk.imgql

## Status
- [x] **COMPLETED** - Parser grammar now supports qualified identifiers
- [x] Grammar updated to support `namespace.primitive` syntax  
- [x] Transformer updated to handle qualified identifiers
- [x] Test file `test_sitk.imgql` parses and executes successfully
- [x] Qualified identifiers work in all expression contexts

## Solution Implemented
Updated the parser grammar to support qualified identifiers by:
1. Adding a `qualified_identifier` rule that matches `identifier.identifier` patterns
2. Updating the `identifier` rule to support both simple and qualified forms  
3. Adding transformer methods for `qualified_identifier` and `simple_identifier`
4. Ensuring qualified identifiers work in all expression contexts

## Files Modified
- `/Users/vincenzo/data/local/repos/VoxLogicA-2/implementation/python/voxlogica/parser.py` - Grammar and transformer updates

## Acceptance Criteria
- [x] Parser successfully parses qualified identifiers like `simpleitk.load_sitk_image`
- [x] Backward compatibility maintained for simple identifiers
- [x] Test file `test_sitk.imgql` parses without errors
- [x] Qualified identifiers work in all expression contexts
