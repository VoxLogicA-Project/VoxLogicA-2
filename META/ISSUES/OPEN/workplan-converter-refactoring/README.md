# Issue: WorkPlan Converter Refactoring

## Status
**OPEN**

## Description

This issue tracks the refactoring of the `WorkPlan` class from a traditional class with conversion methods to a dataclass with external converter functions. This architectural change improves separation of concerns and follows Python best practices.

## Scope

### Key Changes
- Convert `WorkPlan` from class with methods to dataclass
- Extract conversion logic into separate converter functions
- Create new `voxlogica.converters` package with:
  - `json_converter.py` - JSON conversion functionality
  - `dot_converter.py` - DOT (Graphviz) conversion functionality
- Update usage patterns from `work_plan.to_json()` to `to_json(work_plan)`

### Benefits
- **Separation of Concerns**: WorkPlan focuses on data, converters handle serialization
- **Extensibility**: Easy to add new converters without modifying core WorkPlan class
- **Maintainability**: Clear module boundaries and reduced coupling
- **Testing**: Converters can be unit tested independently

## Files
- `workplan-refactoring.md` - Detailed documentation of the refactoring process and design decisions

## Related Components
- `voxlogica/reducer.py` - Contains WorkPlan class
- `voxlogica/converters/` - New converter package (to be created)
- Test files that use WorkPlan conversion methods

## Implementation Status
- [ ] Create converters package structure
- [ ] Implement JSON converter function
- [ ] Implement DOT converter function  
- [ ] Convert WorkPlan to dataclass
- [ ] Update all usage sites
- [ ] Update tests
- [ ] Update documentation

## Created
2025-06-06

## Last Updated
2025-06-06
