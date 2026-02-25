# Dask Dashboard for Real-Time Task Execution Debugging

## Overview

VoxLogicA now supports enabling the Dask web dashboard for real-time task execution debugging. This feature allows developers to monitor and analyze the execution of VoxLogicA programs in real-time, providing valuable insights into task scheduling, resource utilization, and performance bottlenecks.

## Enabling the Dashboard

### Command Line Interface

To enable the Dask dashboard when running VoxLogicA programs, use the `--dask-dashboard` flag:

```bash
voxlogica run program.imgql --dask-dashboard
```

When enabled, you will see a message like:
```
Dask dashboard enabled at: http://localhost:8787
```

### Dashboard URL

The dashboard is accessible at: **http://localhost:8787**

This URL provides access to the web-based dashboard with real-time monitoring capabilities.

## Dashboard Features

The Dask dashboard provides several key views for monitoring VoxLogicA execution:

### 1. Task Stream
- **URL**: http://localhost:8787/tasks
- **Purpose**: Shows real-time task execution timeline
- **Useful for**: Understanding task dependencies, execution order, and bottlenecks

### 2. Resource Monitoring
- **URL**: http://localhost:8787/system
- **Purpose**: Displays CPU, memory, and network usage
- **Useful for**: Identifying resource constraints and memory issues

### 3. Progress Tracking
- **URL**: http://localhost:8787/progress
- **Purpose**: Shows task completion progress
- **Useful for**: Monitoring long-running computations

### 4. Memory Usage
- **URL**: http://localhost:8787/memory
- **Purpose**: Detailed memory usage by task and data
- **Useful for**: Debugging memory leaks and "unmanaged memory" warnings

### 5. Workers
- **URL**: http://localhost:8787/workers
- **Purpose**: Shows worker status and task distribution
- **Useful for**: Understanding thread utilization in VoxLogicA's threaded scheduler

## Understanding VoxLogicA's Dask Usage

### Threaded Scheduler
VoxLogicA uses Dask's threaded scheduler with the following configuration:
- **Process mode**: Threads (not processes) for memory sharing
- **Workers**: 1 worker with 4 threads per worker
- **Memory limit**: 2GB per worker
- **Dashboard**: Disabled by default, enabled with `--dask-dashboard`

### Task Types in the Dashboard

When viewing the dashboard, you'll see different types of tasks:

#### 1. Pure Operations
- **Appearance**: Standard Dask tasks with predictable dependencies
- **Examples**: Arithmetic operations, data transformations, file I/O
- **Behavior**: Handled entirely by Dask's scheduler

#### 2. dask_map Operations
- **Appearance**: Tasks with closure-based dependencies
- **Examples**: `for` loops, `map` operations with closures
- **Behavior**: Pre-executed outside Dask graph, then passed to Dask

#### 3. Goal Computations
- **Appearance**: Final result tasks
- **Examples**: Output operations, final result collection
- **Behavior**: Dependent on all required operations

## Debugging Common Issues

### Memory Warnings
If you see "Unmanaged memory use is high" warnings:

1. **Check Memory Tab**: Monitor which tasks are using the most memory
2. **Look for Patterns**: Identify if specific operations are causing memory buildup
3. **Review Task Dependencies**: Ensure proper cleanup of intermediate results

### Performance Bottlenecks
To identify performance issues:

1. **Task Stream**: Look for tasks with unusually long execution times
2. **Resource Monitor**: Check if CPU or memory are saturated
3. **Progress View**: Identify tasks that are waiting for dependencies

### Concurrency Issues
To understand parallelism:

1. **Workers Tab**: Verify thread utilization
2. **Task Stream**: Check for excessive serialization (tasks running sequentially)
3. **Dependencies**: Ensure proper task dependencies are maintained

## Best Practices for Dashboard Usage

### 1. Enable for Development
- Use `--dask-dashboard` during development and debugging
- Leave disabled in production to reduce overhead

### 2. Monitor Memory Usage
- Pay attention to memory patterns, especially for large datasets
- Use the memory tab to identify memory leaks

### 3. Analyze Task Dependencies
- Review task streams to understand execution order
- Identify opportunities for better parallelization

### 4. Performance Profiling
- Use the dashboard to profile different VoxLogicA programs
- Compare execution patterns between different implementations

## Technical Implementation

### Client Configuration
The dashboard is implemented by modifying the Dask client configuration:

```python
# Dashboard disabled (default)
Client(dashboard_address=None)

# Dashboard enabled
Client(dashboard_address=":8787")
```

### Dynamic Client Recreation
VoxLogicA dynamically recreates the Dask client when the dashboard setting changes:

1. Closes existing client if dashboard setting changes
2. Creates new client with appropriate dashboard configuration
3. Maintains all existing functionality

### Integration with Execution Engine
The dashboard setting is passed through the execution chain:

1. CLI argument `--dask-dashboard`
2. Passed to `handle_run()` in features.py
3. Forwarded to `execute_workplan()`
4. Used in `ExecutionEngine.execute_workplan()`
5. Applied to shared Dask client configuration

## Limitations and Considerations

### 1. Single Client Instance
- Only one dashboard can be active at a time
- Changing dashboard setting recreates the client

### 2. Performance Overhead
- Dashboard adds minimal overhead to execution
- Recommended for development/debugging only

### 3. Port Availability
- Dashboard uses port 8787 by default
- Ensure port is available when enabling dashboard

### 4. Browser Compatibility
- Dashboard requires modern web browser
- Best viewed in Chrome, Firefox, or Safari

## Examples

### Basic Usage
```bash
# Enable dashboard for a simple program
voxlogica run test_simple_for.imgql --dask-dashboard

# Enable dashboard for complex operations
voxlogica run test_simpleitk.imgql --dask-dashboard
```

### Debugging Memory Issues
```bash
# Run with dashboard to monitor memory usage
voxlogica run large_dataset.imgql --dask-dashboard

# Monitor the memory tab at http://localhost:8787/memory
```

### Performance Analysis
```bash
# Compare execution with and without dashboard
voxlogica run computation.imgql                    # Normal execution
voxlogica run computation.imgql --dask-dashboard   # With monitoring
```

## Troubleshooting

### Dashboard Not Accessible
- Verify port 8787 is not blocked by firewall
- Check if another application is using port 8787
- Ensure Bokeh dependency is properly installed

### Poor Performance
- Dashboard adds minimal overhead
- Consider disabling for production runs
- Use dashboard primarily for development and debugging

### Memory Warnings Persist
- Memory warnings are performance indicators, not errors
- Use dashboard to identify memory usage patterns
- Consider implementing memory limits for specific operations

## See Also

- [Dask Documentation](https://docs.dask.org/)
- [Dask Dashboard Documentation](https://docs.dask.org/en/latest/dashboard.html)
- [VoxLogicA GitHub Issues (memory)](https://github.com/VoxLogicA-Project/VoxLogicA-2/issues?q=is%3Aissue+memory)
- [VoxLogicA Execution Engine](../../../implementation/python/voxlogica/execution.py)
